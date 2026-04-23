"""
Utility functions shared across the AI tools discovery system
"""

import json
import logging
import os
import platform
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, NamedTuple, Optional, Tuple

try:
    import pwd
except ImportError:
    pwd = None  # Not available on Windows

from .constants import AUTH_STATUS_TIMEOUT, COMMAND_TIMEOUT, CURSOR_DB_TIMEOUT, CURSOR_PLAN_KEY, DSCL_TIMEOUT, INVALID_SERIAL_VALUES, KEYCHAIN_SERVICE_NAME, KEYCHAIN_TIMEOUT, MACOS_MIN_HUMAN_UID, MACOS_SKIP_USER_DIRS, NON_INTERACTIVE_SHELLS, VERSION_TIMEOUT, WINDOWS_SKIP_USER_DIRS

logger = logging.getLogger(__name__)


def is_valid_serial(serial: str) -> bool:
    """
    Check if serial number is valid (not a placeholder value).
    
    Args:
        serial: Serial number to validate
        
    Returns:
        True if valid, False otherwise
    """
    return serial and serial.upper() not in INVALID_SERIAL_VALUES


def extract_version_number(text: str) -> Optional[str]:
    """
    Extract clean version number from text.
    
    Examples:
        '2.0.37 (Claude Code)' -> '2.0.37'
        'Version: 1.2.3' -> '1.2.3'
    
    Args:
        text: Text containing version information
        
    Returns:
        Version number string or None
    """
    if not text:
        return None

    # Try to extract version pattern (e.g., 2.0.37)
    version_match = re.search(r'(\d+\.\d+\.\d+)', text)
    if version_match:
        return version_match.group(1)

    # Fallback: return first line with digits
    for line in text.split('\n'):
        if any(char.isdigit() for char in line):
            return line.strip()

    return text.strip() if text.strip() else None


def run_command(command: list, timeout: int = COMMAND_TIMEOUT) -> Optional[str]:
    """
    Run a shell command and return its output.
    
    Args:
        command: Command and arguments as list
        timeout: Command timeout in seconds
        
    Returns:
        Command output as string or None if failed
    """
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception as e:
        logger.debug(f"Command {command} failed: {e}")
    return None


def get_hostname() -> str:
    """Get the system hostname."""
    return platform.node()


class DsclBatchData(NamedTuple):
    uid_map: Dict[str, int]
    shell_map: Dict[str, str]
    hidden_set: FrozenSet[str]


def _parse_dscl_list_output(output: Optional[str]) -> Dict[str, str]:
    """Parse ``dscl . -list`` output into {username: value}."""
    if not output:
        return {}
    result: Dict[str, str] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[-1]
    return result


def _fetch_dscl_batch_data() -> DsclBatchData:
    """Fetch UID, shell, and IsHidden data for all users in 3 bulk dscl calls.

    Each query is independently try/excepted — a single failure yields
    an empty map for that attribute while the others remain populated.
    """
    uid_map: Dict[str, int] = {}
    shell_map: Dict[str, str] = {}
    hidden_set: FrozenSet[str] = frozenset()

    try:
        raw = run_command(["dscl", ".", "-list", "/Users", "UniqueID"], timeout=DSCL_TIMEOUT)
        for name, val in _parse_dscl_list_output(raw).items():
            try:
                uid_map[name] = int(val)
            except ValueError:
                pass
    except Exception as exc:
        logger.debug(f"Batch dscl UniqueID query failed: {exc}")

    try:
        raw = run_command(["dscl", ".", "-list", "/Users", "UserShell"], timeout=DSCL_TIMEOUT)
        shell_map = _parse_dscl_list_output(raw)
    except Exception as exc:
        logger.debug(f"Batch dscl UserShell query failed: {exc}")

    try:
        raw = run_command(["dscl", ".", "-list", "/Users", "IsHidden"], timeout=DSCL_TIMEOUT)
        hidden_set = frozenset(
            name for name, val in _parse_dscl_list_output(raw).items() if val == "1"
        )
    except Exception as exc:
        logger.debug(f"Batch dscl IsHidden query failed: {exc}")

    return DsclBatchData(uid_map=uid_map, shell_map=shell_map, hidden_set=hidden_set)


def _is_human_user_macos(username: str, batch_data: DsclBatchData) -> bool:
    """Check if a macOS username is a real human user using batch dscl data.

    Empty maps (from failed batch queries) cause that check to pass through.
    """
    try:
        if batch_data.uid_map and username not in batch_data.uid_map:
            logger.debug(f"Filtering user '{username}': not in uid_map")
            return False
    except Exception as exc:
        logger.debug(f"uid_map lookup failed for '{username}': {exc}")

    try:
        uid = batch_data.uid_map.get(username)
        if uid is not None and uid < MACOS_MIN_HUMAN_UID:
            logger.debug(f"Filtering user '{username}': UID {uid} < {MACOS_MIN_HUMAN_UID}")
            return False
    except Exception as exc:
        logger.debug(f"UID check failed for '{username}': {exc}")

    try:
        shell = batch_data.shell_map.get(username)
        if shell in NON_INTERACTIVE_SHELLS:
            logger.debug(f"Filtering user '{username}': non-interactive shell {shell}")
            return False
    except Exception as exc:
        logger.debug(f"Shell check failed for '{username}': {exc}")

    try:
        if username in batch_data.hidden_set:
            logger.debug(f"Filtering user '{username}': hidden")
            return False
    except Exception as exc:
        logger.debug(f"Hidden check failed for '{username}': {exc}")

    return True


def get_all_users_macos() -> List[str]:
    """
    Get all user directories from /Users on macOS.

    Filters out hidden directories, directories in MACOS_SKIP_USER_DIRS,
    and accounts that fail the _is_human_user_macos checks (service
    accounts, MDM profiles, etc.).

    Returns:
        List of usernames (directory names in /Users)
    """
    users = []
    if platform.system() != "Darwin":
        return users

    users_dir = Path("/Users")
    if not users_dir.exists():
        return users

    batch_data = _fetch_dscl_batch_data()

    try:
        for user_dir in users_dir.iterdir():
            if (user_dir.is_dir()
                    and not user_dir.name.startswith('.')
                    and user_dir.name not in MACOS_SKIP_USER_DIRS
                    and _is_human_user_macos(user_dir.name, batch_data=batch_data)):
                users.append(user_dir.name)
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not list users from /Users: {e}")

    return users


def get_all_users_windows() -> List[str]:
    """
    Get all user directory names from C:\\Users on Windows.

    Filters out hidden directories and well-known system/service
    directories listed in WINDOWS_SKIP_USER_DIRS.

    Returns:
        List of usernames (directory names under C:\\Users), or an
        empty list if not running on Windows or the path does not exist.
    """
    if platform.system() != "Windows":
        return []

    try:
        win_users_dir = Path(Path.home().anchor) / "Users"
        if not win_users_dir.exists():
            return []

        users = []
        for user_dir in win_users_dir.iterdir():
            if (user_dir.is_dir()
                    and not user_dir.name.startswith('.')
                    and user_dir.name not in WINDOWS_SKIP_USER_DIRS):
                users.append(user_dir.name)
        return users
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not list users from Windows Users directory: {e}")
        return []


def get_user_info() -> str:
    """
    Get current user information (whoami equivalent).
    Cross-platform function that returns username.
    Gets username directly from system information, not environment variables.
    
    On macOS, when running as root, finds the user with the most storage space
    in /Users directory to get the actual user instead of "root".
    
    On Windows, when running as administrator, finds the actual logged-in user
    by querying explorer.exe process owner, Win32_ComputerSystem, or active console
    session instead of returning "Administrator" or "admin".
    
    Returns:
        Current username as string
    """
    try:
        username = None
        
        if platform.system() == "Windows":
            # Use whoami command on Windows (works reliably)
            whoami_output = run_command(["whoami"], COMMAND_TIMEOUT)
            # Extract just the username if whoami returns DOMAIN\username format
            if username and "\\" in username:
                username = username.split("\\")[-1]
        else:
            # On macOS/Linux, check if running as root first
            current_user = run_command(["whoami"], COMMAND_TIMEOUT)
            
            # If running as root on macOS, try to find the actual user
            if current_user == "root" and platform.system() == "Darwin":
                # Method 1: Get console user (most direct and reliable)
                username = run_command(["stat", "-f", "%Su", "/dev/console"], COMMAND_TIMEOUT)
                
                # Method 2: Fallback to finding user with most storage space in /Users
                # Command: du -sk /Users/* 2>/dev/null | awk '!/\/Shared$/ {print}' | sort -nr | head -1 | awk -F/ '{print $NF}'
                # Using shell=True to properly handle glob expansion and pipes
                if not username:
                    try:
                        result = subprocess.run(
                            "du -sk /Users/* 2>/dev/null | awk '!/\\/Shared$/ {print}' | sort -nr | head -1 | awk -F/ '{print $NF}'",
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=COMMAND_TIMEOUT
                        )
                        if result.returncode == 0 and result.stdout.strip():
                            username = result.stdout.strip()
                    except Exception as e:
                        logger.debug(f"Failed to get user from storage space: {e}")
            
            # If not root or methods above didn't work, use standard methods
            if not username:
                username = current_user
                if not username:
                    # Fallback to id -un
                    username = run_command(["id", "-un"], COMMAND_TIMEOUT)
        
        # Final fallback to getpass (uses system user database)
        if not username:
            import getpass
            username = getpass.getuser()
        
        return username or "unknown"
    except Exception as e:
        logger.warning(f"Could not extract username: {e}")
        return "unknown"


def resolve_windows_shortcut(shortcut_path: Path) -> Optional[Path]:
    """
    Resolve Windows .lnk shortcut to its target path.
    
    Args:
        shortcut_path: Path to the .lnk file
        
    Returns:
        Target path or None if resolution failed
    """
    try:
        ps_command = (
            f'$shell = New-Object -ComObject WScript.Shell; '
            f'$shortcut = $shell.CreateShortcut({repr(str(shortcut_path))}); '
            f'$shortcut.TargetPath'
        )
        output = run_command(["powershell", "-Command", ps_command], VERSION_TIMEOUT)
        if output and Path(output).exists():
            return Path(output)
    except Exception:
        pass
    return None

def normalize_url(domain: str) -> str:
    """Normalize domain to proper URL format."""
    domain = domain.strip()
    
    if domain.startswith("http://") or domain.startswith("https://"):
        url = domain
    else:
        url = f"https://{domain}"
    
    return url.rstrip('/')

def send_scan_event(
    backend_url: str,
    api_key: str,
    device_id: str,
    run_id: str,
    scan_event: str,
    app_name: Optional[str] = None,
    home_user: Optional[str] = None,
    scan_error: Optional[Dict] = None,
    sentry_context: Optional[Dict] = None
) -> Tuple[bool, bool]:
    """
    Send scan lifecycle event to backend (in_progress, completed, failed).

    Args:
        backend_url: Backend URL to send the event to
        api_key: API key for authentication
        device_id: Device identifier
        run_id: UUID for this scan run (client-generated)
        scan_event: Event type - "in_progress", "completed", or "failed"
        app_name: Optional application name (e.g., JumpCloud)
        home_user: Optional user context (for user-specific failures)
        scan_error: Optional error data (required when scan_event="failed")
        sentry_context: Optional context dict forwarded to Sentry on failure

    Returns:
        Tuple of (success, retryable): success=True if sent, retryable=True if caller should queue
    """
    payload = {
        "device_id": device_id,
        "run_id": run_id,
        "scan_event": scan_event,
    }

    if app_name:
        payload["app_name"] = app_name

    if home_user:
        payload["home_user"] = home_user

    if scan_error:
        payload["scan_error"] = scan_error

    return send_report_to_backend(
        backend_url,
        api_key,
        payload,
        app_name,
        sentry_context
    )


def send_report_to_backend(backend_url: str, api_key: str, report: Dict, app_name: Optional[str] = None, sentry_context: Optional[Dict] = None) -> Tuple[bool, bool]:
    """
    Send discovery report to backend endpoint using curl with retry logic.

    Uses curl subprocess to avoid Zscaler certificate issues with urllib.
    Retries up to 3 times with exponential backoff (2s, 4s) for retryable errors.
    Non-retryable HTTP errors (400, 401, 403, 404, 405, 422) fail immediately.

    Args:
        backend_url: Backend URL to send the report to
        api_key: API key for authentication
        report: Report dictionary to send
        app_name: Optional application name (e.g., JumpCloud) to include in request body
        sentry_context: Optional context dict forwarded to Sentry on failure

    Returns:
        Tuple of (success, retryable): success=True if sent, retryable=True if caller should queue
    """
    NON_RETRYABLE_CODES = (400, 401, 403, 404, 405, 422)
    MAX_ATTEMPTS = 3
    BACKOFF_SECONDS = [2, 4]

    url = f"{normalize_url(backend_url)}/api/v1/ai-tools/report/"
    ctx = sentry_context or {}

    if not api_key or not api_key.strip():
        logger.error("API key is empty or missing. Please provide a valid API key.")
        return (False, False)

    payload = dict(report)
    if app_name:
        payload["app_name"] = app_name

    payload_json = json.dumps(payload)
    ctx = {
        **ctx,
        "payload_size_bytes": len(payload_json),
        "payload_keys": ",".join(sorted(payload.keys())),
    }

    # Write payload to a temp file to avoid OSError when payload exceeds ARG_MAX.
    # The file is written once and reused across retries, then cleaned up in finally.
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="ai-discovery-payload-", suffix=".json")
    except OSError as e:
        logger.error(f"Could not create temp file for payload: {e}")
        report_to_sentry(e, {**ctx, "phase": "send_report_tmpfile"}, level="warning")
        return (False, True)

    try:
        try:
            os.write(fd, payload_json.encode("utf-8"))
        finally:
            os.close(fd)

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                result = subprocess.run(
                    [
                        "curl", "-s",
                        "-X", "POST",
                        "-H", f"Authorization: Bearer {api_key}",
                        "-H", "Content-Type: application/json",
                        "-H", "User-Agent: AI-Tools-Discovery/1.0",
                        "-d", f"@{tmp_path}",
                        "--max-time", "60",
                        "-w", "\n%{http_code}",
                        url,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=65,
                )

                # Parse response: stdout = body + "\n" + http_code
                lines = result.stdout.rsplit("\n", 1)
                status_str = lines[-1].strip() if lines else ""
                response_body = lines[0] if len(lines) > 1 else ""

                if result.returncode != 0 or not status_str.isdigit():
                    # Connection/DNS failure — retryable
                    error_msg = result.stderr.strip() or f"curl exit code {result.returncode}"
                    logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {error_msg}")
                    if attempt < MAX_ATTEMPTS:
                        _backoff(attempt, BACKOFF_SECONDS)
                        continue
                    try:
                        raise RuntimeError(error_msg)
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "attempt": attempt, "curl_stderr": (result.stderr.strip() or "")[:1024]}, level="warning")
                    return (False, True)

                http_code = int(status_str)

                if 200 <= http_code < 300:
                    return (True, False)

                logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: HTTP {http_code}")
                _log_http_error_details(http_code, response_body or None)

                # Cloudflare 403s with error 1010 are transient rate limits — allow retry
                is_cloudflare_block = http_code == 403 and response_body and "1010" in response_body
                if http_code in NON_RETRYABLE_CODES and not is_cloudflare_block:
                    try:
                        error_detail = f"HTTP {http_code}"
                        if response_body:
                            error_detail += f": {response_body[:200]}"
                        raise RuntimeError(error_detail)
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "http_code": http_code, "attempt": attempt, "response_body": (response_body or "")[:1024]}, level="warning")
                    return (False, False)

                if attempt < MAX_ATTEMPTS:
                    _backoff(attempt, BACKOFF_SECONDS)
                else:
                    try:
                        error_detail = f"HTTP {http_code}"
                        if response_body:
                            error_detail += f": {response_body[:200]}"
                        raise RuntimeError(error_detail)
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "http_code": http_code, "attempt": attempt, "response_body": (response_body or "")[:1024]}, level="warning")
                    return (False, True)

            except subprocess.TimeoutExpired:
                logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} timed out")
                if attempt < MAX_ATTEMPTS:
                    _backoff(attempt, BACKOFF_SECONDS)
                else:
                    try:
                        raise RuntimeError("curl timeout")
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "attempt": attempt}, level="warning")
                    return (False, True)

            except Exception as e:
                logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} error: {e}")
                if attempt < MAX_ATTEMPTS:
                    _backoff(attempt, BACKOFF_SECONDS)
                else:
                    report_to_sentry(e, {**ctx, "phase": "send_report", "attempt": attempt}, level="warning")
                    return (False, True)

        return (False, True)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _log_http_error_details(code: int, error_body: Optional[str]) -> None:
    """Log contextual details for specific HTTP error codes."""
    if code == 403:
        if error_body and "1010" in error_body:
            logger.error("403 Forbidden - Cloudflare/WAF blocked the request (Error 1010)")
        else:
            logger.error("403 Forbidden - Authentication failed. Check API key.")
        if error_body:
            logger.error(f"  Backend message: {error_body}")
    elif error_body:
        logger.error(f"Backend response: {error_body}")


def _backoff(attempt: int, delays: List[int]) -> None:
    """Sleep for the backoff duration corresponding to the given attempt."""
    wait = delays[attempt - 1]
    logger.info(f"  Retrying in {wait}s...")
    time.sleep(wait)


# ---------------------------------------------------------------------------
# Persistence: queue failed reports for the next run
# ---------------------------------------------------------------------------

def _get_queue_file_path() -> Path:
    """Return platform-appropriate queue file path.

    On Unix, /var/tmp persists across reboots (unlike /tmp).
    The filename includes the current UID so that different users
    (e.g. root via MDM vs. a regular login user) each get their own
    queue file, avoiding PermissionError on files created with 0600.
    On Windows, fall back to the standard temp directory (already per-user).
    """
    if platform.system() == "Windows":
        return Path(tempfile.gettempdir()) / "ai-discovery-queue.json"
    uid = os.getuid()
    return Path(f"/var/tmp/ai-discovery-queue-{uid}.json")


QUEUE_FILE = _get_queue_file_path()
QUEUE_MAX_AGE_SECONDS = 86400  # 24 hours
MAX_QUEUE_SIZE = 100  # Prevent unbounded growth across successive failures


def save_failed_reports(reports: List[Dict]) -> None:
    """Write failed report envelopes to the queue file, merging with any existing entries."""
    try:
        existing = _load_queue_file_safe()
        now_iso = datetime.now(timezone.utc).isoformat()
        envelopes = existing + [
            {"report": r, "queued_at": now_iso} for r in reports
        ]
        # Keep only the most recent entries to prevent unbounded growth
        envelopes = envelopes[-MAX_QUEUE_SIZE:]
        _write_file_secure(QUEUE_FILE, json.dumps(envelopes).encode())
        logger.info(f"Saved {len(reports)} failed report(s) to {QUEUE_FILE}")
    except Exception as e:
        logger.warning(f"Could not save failed reports: {e}")


def load_pending_reports() -> List[Dict]:
    """Load pending reports from the queue file and return the list.

    Reports older than 24 hours are silently discarded.
    """
    old_shared = Path("/var/tmp/ai-discovery-queue.json")
    if platform.system() != "Windows" and old_shared.exists():
        logger.info(
            f"Legacy shared queue file detected at {old_shared}"
            f" -- can be removed with: sudo rm {old_shared}"
        )

    if not QUEUE_FILE.exists():
        return []

    try:
        envelopes = json.loads(QUEUE_FILE.read_text())
    except Exception as e:
        logger.warning(f"Could not load pending reports: {e}")
        return []

    now = datetime.now(timezone.utc)
    valid: List[Dict] = []
    for envelope in envelopes:
        try:
            queued_at = datetime.fromisoformat(envelope["queued_at"])
            if (now - queued_at).total_seconds() > QUEUE_MAX_AGE_SECONDS:
                logger.debug("Discarding stale queued report (older than 24h)")
                continue
            valid.append(envelope["report"])
        except Exception:
            # Malformed envelope -- keep the report data if present
            valid.append(envelope.get("report", envelope))

    expired_count = len(envelopes) - len(valid)
    logger.info(f"Loaded {len(valid)} pending report(s) from queue ({expired_count} expired)")
    return valid


def _load_queue_file_safe() -> List[Dict]:
    """Load existing queue file contents, returning an empty list on any error."""
    if not QUEUE_FILE.exists():
        return []
    try:
        return json.loads(QUEUE_FILE.read_text())
    except Exception:
        return []


def _write_file_secure(path: Path, data: bytes) -> None:
    """Write data to a file with restricted permissions (0600 on Unix)."""
    path.write_bytes(data)
    # Restrict permissions to owner-only (rw-------) on Unix systems
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Claude Code subscription plan detection
# ---------------------------------------------------------------------------


def _is_root() -> bool:
    """Check if the current process is running as root (UID 0).

    Returns False on Windows where os.getuid() is not available.
    """
    try:
        return os.getuid() == 0
    except AttributeError:
        return False


def _get_uid_for_user(username: str) -> Optional[int]:
    """Resolve username to UID via the pwd module.

    Returns the numeric UID or None if the user cannot be found.
    """
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(username).pw_uid
    except (KeyError, ImportError):
        return None


def _is_daemon_container() -> bool:
    """Detect if running inside a macOS Daemon Container (e.g. Rippling MDM).

    Daemon Containers redirect Path.home() to a path under
    ~/Library/Daemon Containers/<UUID>/Data/Downloads.
    """
    return "Daemon Containers" in str(Path.home())


def _get_real_home(username: str) -> Optional[str]:
    """Resolve the real home directory for a user via the pwd module.
    Returns the home directory path or None if it cannot be resolved.
    """
    if pwd is None:
        return None
    try:
        return pwd.getpwnam(username).pw_dir
    except (KeyError, ImportError):
        return None


_COMPATIBLE_SHELLS = frozenset({"/bin/bash", "/bin/zsh", "/bin/sh"})


def _get_compatible_shell(username: str) -> str:
    """Return the user's login shell if it supports ``-lc``, else ``/bin/bash``.

    Reads the shell from the system passwd database via ``pwd.getpwnam``.
    Only shells in the allowlist (bash, zsh, sh) are returned directly;
    exotic shells like fish or csh are replaced with ``/bin/bash`` because
    their ``-lc`` behaviour is incompatible.

    Args:
        username: System username to look up.

    Returns:
        Absolute path to a shell that accepts ``-lc``.
    """
    if pwd is None:
        return "/bin/bash"
    try:
        shell = pwd.getpwnam(username).pw_shell
        if shell in _COMPATIBLE_SHELLS:
            return shell
    except (KeyError, ImportError):
        pass
    return "/bin/bash"


def _run_auth_status(
    cmd: list,
    username: str,
    method: str = "direct",
    env: Optional[dict] = None,
) -> Tuple[bool, Optional[str]]:
    """Execute an auth-status command and parse the subscription type.

    Returns a tuple of (success, subscription_type):
    - (True, "max")  — command ran successfully, user has a plan
    - (True, None)   — command ran successfully, user is not logged in
    - (False, None)  — command failed (non-zero exit, timeout, OS error)
    """
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=AUTH_STATUS_TIMEOUT,
            env=env,
        )

        if result.returncode != 0:
            logger.debug(
                f"claude auth status ({method}) returned non-zero for "
                f"{username}: rc={result.returncode}, "
                f"stderr={result.stderr.strip()}"
            )
            return (False, None)

        parsed = json.loads(result.stdout.strip())
        return (True, parsed.get("subscriptionType"))

    except subprocess.TimeoutExpired:
        logger.debug(f"claude auth status ({method}) timed out for {username}")
        return (False, None)
    except json.JSONDecodeError:
        logger.warning(f"claude auth status ({method}) returned non-JSON for {username}")
        return (False, None)
    except OSError as e:
        logger.debug(f"Could not run claude auth status ({method}) for {username}: {e}")
        return (False, None)


def _get_plan_from_keychain(username: str) -> Optional[str]:
    """Read Claude Code subscription plan directly from macOS Keychain.

    Reads the ``Claude Code-credentials`` entry for the given user from
    the macOS Keychain via the ``security`` CLI.  This avoids launching
    the full Node.js-based Claude CLI, making it ~25x faster and fully
    deterministic (no network, no timeout variability).

    When running as root the user's login keychain is not on the default
    search list, so we pass the path explicitly.

    Args:
        username: macOS username whose keychain entry to read.

    Returns:
        Subscription type string (e.g. "max", "pro") or None on any failure.
    """
    cmd = [
        "security", "find-generic-password",
        "-s", KEYCHAIN_SERVICE_NAME,
        "-a", username, "-w",
    ]

    is_root = _is_root()
    is_darwin = platform.system() == "Darwin"

    if is_root:
        real_home = _get_real_home(username)
        if real_home:
            keychain_path = f"{real_home}/Library/Keychains/login.keychain-db"
            cmd.append(keychain_path)

    is_container = is_darwin and _is_daemon_container()
    if is_darwin and (is_root or is_container):
        uid = _get_uid_for_user(username)
        if uid is not None:
            cmd = ["launchctl", "asuser", str(uid)] + cmd

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=KEYCHAIN_TIMEOUT,
        )
        if result.returncode != 0:
            logger.debug(f"No keychain entry for {username}: rc={result.returncode}")
            return None

        creds = json.loads(result.stdout.strip())
        plan = creds.get("claudeAiOauth", {}).get("subscriptionType")
        if plan:
            logger.debug(f"Keychain plan for {username}: {plan}")
        return plan

    except subprocess.TimeoutExpired:
        logger.debug(f"Keychain read timed out for {username}")
        return None
    except (json.JSONDecodeError, ValueError):
        logger.debug(f"Keychain entry for {username} is not valid JSON")
        return None
    except OSError as e:
        logger.debug(f"Could not read keychain for {username}: {e}")
        return None


def get_claude_subscription_type(
    username: str,
    claude_binary: str,
) -> Optional[str]:
    """
    Get the Claude Code subscription type for a specific user.

    On macOS, first attempts a fast-path read directly from the macOS
    Keychain (~15ms).  Falls back to running 'claude auth status --json'
    as the specified user if the keychain read fails.

    On macOS when running as root, uses 'launchctl asuser <uid>' to execute
    in the user's Mach bootstrap namespace (required for Keychain access).
    Falls back to 'su - {username} -c ...' if launchctl fails.

    On macOS when running inside a Daemon Container (e.g. Rippling MDM),
    also tries 'launchctl asuser' to escape the sandbox.

    On other platforms or when not running as root, runs directly.

    Args:
        username: System username to run the command as
        claude_binary: Absolute path to the claude binary

    Returns:
        Subscription type string (e.g., "max", "pro", "team", "enterprise")
        or None if detection fails or user is not logged in
    """
    try:
        # Fast path: read directly from macOS Keychain (no CLI needed)
        if platform.system() == "Darwin":
            plan = _get_plan_from_keychain(username)
            if plan:
                return plan

        # CLI fallback: spawn 'claude auth status --json'
        is_root = _is_root()
        is_darwin = platform.system() == "Darwin"
        is_container = is_darwin and _is_daemon_container()
        use_launchctl = is_darwin and (is_root or is_container)

        if use_launchctl:
            uid = _get_uid_for_user(username)
            if uid is not None:
                shell = _get_compatible_shell(username)
                cmd = [
                    "launchctl", "asuser", str(uid),
                    shell, "-lc",
                    f"{shlex.quote(claude_binary)} auth status --json",
                ]
                ok, plan = _run_auth_status(cmd, username, method="launchctl asuser")
                if ok:
                    return plan
                logger.debug(
                    f"launchctl asuser failed for {username}, "
                    f"trying fallback"
                )
            else:
                logger.debug(
                    f"Could not resolve UID for {username}, "
                    f"skipping launchctl asuser"
                )

        # Fallback for root on macOS: su - username
        if is_darwin and is_root:
            cmd = [
                "su", "-", username, "-c",
                f"{shlex.quote(claude_binary)} auth status --json",
            ]
            ok, plan = _run_auth_status(cmd, username, method="su")
            if ok:
                return plan

        # Direct execution — final fallback for all platforms
        cmd = [claude_binary, "auth", "status", "--json"]
        env = None
        if is_container:
            real_home = _get_real_home(username)
            if real_home:
                env = dict(os.environ)
                env["HOME"] = real_home
                logger.debug(
                    f"Overriding HOME to {real_home} for {username} "
                    f"(daemon container detected)"
                )
        ok, plan = _run_auth_status(cmd, username, method="direct", env=env)
        return plan

    except Exception as e:
        logger.debug(f"Unexpected error getting subscription for {username}: {e}")
        return None


# ---------------------------------------------------------------------------
# Cursor IDE subscription plan detection
# ---------------------------------------------------------------------------


def _get_cursor_db_path(user_home: Path) -> Optional[Path]:
    """Return the path to Cursor's state.vscdb for the given user home directory.

    Supports macOS and Windows. Returns None if the platform is unsupported
    or the database file does not exist.

    Args:
        user_home: Path to the user's home directory.

    Returns:
        Path to state.vscdb or None.
    """
    system = platform.system()
    if system == "Darwin":
        db_path = user_home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    elif system == "Windows":
        db_path = user_home / "AppData" / "Roaming" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    else:
        return None

    if not db_path.is_file():
        return None

    return db_path


def get_cursor_subscription_type(user_home: Path) -> Optional[str]:
    """Get the Cursor IDE subscription plan for a specific user.

    Reads the plan string from Cursor's SQLite state database using a
    temporary copy to avoid holding locks on the live file.

    Args:
        user_home: Path to the user's home directory.

    Returns:
        Plan string (e.g. "pro", "enterprise", "free", "business")
        or None if detection fails.
    """
    temp_db_path = None
    try:
        db_path = _get_cursor_db_path(user_home)
        if db_path is None:
            return None

        with tempfile.NamedTemporaryFile(suffix=".vscdb", delete=False) as temp_db:
            temp_db_path = temp_db.name

        shutil.copy2(db_path, temp_db_path)

        conn = sqlite3.connect(temp_db_path, timeout=CURSOR_DB_TIMEOUT)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM ItemTable WHERE key = ?", (CURSOR_PLAN_KEY,))
            row = cursor.fetchone()
        finally:
            conn.close()

        if not row:
            return None

        raw_value = row[0]
        if isinstance(raw_value, bytes):
            plan = raw_value.decode("utf-8", errors="ignore").strip()
        else:
            plan = str(raw_value).strip()

        return plan if plan else None

    except Exception:
        return None
    finally:
        if temp_db_path:
            try:
                Path(temp_db_path).unlink(missing_ok=True)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Sentry error reporting via raw HTTP (no SDK dependency)
# ---------------------------------------------------------------------------

_SENTRY_DSN = os.environ.get(
    "AI_DISCOVERY_SENTRY_DSN",
    "https://62a73a0043568547cb63a35394b63906@o4509196569149440.ingest.us.sentry.io/4510874666663936",
)
_SENTRY_ENV = os.environ.get("AI_DISCOVERY_SENTRY_ENV", "production")


def _parse_sentry_dsn(dsn: str) -> Optional[Dict[str, str]]:
    """Parse a Sentry DSN into its components."""
    try:
        # https://<key>@<host>/<project_id>
        scheme_rest = dsn.split("://", 1)
        scheme = scheme_rest[0]
        key_host_project = scheme_rest[1]
        key, host_project = key_host_project.split("@", 1)
        host, project_id = host_project.rsplit("/", 1)
        return {
            "key": key,
            "host": host,
            "project_id": project_id,
            "store_url": f"{scheme}://{host}/api/{project_id}/store/",
        }
    except Exception:
        return None


_SENTRY_TAG_KEYS = (
    "device_id", "app_name", "system_user",
    "tool_name", "domain", "phase", "http_code",
)


def report_to_sentry(
    exception: Exception,
    context: Optional[Dict] = None,
    level: str = "error",
) -> None:
    """Send an event to Sentry using the raw HTTP store endpoint.

    Args:
        exception: The exception to report.
        context: Extra tags/context (e.g. phase, tool_name, http_code).
        level: Sentry level -- "error" for crashes, "warning" for HTTP send failures.
    """
    try:
        dsn = _parse_sentry_dsn(_SENTRY_DSN)
        if not dsn:
            logger.debug("Sentry reporting skipped (no valid DSN configured)")
            return

        ctx = context or {}

        tags = {
            "os": platform.system(),
            "hostname": platform.node(),
            **{k: str(ctx[k]) for k in _SENTRY_TAG_KEYS if k in ctx},
        }

        payload = {
            "event_id": os.urandom(16).hex(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "platform": "python",
            "environment": _SENTRY_ENV,
            "sdk": {"name": "ai-tools-discovery", "version": "1.0.0"},
            "tags": tags,
            "exception": {
                "values": [
                    {
                        "type": type(exception).__name__,
                        "value": str(exception),
                        "stacktrace": {"frames": _extract_frames(exception)},
                    }
                ]
            },
            "extra": ctx,
        }

        sentry_auth = f"Sentry sentry_version=7, sentry_key={dsn['key']}, sentry_client=ai-tools-discovery/1.0.0"
        fd, tmp_path = tempfile.mkstemp(prefix="ai-discovery-sentry-", suffix=".json")
        try:
            try:
                os.write(fd, json.dumps(payload).encode("utf-8"))
            finally:
                os.close(fd)
            result = subprocess.run(
                [
                    "curl", "-s", "-o", "/dev/null",
                    "-w", "%{http_code}",
                    "-X", "POST",
                    "-H", "Content-Type: application/json",
                    "-H", f"X-Sentry-Auth: {sentry_auth}",
                    "-d", f"@{tmp_path}",
                    "--max-time", "5",
                    dsn["store_url"],
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                logger.debug(f"Sentry event sent ({result.stdout.strip()})")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as sentry_err:
        # Sentry failures must never crash the script
        logger.debug(f"Sentry reporting failed: {sentry_err}")


def _extract_frames(exception: Exception) -> List[Dict]:
    """Convert exception traceback into Sentry-style frame dicts."""
    if not exception.__traceback__:
        return []
    return [
        {
            "filename": frame.filename,
            "lineno": frame.lineno,
            "function": frame.name,
        }
        for frame in traceback.extract_tb(exception.__traceback__)
    ]


def send_discovery_metrics(
    backend_url: str,
    api_key: str,
    device_id: str,
    sentry_metrics: Dict[str, Any],
    run_id: Optional[str] = None,
    app_name: Optional[str] = None,
) -> bool:
    """Fire-and-forget POST of client-side timing metrics to the backend.

    Piggybacks on /api/v1/ai-tools/report/ with ``tools=[]`` so the backend's
    ``emit_discovery_metrics`` runs. Short timeout, no retries — metrics
    failures must never crash or slow down the discovery run.
    """
    if not backend_url or not api_key:
        return False

    url = f"{normalize_url(backend_url)}/api/v1/ai-tools/report/"
    payload: Dict[str, Any] = {
        "device_id": device_id,
        "tools": [],
        "sentry_metrics": sentry_metrics,
    }
    if run_id:
        payload["run_id"] = run_id
    if app_name:
        payload["app_name"] = app_name

    try:
        result = subprocess.run(
            [
                "curl", "-s",
                "-X", "POST",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-H", "User-Agent: AI-Tools-Discovery/1.0",
                "-d", json.dumps(payload),
                "--max-time", "10",
                "-w", "\n%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        status = result.stdout.rsplit("\n", 1)[-1].strip()
        ok = result.returncode == 0 and status.isdigit() and status.startswith("2")
        if ok:
            logger.debug(f"Discovery metrics sent (HTTP {status})")
        else:
            logger.debug(
                f"Discovery metrics send failed: rc={result.returncode} "
                f"status={status!r} stderr={result.stderr.strip()!r}"
            )
        return ok
    except Exception as e:
        logger.debug(f"Discovery metrics send raised: {e}")
        return False
