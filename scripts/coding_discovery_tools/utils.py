"""
Utility functions shared across the AI tools discovery system
"""

import json
import logging
import os
import platform
import re
import shlex
import subprocess
import tempfile
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

try:
    import pwd
except ImportError:
    pwd = None  # Not available on Windows

from .constants import AUTH_STATUS_TIMEOUT, COMMAND_TIMEOUT, INVALID_SERIAL_VALUES, VERSION_TIMEOUT

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


def get_all_users_macos() -> List[str]:
    """
    Get all user directories from /Users on macOS.
    
    Returns:
        List of usernames (directory names in /Users)
    """
    users = []
    if platform.system() != "Darwin":
        return users
    
    users_dir = Path("/Users")
    if not users_dir.exists():
        return users
    
    try:
        for user_dir in users_dir.iterdir():
            # Skip hidden directories and Shared directory
            if user_dir.is_dir() and not user_dir.name.startswith('.') and user_dir.name != "Shared":
                users.append(user_dir.name)
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not list users from /Users: {e}")
    
    return users


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
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "attempt": attempt}, level="warning")
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
                        raise RuntimeError(f"HTTP {http_code}")
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "http_code": http_code, "attempt": attempt}, level="warning")
                    return (False, False)

                if attempt < MAX_ATTEMPTS:
                    _backoff(attempt, BACKOFF_SECONDS)
                else:
                    try:
                        raise RuntimeError(f"HTTP {http_code}")
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "http_code": http_code, "attempt": attempt}, level="warning")
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
    On Windows, fall back to the standard temp directory.
    """
    if platform.system() == "Windows":
        return Path(tempfile.gettempdir()) / "ai-discovery-queue.json"
    return Path("/var/tmp/ai-discovery-queue.json")


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


def _run_auth_status(
    cmd: list,
    username: str,
    method: str = "direct",
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


def get_claude_subscription_type(
    username: str,
    claude_binary: str,
) -> Optional[str]:
    """
    Get the Claude Code subscription type for a specific user.

    Runs 'claude auth status' as the specified user and extracts the
    subscriptionType from the JSON output.

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
        is_root = _is_root()
        is_darwin = platform.system() == "Darwin"
        use_launchctl = is_darwin and (is_root or _is_daemon_container())

        if use_launchctl:
            uid = _get_uid_for_user(username)
            if uid is not None:
                cmd = [
                    "launchctl", "asuser", str(uid),
                    claude_binary, "auth", "status", "--json",
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

        # Direct execution (non-root, non-Darwin, or all fallbacks failed)
        if not is_root or not is_darwin:
            cmd = [claude_binary, "auth", "status", "--json"]
            ok, plan = _run_auth_status(cmd, username, method="direct")
            return plan

        return None

    except Exception as e:
        logger.debug(f"Unexpected error getting subscription for {username}: {e}")
        return None


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
