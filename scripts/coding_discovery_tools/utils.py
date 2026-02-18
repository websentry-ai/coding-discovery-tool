"""
Utility functions shared across the AI tools discovery system
"""

import json
import logging
import os
import platform
import re
import subprocess
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import COMMAND_TIMEOUT, INVALID_SERIAL_VALUES, VERSION_TIMEOUT

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
    Send discovery report to backend endpoint with retry logic.

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

    data = json.dumps(payload).encode('utf-8')

    for attempt in range(1, MAX_ATTEMPTS + 1):
        req = urllib.request.Request(url, data=data, method='POST')
        req.add_header("Authorization", f"Bearer {api_key}")
        req.add_header("Content-Type", "application/json")
        req.add_header("User-Agent", "AI-Tools-Discovery/1.0")

        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                if 200 <= response.getcode() < 300:
                    return (True, False)
        except urllib.error.HTTPError as e:
            error_body = _read_error_body(e)
            logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} failed: {e.code} - {e.reason}")
            _log_http_error_details(e.code, error_body)

            # Cloudflare 403s with error 1010 are transient rate limits — allow retry
            is_cloudflare_block = e.code == 403 and error_body and "1010" in error_body
            if e.code in NON_RETRYABLE_CODES and not is_cloudflare_block:
                report_to_sentry(
                    e,
                    {**ctx, "phase": "send_report", "http_code": e.code, "attempt": attempt},
                    level="warning",
                )
                return (False, False)

            if attempt < MAX_ATTEMPTS:
                _backoff(attempt, BACKOFF_SECONDS)
            else:
                report_to_sentry(
                    e,
                    {**ctx, "phase": "send_report", "http_code": e.code, "attempt": attempt},
                    level="warning",
                )
                return (False, True)

        except Exception as e:
            logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} error: {e}")

            if attempt < MAX_ATTEMPTS:
                _backoff(attempt, BACKOFF_SECONDS)
            else:
                report_to_sentry(
                    e,
                    {**ctx, "phase": "send_report", "attempt": attempt},
                    level="warning",
                )
                return (False, True)

    return (False, True)


def _read_error_body(error: urllib.error.HTTPError) -> Optional[str]:
    """Read and decode the response body from an HTTPError."""
    try:
        return error.read().decode('utf-8')
    except Exception:
        return None


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
        import tempfile
        return Path(tempfile.gettempdir()) / "ai-discovery-queue.json"
    return Path("/var/tmp/ai-discovery-queue.json")


QUEUE_FILE = _get_queue_file_path()
QUEUE_MAX_AGE_SECONDS = 86400  # 24 hours
MAX_QUEUE_SIZE = 100  # Prevent unbounded growth across successive failures


def save_failed_reports(reports: List[Dict]) -> None:
    """Write failed report envelopes to the queue file, merging with any existing entries.

    Expired entries (older than 24h) in the existing queue are discarded during merge.
    """
    try:
        existing = _load_queue_file_safe()
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        # Filter out expired entries from existing queue before merging
        fresh = []
        for env in existing:
            try:
                queued_at = datetime.fromisoformat(env["queued_at"])
                if (now - queued_at).total_seconds() <= QUEUE_MAX_AGE_SECONDS:
                    fresh.append(env)
            except Exception:
                fresh.append(env)  # Keep malformed envelopes
        envelopes = fresh + [
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
    """Write data atomically to a file with restrictive permissions (0o600)."""
    tmp_path = path.with_suffix(".tmp")
    fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)
    os.replace(str(tmp_path), str(path))


def send_report_to_backend_using_curl(backend_url: str, api_key: str, report: Dict, app_name: Optional[str] = None) -> bool:
    """
    Send discovery report to backend endpoint using curl.
    """
    url = f"{normalize_url(backend_url)}/api/v1/ai-tools/report/"

    if not api_key or not api_key.strip():
        logger.error("API key is empty or missing.")
        return False

    if app_name:
        report["app_name"] = app_name

    try:
        result = subprocess.run(
            [
                "curl",
                "-s", "-f",
                "-X", "POST",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-H", "User-Agent: AI-Tools-Discovery/1.0",
                "-d", json.dumps(report),
                "--max-time", "30",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=35,
        )

        if result.returncode == 0:
            return True

        error_msg = result.stderr.strip() or f"curl exit code {result.returncode}"
        logger.error(f"Failed to send report to {url}: {error_msg}")
        return False

    except subprocess.TimeoutExpired:
        logger.error(f"Request timed out sending report to {url}")
        return False
    except Exception as e:
        logger.error(f"Error sending report: {e}")
        return False


# ---------------------------------------------------------------------------
# Sentry error reporting via raw HTTP (no SDK dependency)
# ---------------------------------------------------------------------------

_SENTRY_DSN = os.environ.get("AI_DISCOVERY_SENTRY_DSN", "")


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

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(dsn["store_url"], data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header(
            "X-Sentry-Auth",
            f"Sentry sentry_version=7, sentry_key={dsn['key']}, sentry_client=ai-tools-discovery/1.0.0",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            logger.debug(f"Sentry event sent ({resp.getcode()})")
    except Exception:
        # Sentry failures must never crash the script
        pass


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
