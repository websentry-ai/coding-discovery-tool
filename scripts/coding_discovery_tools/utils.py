"""
Utility functions shared across the AI tools discovery system
"""

import functools
import json
import logging
import os
import platform
import random
import re
import shlex
import shutil
import socket
import sqlite3
import stat
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


def resolve_npm_global_tool_bin(
    tool: str, user_home: Path, is_root: bool
) -> Optional[str]:
    """Resolve the install path of an npm-global Node CLI (e.g. ``gemini``,
    ``openclaw``) whose real binary lives at ``<npm global prefix>/bin/<tool>``.

    The npm global prefix varies (Homebrew node, nvm, pnpm), so we resolve it
    dynamically with ``npm prefix -g`` AND probe a set of static fallbacks.

    GUARD (cross-user FP class fixed in commit 93b5fc2): ``npm prefix -g``
    resolves the SCANNER's npm config — under a root/MDM multi-user scan that is
    NOT the target user's prefix, so honouring it would attribute the scanner's
    install to a user who has only residue. The ``npm prefix -g`` probe and the
    machine-global ``/opt/homebrew/bin`` fallback are therefore gated behind
    ``not is_root``. The ``user_home``-relative fallbacks (``~/.npm-global/bin``,
    nvm under ``user_home``, pnpm under ``user_home``) are correctly scoped to
    the user and stay unconditional. Never raises.

    Args:
        tool: The CLI/binary name (e.g. ``"gemini"`` / ``"openclaw"``).
        user_home: Home dir of the user being scanned.
        is_root: Whether the scan is running as root/SYSTEM.

    Returns:
        Absolute path to the resolved executable as a string, or None.
    """
    candidates: List[Path] = []

    # 1. Dynamic npm global prefix — SCANNER-scoped, so non-root only.
    if not is_root:
        prefix = run_command(["npm", "prefix", "-g"], COMMAND_TIMEOUT)
        if prefix:
            prefix = prefix.strip()
            if prefix:
                candidates.append(Path(prefix) / "bin" / tool)

    # 2. Machine-global Homebrew prefix — non-root only (shared install).
    if not is_root:
        candidates.append(Path("/opt/homebrew/bin") / tool)

    # 3. user_home-relative fallbacks — always safe (scoped to this user).
    candidates.append(user_home / ".npm-global" / "bin" / tool)
    candidates.append(user_home / ".local" / "share" / "pnpm" / tool)  # pnpm global
    try:
        nvm_node = user_home / ".nvm" / "versions" / "node"
        if nvm_node.exists():
            for version_dir in sorted(nvm_node.iterdir()):
                try:
                    if version_dir.is_dir():
                        candidates.append(version_dir / "bin" / tool)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError) as e:
        logger.debug(f"Could not enumerate nvm node dirs for {tool}: {e}")

    for candidate in candidates:
        try:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)
        except (PermissionError, OSError):
            continue

    return None


def machine_global_binary_owned_by_user(candidate: Path, user_home: Path) -> bool:
    """Under a root/MDM multi-user scan, decide whether a MACHINE-GLOBAL binary
    (Homebrew / /usr/local / /usr/bin) should be attributed to ``user_home``.

    - Owned by a REGULAR user (Homebrew on macOS and manual /usr/local installs
      are owned by the installing user): attribute to that owner ONLY — this is
      what prevents one user's install fanning out to every user (the 93b5fc2
      cross-user FP).
    - Owned by ROOT/system (uid 0, e.g. /usr/bin/claude from apt/dnf): genuinely
      system-wide and available to every user, so attribute to whoever is being
      scanned.

    Never raises: any stat/pwd failure returns False (do not attribute).

    Args:
        candidate: Absolute path to a machine-global binary.
        user_home: Home dir of the user currently being scanned.

    Returns:
        True if the binary should be attributed to ``user_home``, else False.
    """
    try:
        uid = os.stat(str(candidate)).st_uid
    except (OSError, PermissionError):
        return False
    if uid == 0:
        return True  # system-wide -> available to every scanned user
    if pwd is None:
        return False  # POSIX-only; should never be hit on Windows
    try:
        owner_home = Path(pwd.getpwuid(uid).pw_dir)
    except (KeyError, OSError, AttributeError):
        return False
    try:
        return owner_home.resolve() == user_home.resolve()
    except (OSError, RuntimeError):
        return owner_home == user_home


def get_hostname() -> str:
    """Get the system hostname."""
    return platform.node()


@functools.lru_cache(maxsize=1)
def in_container() -> bool:
    """Best-effort detection of whether we're running inside a container.

    Combines several signals because no single one is reliable across runtimes
    and kernels:
      - ``/.dockerenv`` / ``/run/.containerenv`` — Docker / Podman runtime markers.
      - root filesystem mounted as ``overlay`` — cgroup-version-agnostic.
      - ``/proc/1/cgroup`` docker/lxc/kube markers — cgroup v1 ONLY (v2 shows
        ``0::/`` from inside, so this is a fallback, not the primary check).

    This is for honest behavioural branching, not security — every marker here
    is forgeable by whoever controls the container. Result is cached for the
    process lifetime.
    """
    try:
        if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
            return True
    except OSError:
        pass

    try:
        with open("/proc/mounts", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "/" and parts[2] == "overlay":
                    return True
    except OSError:
        pass

    try:
        with open("/proc/1/cgroup", encoding="utf-8") as f:
            blob = f.read()
        if any(marker in blob for marker in ("/docker", "/lxc", "kubepods", "/containerd")):
            return True
    except OSError:
        pass

    return False


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


def get_all_users_linux() -> List[str]:
    """
    Get all human user directory names from /home on Linux.

    Parses /etc/passwd to filter out system accounts (UID < 1000) and
    accounts with non-interactive shells (nologin, false, etc.).
    Falls back to listing /home subdirectories when /etc/passwd is unreadable.

    Returns:
        List of usernames (directory names under /home), or an empty list
        if not running on Linux or /home does not exist.
    """
    if platform.system() != "Linux":
        return []

    home_dir = Path("/home")
    if not home_dir.exists():
        # Docker/CI root-only containers may have no /home at all
        if _is_root():
            return ["root"]
        return []

    # Build a set of usernames with UID >= 1000 and interactive shells
    # from /etc/passwd so we filter out service accounts.
    human_users: set = set()
    try:
        with open("/etc/passwd", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split(":")
                if len(parts) < 7:
                    continue
                username, _, uid_str, _, _, home_path, shell = (
                    parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], parts[6]
                )
                try:
                    uid = int(uid_str)
                except ValueError:
                    continue
                if uid < 1000:
                    continue
                if shell in NON_INTERACTIVE_SHELLS:
                    continue
                # Only include users whose home is under /home
                if home_path.startswith("/home/"):
                    human_users.add(username)
    except Exception as e:
        logger.debug(f"Could not parse /etc/passwd: {e}")

    users: List[str] = []
    try:
        for user_dir in home_dir.iterdir():
            if not user_dir.is_dir() or user_dir.name.startswith("."):
                continue
            # If we got passwd data, require UID >= 1000 filter; otherwise allow all
            if human_users and user_dir.name not in human_users:
                continue
            users.append(user_dir.name)
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not list users from /home: {e}")

    # Always include root's own account when running as root, regardless of /home contents
    if _is_root():
        root_name = Path("/root").name  # "root"
        if root_name not in users:
            users.append(root_name)

    return users


# Identities that are never a real human end-user. We map these to None for the
# audit/payload value so the backend never attributes a machine to a service
# account. Matching is case-insensitive against the whole, trimmed name.
# Trade-off: a rare human whose login literally equals one of these (e.g. an
# admin named "administrator", or a person named "daemon"/"nginx") is also
# rejected. We accept that — for audit attribution a false None is safer than
# mislabelling a machine with a service identity (the FE shows "No AI tools
# detected" rather than a wrong owner).
_NON_HUMAN_USERS: FrozenSet[str] = frozenset(
    {
        "root",
        "system",
        "unknown",
        # Windows built-in / service identities (may also appear bare, without a
        # DOMAIN prefix, depending on how whoami resolves them).
        "administrator",
        "localsystem",
        "local service",
        "network service",
        # Common Linux service accounts.
        "www-data",
        "postgres",
        "nobody",
        "daemon",
        "nginx",
        "mysql",
    }
)

# Windows domains that only ever own service principals (e.g.
# ``NT AUTHORITY\LOCAL SERVICE``, ``NT SERVICE\MSSQLSERVER``). Any user under
# these is a service account, never a human end-user.
_NON_HUMAN_WINDOWS_DOMAINS: FrozenSet[str] = frozenset({"nt authority", "nt service"})

# Home-relative config dirs that identify a tool wherever its binary was installed from.
TOOL_CONFIG_DIRS: FrozenSet[str] = frozenset({
    ".antigravity", ".augment", ".claude", ".cline", ".codeium", ".codex",
    ".copilot", ".cursor", ".gemini", ".junie", ".kilocode", ".roo",
    ".vscode", ".windsurf",
})


def probe_profile(home_user: str, user_home: Path) -> Dict:
    """Record what the scan could actually see in one profile.

    A detector that finds nothing looks identical whether the machine is clean or
    the profile was unreadable. ``readable``/``entries`` separate those two, and
    ``config_dirs`` flags a tool whose binary sits outside the candidate paths.

    Presence comes from the single directory listing rather than ``Path.exists()``,
    which reports an unreachable path (ENOTDIR/ELOOP, or Windows ERROR_NOT_READY on
    an unmounted profile container) as plainly absent. Each listed marker is then
    stat'd: it is known to exist, so any error there is denied access, never absence.
    """
    probe: Dict = {
        "home_user": home_user,
        "readable": False,
        "entries": 0,
        "config_dirs": [],
        "error": None,
    }
    try:
        names = {entry.name for entry in user_home.iterdir()}
    except OSError as e:
        probe["error"] = type(e).__name__
        return probe

    probe["readable"] = True
    probe["entries"] = len(names)

    for name in sorted(names & TOOL_CONFIG_DIRS):
        try:
            os.stat(user_home / name)
            probe["config_dirs"].append(name)
        except OSError as e:
            probe["error"] = probe["error"] or type(e).__name__
    return probe


# A home that isn't there is genuinely absent, not unreadable, so it must not block prune.
_ABSENT_PROFILE_ERRORS: FrozenSet[str] = frozenset({"FileNotFoundError", "NotADirectoryError"})


def profile_unreadable(probe: Dict) -> bool:
    """True when a profile is on disk but the scan could not inspect it."""
    return bool(probe["error"]) and probe["error"] not in _ABSENT_PROFILE_ERRORS


def _strip_windows_domain(name: str) -> str:
    """Return the bare username from a Windows ``DOMAIN\\username`` string.

    ``whoami`` on Windows returns ``DOMAIN\\username`` (or ``MACHINE\\username``);
    we only want the trailing username component. Names without a backslash are
    returned unchanged.

    Args:
        name: Raw whoami output (possibly ``DOMAIN\\username``).

    Returns:
        The bare username with any domain prefix stripped.
    """
    if name and "\\" in name:
        return name.split("\\")[-1]
    return name


def _real_user_or_none(name: Optional[str]) -> Optional[str]:
    """Return the trimmed username if it is a real human, otherwise None.

    Maps junk / service / machine identities to None so scan-lifecycle audit
    payloads never attribute a machine to a non-human account. This is the
    canonical filter and is self-contained: it strips any Windows ``DOMAIN\\``
    prefix itself, so it is safe regardless of the caller's path. Rejected
    (case-insensitive, after trimming + domain-stripping):
      - empty / whitespace-only
      - anything under the ``NT AUTHORITY`` / ``NT SERVICE`` Windows domains
        (e.g. ``NT AUTHORITY\\LOCAL SERVICE``, ``NT SERVICE\\MSSQLSERVER``)
      - the literal ``"unknown"``
      - ``"root"``, ``"system"``, and Windows built-ins (administrator,
        localsystem, local service, network service)
      - anything starting with ``"_"`` (macOS daemon accounts)
      - anything ending with ``"$"`` (Windows machine accounts)
      - common Linux service accounts (www-data, postgres, nobody, daemon,
        nginx, mysql)

    Args:
        name: Candidate username (may be None, may be ``DOMAIN\\username``).

    Returns:
        The trimmed, domain-stripped username if it is a real human, else None.
    """
    if not name:
        return None
    raw = name.strip()
    # Reject service principals by their Windows domain before stripping it.
    if "\\" in raw and raw.split("\\")[0].strip().lower() in _NON_HUMAN_WINDOWS_DOMAINS:
        return None
    stripped = _strip_windows_domain(raw).strip()
    if not stripped:
        return None
    if stripped.startswith("_"):
        return None
    if stripped.endswith("$"):
        return None
    if stripped.lower() in _NON_HUMAN_USERS:
        return None
    return stripped


def get_audit_user() -> Optional[str]:
    """Return the real human user running the scan, or None.

    This is the value to attach to scan-lifecycle audit payloads: it is the
    real human OR None, never a junk/service/machine identity. For
    container/daemon/root scans where no human can be resolved, returns None
    rather than a synthesized owner.

    Returns:
        The real human username, or None when no human user can be resolved.
    """
    # On Windows, resolve the RAW, domain-qualified identity (``whoami`` →
    # ``DOMAIN\\user``) so _real_user_or_none can apply its NT AUTHORITY /
    # NT SERVICE domain rejection. get_user_info() pre-strips the ``DOMAIN\\``
    # prefix (path-building needs the bare name), which would otherwise hide a
    # service principal like ``NT SERVICE\\MSSQLSERVER`` behind its bare,
    # non-denylisted name. Fall back to get_user_info() if whoami yields nothing.
    if platform.system() == "Windows":
        raw = run_command(["whoami"], COMMAND_TIMEOUT)
        if raw:
            return _real_user_or_none(raw)
    return _real_user_or_none(get_user_info())


def get_user_info() -> str:
    """
    Get current user information (whoami equivalent).
    Cross-platform function that returns username.
    Gets username directly from system information, not environment variables.

    On macOS, when running as root, finds the user with the most storage space
    in /Users directory to get the actual user instead of "root".

    On Windows, returns ``whoami`` with any ``DOMAIN\\`` prefix stripped, falling
    back to ``getpass.getuser()``. (It does NOT currently resolve the real
    interactive user when running as a service/SYSTEM — get_audit_user() maps
    such non-human identities to None.)

    NOTE: This ALWAYS returns a usable, non-None string (falling back to
    "unknown"). Callers that build filesystem paths like ``/Users/<user>`` rely
    on that guarantee. For an audit/payload value that is the real human OR
    None, use ``get_audit_user()`` instead.

    Returns:
        Current username as string (never None)
    """
    try:
        username = None

        if platform.system() == "Windows":
            # Use whoami command on Windows (works reliably)
            whoami_output = run_command(["whoami"], COMMAND_TIMEOUT)
            # Extract just the username if whoami returns DOMAIN\username format
            username = _strip_windows_domain(whoami_output) if whoami_output else None
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
    sentry_context: Optional[Dict] = None,
    system_user: Optional[str] = None,
    manifest: Optional[List[Dict]] = None,
    covered_home_users: Optional[List[str]] = None,
    probe_summary: Optional[List[Dict]] = None,
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
        system_user: Optional real human user running the scan (or None). Used by
            the backend to attribute empty machines. MUST be a real human or
            None (see ``get_audit_user``), never a junk/service identity.
        manifest: Optional [{"home_user", "tool_name"}] seen this run; sent only on
            "completed" so the backend set-diffs it to prune the rest.
        covered_home_users: Optional home users covered; sent only on "completed" to
            bound the prune scope.
        probe_summary: Optional per-profile probe results; sent on "completed" so an
            empty manifest can be told apart from an unreadable machine.

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

    if system_user:
        payload["system_user"] = system_user

    if home_user:
        payload["home_user"] = home_user

    if scan_error:
        payload["scan_error"] = scan_error

    if manifest is not None:
        payload["manifest"] = manifest

    if covered_home_users is not None:
        payload["covered_home_users"] = covered_home_users

    if probe_summary is not None:
        payload["probe_summary"] = probe_summary

    return send_report_to_backend(
        backend_url,
        api_key,
        payload,
        app_name,
        sentry_context
    )


MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 15
BACKOFF_CAP_SECONDS = 120


def send_report_to_backend(backend_url: str, api_key: str, report: Dict, app_name: Optional[str] = None, sentry_context: Optional[Dict] = None) -> Tuple[bool, bool]:
    """
    Send discovery report to backend endpoint using curl with retry logic.

    Uses curl subprocess to avoid Zscaler certificate issues with urllib.
    Retries up to 3 times with exponential backoff (2s, 4s) for retryable errors.
    Non-retryable HTTP errors (400, 401, 403, 404, 405, 422) fail immediately.

    For data reports (payloads carrying a non-empty ``tools`` list), tries the
    S3 presigned-upload path first (3-step: upload-url → S3 PUT → from-s3). On
    any failure, falls through to this legacy direct-POST endpoint, which has
    its own retry/queue logic. Scan-lifecycle events bypass S3 and use the
    legacy endpoint directly — they are tiny.

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

    url = f"{normalize_url(backend_url)}/api/v1/ai-tools/report/"
    ctx = sentry_context or {}

    if not api_key or not api_key.strip():
        logger.error("API key is empty or missing. Please provide a valid API key.")
        return (False, False)

    payload = dict(report)
    if app_name:
        payload["app_name"] = app_name

    # Stamp tool_name + hash atomically; backend uses both to dedup unchanged re-scans.
    from .s3_uploader import compute_payload_hash, should_use_s3, try_s3_upload
    tools = payload.get("tools")
    if isinstance(tools, list) and len(tools) == 1 and isinstance(tools[0], dict):
        raw_name = tools[0].get("name")
        if isinstance(raw_name, str) and raw_name.strip():
            try:
                payload_hash = compute_payload_hash(tools[0])
                payload["tool_name"] = raw_name.strip()
                payload["payload_hash"] = payload_hash
            except Exception as e:
                # Hash failure should never block the upload — log and proceed.
                logger.warning(f"Could not compute payload hash, dedup disabled for this report: {e}")

    if should_use_s3(payload):
        s3_success, _ = try_s3_upload(
            backend_url, api_key, payload, sentry_context=ctx,
        )
        if s3_success:
            return (True, False)
        logger.info("S3 upload path failed; falling back to legacy /api/v1/ai-tools/report/")

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
                        _backoff(attempt)
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
                    _backoff(attempt)
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
                    _backoff(attempt)
                else:
                    try:
                        raise RuntimeError("curl timeout")
                    except RuntimeError as exc:
                        report_to_sentry(exc, {**ctx, "phase": "send_report", "attempt": attempt}, level="warning")
                    return (False, True)

            except OSError as e:
                # curl missing or not executable: local, not transient, so sleeping cannot help.
                logger.error(f"Cannot execute curl: {e}")
                report_to_sentry(e, {**ctx, "phase": "send_report", "attempt": attempt}, level="warning")
                return (False, True)

            except Exception as e:
                logger.error(f"Attempt {attempt}/{MAX_ATTEMPTS} error: {e}")
                if attempt < MAX_ATTEMPTS:
                    _backoff(attempt)
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


def _backoff(attempt: int) -> None:
    """Sleep with equal-jittered exponential backoff; jitter keeps a fleet that failed together from retrying together."""
    ceiling = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
    wait = random.uniform(ceiling / 2, ceiling)
    logger.info(f"  Retrying in {wait:.1f}s...")
    time.sleep(wait)


# ---------------------------------------------------------------------------
# Persistence: queue failed reports for the next run
# ---------------------------------------------------------------------------

def _get_queue_file_path() -> Path:
    """Return platform-appropriate queue file path.

    If AI_DISCOVERY_QUEUE_FILE is set (and non-empty) in the environment,
    that path is used verbatim. This lets the test harness redirect the
    queue away from the real per-UID /var/tmp file so an interrupted test
    can never leave a fixture that a later real agent run would drain and
    POST to production.

    On Unix, /var/tmp persists across reboots (unlike /tmp).
    The filename includes the current UID so that different users
    (e.g. root via MDM vs. a regular login user) each get their own
    queue file, avoiding PermissionError on files created with 0600.
    On Windows, fall back to the standard temp directory (already per-user).
    """
    override = (os.environ.get("AI_DISCOVERY_QUEUE_FILE") or "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    if platform.system() == "Windows":
        return Path(tempfile.gettempdir()) / "ai-discovery-queue.json"
    uid = os.getuid()
    return Path(f"/var/tmp/ai-discovery-queue-{uid}.json")


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
        queue_file = _get_queue_file_path()
        _write_file_secure(queue_file, json.dumps(envelopes).encode())
        logger.info(f"Saved {len(reports)} failed report(s) to {queue_file}")
    except Exception as e:
        logger.warning(f"Could not save failed reports: {e}")
        report_to_sentry(e, {"phase": "queue"}, level="warning")


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

    queue_file = _get_queue_file_path()
    if not queue_file.exists():
        return []

    try:
        envelopes = json.loads(queue_file.read_text())
    except Exception as e:
        logger.warning(f"Could not load pending reports: {e}")
        report_to_sentry(e, {"phase": "queue"}, level="warning")
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
    queue_file = _get_queue_file_path()
    if not queue_file.exists():
        return []
    try:
        return json.loads(queue_file.read_text())
    except Exception:
        return []


def _write_file_secure(path: Path, data: bytes) -> None:
    """Write data to a file with restricted permissions (0600 on Unix)."""
    # Ensure the parent exists so a queue-path override with a missing parent
    # doesn't silently drop the write (and lose the failed reports).
    path.parent.mkdir(parents=True, exist_ok=True)
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
) -> Tuple[bool, Optional[str], Optional[str], Optional[str]]:
    """Execute an auth-status command and parse the subscription type.

    Returns a tuple of (success, subscription_type, auth_method, api_key_source):
    - (True, "max", "claude.ai", None)           — user has a personal plan
    - (True, "api_key", "api_key", "ANTHROPIC_API_KEY") — API key auth
    - (True, None, "claude.ai", "/login managed key")   — org-managed login
    - (True, None, None, None)                   — user is not logged in
    - (False, None, None, None)                  — command failed
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
            return (False, None, None, None)

        parsed = json.loads(result.stdout.strip())
        auth_method = parsed.get("authMethod")
        api_key_source = parsed.get("apiKeySource")
        plan = parsed.get("subscriptionType")
        if plan is None and "api_key" in str(auth_method or "").lower():
            plan = "api_key"
        return (True, plan, auth_method, api_key_source)

    except subprocess.TimeoutExpired:
        logger.debug(f"claude auth status ({method}) timed out for {username}")
        return (False, None, None, None)
    except json.JSONDecodeError:
        logger.warning(f"claude auth status ({method}) returned non-JSON for {username}")
        return (False, None, None, None)
    except OSError as e:
        logger.debug(f"Could not run claude auth status ({method}) for {username}: {e}")
        return (False, None, None, None)


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


def _get_plan_from_credentials_file(user_home: Path) -> Optional[str]:
    """Read the Claude subscription plan from ``<user_home>/.claude/.credentials.json``.

    On Linux and Windows, Claude caches the plan as
    ``claudeAiOauth.subscriptionType`` there. Reading it directly — like Cursor's
    plan read — works cross-user in a privileged all-users scan, avoiding a CLI
    run that would read the scanner's own session. Opens non-blocking and checks
    the descriptor is a regular file, so a raced/planted FIFO can't stall the
    scan. Returns None on any failure (including a ``null`` ``subscriptionType``),
    so the caller falls back to the CLI.
    """
    path = os.path.join(str(user_home), ".claude", ".credentials.json")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None  # missing / no permission / symlink (O_NOFOLLOW)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None  # FIFO / device / dir — don't block or stream
        # The opened file must resolve inside the home and be the file we opened
        # (fd-anchored, so a redirect swapped in around open can't slip through).
        real_home = os.path.realpath(str(user_home))
        resolved = os.path.realpath(path)
        if os.path.normcase(os.path.commonpath([resolved, real_home])) != os.path.normcase(real_home):
            return None
        rst = os.stat(resolved)
        if (rst.st_ino, rst.st_dev) != (st.st_ino, st.st_dev):
            return None
        # Reject a hardlink (shares an inode, stays in-home, passing the checks
        # above). rst is the validated stat; os.stat gives st_nlink on Windows too.
        if rst.st_nlink > 1:
            return None
        # POSIX: also require the file to belong to the home's owner.
        if hasattr(os, "geteuid"):
            try:
                if st.st_uid != os.stat(str(user_home)).st_uid:
                    return None
            except OSError:
                return None
        raw = os.read(fd, 1_000_000).decode("utf-8", "replace")  # tiny file; cap
    except (OSError, ValueError) as e:
        logger.debug("Could not read Claude credentials at %s: %s", path, e)
        return None
    finally:
        os.close(fd)
    try:
        creds = json.loads(raw)
    except (ValueError, RecursionError) as e:
        # RecursionError (deeply nested JSON) isn't a ValueError; catch it too so
        # a planted file can't propagate an exception out of the fast path.
        logger.debug("Claude credentials at %s not parseable: %s", path, type(e).__name__)
        return None
    oauth = creds.get("claudeAiOauth") if isinstance(creds, dict) else None
    plan = oauth.get("subscriptionType") if isinstance(oauth, dict) else None
    if isinstance(plan, str):
        plan = plan.strip()
        # The file is user-writable in an all-users scan; accept only a plain tier
        # identifier so a crafted value can't inject into logs / the report field.
        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", plan):
            return plan
    return None


def get_claude_subscription_type(
    username: str,
    claude_binary: Optional[str] = None,
    diagnostics: Optional[List[Dict]] = None,
    user_home: Optional[Path] = None,
) -> Optional[str]:
    """
    Get the Claude Code subscription type for a specific user.

    On macOS, first attempts a fast-path read directly from the macOS
    Keychain (~15ms).  Falls back to running 'claude auth status --json'
    as the specified user if the keychain read fails.

    When ``claude_binary`` is ``None``, the command is passed through the
    user's login shell (``shell -lc "claude auth status --json"``) so that
    the shell's PATH resolves the binary.  This covers installations via
    non-standard package managers (volta, pnpm, fnm, asdf, mise, etc.)
    without needing to know the exact install path.

    On macOS when running as root, uses 'launchctl asuser <uid>' to execute
    in the user's Mach bootstrap namespace (required for Keychain access).
    Falls back to 'su - {username} -c ...' if launchctl fails.

    On macOS when running inside a Daemon Container (e.g. Rippling MDM),
    also tries 'launchctl asuser' to escape the sandbox.

    On other platforms or when not running as root, runs directly.

    Args:
        username: System username to run the command as
        claude_binary: Absolute path to the claude binary, or None to
            let the user's login shell resolve ``claude`` via PATH.
        diagnostics: Optional list to collect breadcrumb dicts for
            diagnostic reporting.  When ``None`` (the default), no
            breadcrumbs are recorded and behaviour is identical to
            previous versions.

    Returns:
        Subscription type string (e.g., "max", "pro", "team", "enterprise",
        "api_key") or None if detection fails or user is not logged in
    """
    try:
        is_root = _is_root()
        binary_status = "provided" if claude_binary else "shell_resolution"

        if diagnostics is not None:
            diagnostics.append({
                "category": "plan_detection",
                "message": "Starting plan detection",
                "level": "info",
                "data": {
                    "os": platform.system(),
                    "is_root": is_root,
                    "binary_status": binary_status,
                    "username": username,
                },
            })

        # Fast path: read directly from macOS Keychain (no CLI needed)
        if platform.system() == "Darwin":
            plan = _get_plan_from_keychain(username)
            if diagnostics is not None:
                diagnostics.append({
                    "category": "keychain",
                    "message": f"Keychain result: {plan}" if plan else "Keychain returned None",
                    "level": "info" if plan else "warning",
                    "data": {"plan": plan},
                })
            if plan:
                return plan

        # Fast path: read the cached plan from <user_home>/.claude/.credentials.json
        # (Linux/Windows only). A plain file read that works cross-user in an
        # all-users scan, unlike the CLI below which would read the scanner's own
        # session. Skipped on macOS: there the keychain above is the live source
        # and this file is often stale/leftover, so it must not shadow the CLI.
        if user_home is not None and platform.system() != "Darwin":
            plan = _get_plan_from_credentials_file(user_home)
            if diagnostics is not None:
                diagnostics.append({
                    "category": "credentials_file",
                    "message": f"Credentials-file result: {plan}" if plan else "Credentials file returned None",
                    "level": "info" if plan else "warning",
                    "data": {"plan": plan},
                })
            if plan:
                return plan

        # Build the auth command — full path when known, bare name otherwise
        if claude_binary:
            auth_cmd = f"{shlex.quote(claude_binary)} auth status --json"
        else:
            auth_cmd = "claude auth status --json"

        # CLI fallback: spawn 'claude auth status --json'
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
                    auth_cmd,
                ]
                ok, plan, auth_method, key_source = _run_auth_status(cmd, username, method="launchctl asuser")
                if diagnostics is not None:
                    diagnostics.append({
                        "category": "launchctl_asuser",
                        "message": f"ok={ok}, plan={plan}",
                        "level": "info" if ok else "warning",
                        "data": {"ok": ok, "plan": plan, "auth_method": auth_method, "key_source": key_source, "uid": uid, "shell": shell},
                    })
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
                if diagnostics is not None:
                    diagnostics.append({
                        "category": "launchctl_asuser",
                        "message": "UID resolution failed, skipping launchctl",
                        "level": "warning",
                        "data": {"uid": None},
                    })

        # Fallback for root on macOS: su - username
        if is_darwin and is_root:
            cmd = [
                "su", "-", username, "-c",
                auth_cmd,
            ]
            ok, plan, auth_method, key_source = _run_auth_status(cmd, username, method="su")
            if diagnostics is not None:
                diagnostics.append({
                    "category": "su_fallback",
                    "message": f"ok={ok}, plan={plan}",
                    "level": "info" if ok else "warning",
                    "data": {"ok": ok, "plan": plan, "auth_method": auth_method, "key_source": key_source},
                })
            if ok:
                return plan

        # Direct execution — final fallback for all platforms
        shell_fallback = False
        if claude_binary:
            cmd = [claude_binary, "auth", "status", "--json"]
        else:
            # No binary path known — use login shell to resolve via PATH
            shell_fallback = True
            shell = _get_compatible_shell(username)
            cmd = [shell, "-lc", auth_cmd]
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
        ok, plan, auth_method, key_source = _run_auth_status(cmd, username, method="direct", env=env)
        if diagnostics is not None:
            diagnostics.append({
                "category": "direct_exec",
                "message": f"ok={ok}, plan={plan}",
                "level": "info" if ok else "warning",
                "data": {
                    "ok": ok,
                    "plan": plan,
                    "auth_method": auth_method,
                    "key_source": key_source,
                    "binary": claude_binary,
                    "shell_fallback": shell_fallback,
                    "daemon_container": is_container if is_darwin else False,
                },
            })
        return plan

    except Exception as e:
        logger.debug(f"Unexpected error getting subscription for {username}: {e}")
        if diagnostics is not None:
            diagnostics.append({
                "category": "unexpected_error",
                "message": f"{type(e).__name__}: {e}",
                "level": "error",
                "data": {"error_type": type(e).__name__, "error_message": str(e)},
            })
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
    elif system == "Linux":
        db_path = user_home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
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


def _windows_process_is_elevated() -> bool:
    """True if the process is elevated, or on any error (fail closed)."""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return True


def _binary_in_cwd(path: str) -> bool:
    """True if ``path`` is inside the current working directory (a possible
    planted binary). A binary directly in the cwd counts even at a filesystem
    root; the nested-subtree check is skipped for a root cwd so real PATH installs
    aren't rejected. Case-folded for Windows. Fails closed on error."""
    try:
        nc = os.path.normcase  # case-fold on Windows; no-op on POSIX
        real_cwd = os.path.realpath(os.getcwd())
        cwd_is_root = os.path.dirname(real_cwd) == real_cwd
        abs_parent = os.path.dirname(os.path.abspath(path))
        # Check the parent lexically (catches a leaf like <cwd>/auggie) and
        # resolved (catches symlinks that would escape the tree).
        for parent, base in ((abs_parent, os.path.abspath(os.getcwd())),
                             (os.path.realpath(abs_parent), real_cwd)):
            parent, base = nc(parent), nc(base)
            if parent == base:
                return True
            if not cwd_is_root:
                try:
                    if os.path.commonpath([parent, base]) == base:
                        return True
                except ValueError:
                    pass  # different drive -> not under this cwd
        return False
    except OSError:
        return True


def _is_safe_exec_path(path: str) -> bool:
    """True if a resolved binary at ``path`` is safe to execute during a scan — not
    one another local account could have planted. POSIX: the binary and the
    directory it was found in must be owned by the running user or root and not
    group/world-writable, so a shared-writable PATH entry (e.g. a group-writable
    ``/usr/local/bin``) can't supply it. Windows has no comparable cheap check, so
    only the CWD guard applies there. Fails closed on any error."""
    if os.name == "nt":
        return True
    try:
        euid = os.geteuid()
        for target in (path, os.path.dirname(path) or os.sep):
            info = os.stat(target)
            if info.st_uid not in (euid, 0):
                return False
            if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
                return False
        return True
    except OSError:
        return False


def _which_no_cwd(name: str) -> Optional[str]:
    """``shutil.which`` that rejects a match planted in the CWD or supplied by a
    shared-writable directory another local account could plant into."""
    found = shutil.which(name)
    if not found:
        return None
    found = os.path.abspath(found)
    if _binary_in_cwd(found) or not _is_safe_exec_path(found):
        return None
    return found


def _is_scanning_users_own_home(user_home: Optional[Path]) -> bool:
    """True only if ``user_home`` is the scanning account's own home and the
    process isn't privileged. Both the plan probe and the detector PATH fallbacks
    gate on this so they can't drift. POSIX refuses ``euid == 0`` and compares the
    passwd home (not the spoofable ``$HOME``); Windows refuses an admin token and
    compares ``Path.home()``. Fails closed on any error."""
    if user_home is None:
        return False
    try:
        if platform.system() == "Windows":
            if _windows_process_is_elevated():
                return False
            own_home = Path.home()
        else:
            if not hasattr(os, "geteuid") or os.geteuid() == 0:
                return False
            try:
                own_home = Path(pwd.getpwuid(os.geteuid()).pw_dir) if pwd else Path.home()
            except (KeyError, OSError):
                return False  # arbitrary UID with no passwd entry — soft-fail
        return Path(user_home).resolve() == own_home.resolve()
    except (OSError, RuntimeError):
        return False


_AUGMENT_TENANT_HOST_SUFFIX = ".augmentcode.com"
_SESSION_MAX_BYTES = 1_000_000  # session.json is well under a KB; cap the read


def _windows_system_dir() -> Optional[str]:
    """The real Windows system directory (e.g. ``C:\\Windows\\System32``) from the OS
    via ``GetSystemDirectoryW``, not the ``%SystemRoot%`` env — so a caller-controlled
    env can't steer a trusted-path lookup. None on failure. Windows only."""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetSystemDirectoryW.argtypes = [wintypes.LPWSTR, wintypes.UINT]
        k32.GetSystemDirectoryW.restype = wintypes.UINT
        buf = ctypes.create_unicode_buffer(260)
        n = k32.GetSystemDirectoryW(buf, 260)
        if not n or n >= 260:
            return None
        return buf.value
    except (OSError, ValueError, AttributeError):
        return None


def _trusted_curl() -> Optional[str]:
    """Absolute path to the system curl (trusted OS locations only, never PATH),
    or None. Keeps a privileged scan from handing the token to a planted curl."""
    if platform.system() == "Windows":
        sysdir = _windows_system_dir()
        if not sysdir:
            return None
        candidates = [os.path.join(sysdir, "curl.exe")]
    else:
        candidates = ["/usr/bin/curl", "/bin/curl"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    return None


def _is_symlink_or_reparse(p: Path) -> bool:
    """True if ``p`` is a symlink or a Windows reparse point (junction), which
    could redirect a read elsewhere. Fails closed (True) if undetermined."""
    try:
        st = os.lstat(str(p))
    except OSError:
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(getattr(st, "st_file_attributes", 0) & reparse)


def _strip_extended_prefix(p: str) -> str:
    """Drop a Windows extended-length prefix (``\\\\?\\``, ``\\??\\``, ``\\\\?\\UNC\\``)
    so a ``GetFinalPathNameByHandle`` result compares against a plain ``realpath``."""
    if p.startswith("\\\\?\\UNC\\"):
        return "\\\\" + p[len("\\\\?\\UNC\\"):]
    for pre in ("\\\\?\\", "\\??\\"):
        if p.startswith(pre):
            return p[len(pre):]
    return p


def _windows_final_path(fd: int) -> Optional[str]:
    """The real filesystem path an open fd points to (junctions/symlinks resolved),
    read from the handle so a later path swap can't change it. None on any failure.
    Windows only; a privileged scan uses this instead of an owner check because
    Windows file ownership is unreliable (elevated writes are owned by the
    Administrators group, not the user)."""
    try:
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR,
                                                  wintypes.DWORD, wintypes.DWORD]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        handle = msvcrt.get_osfhandle(fd)
        needed = k32.GetFinalPathNameByHandleW(handle, None, 0, 0)  # NORMALIZED|DOS
        if not needed:
            return None
        buf = ctypes.create_unicode_buffer(needed)
        if not k32.GetFinalPathNameByHandleW(handle, buf, needed, 0):
            return None
        return buf.value
    except (OSError, ValueError, AttributeError):
        return None


def _read_own_regular_file(path: Path, owner_ref: Path, max_bytes: int) -> Optional[str]:
    """Read up to ``max_bytes`` of ``path`` as a regular file inside ``owner_ref``'s
    home, or None. Hardened for an all-users scan: refuses redirects and non-regular
    files, and re-checks the opened fd. Cross-user reads are allowed on both
    platforms — POSIX verifies the fd's own owner, Windows verifies the handle's
    real path stays inside the home (ownership is unreliable there)."""
    # Refuse a redirect at the file or its parent dir, and capture the file's own
    # identity — lstat never follows — to compare against the opened fd below.
    if _is_symlink_or_reparse(path.parent) or _is_symlink_or_reparse(path):
        return None
    try:
        lst = os.lstat(str(path))
    except OSError:
        return None
    if not stat.S_ISREG(lst.st_mode):
        return None
    try:
        fd = os.open(str(path),
                     os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None  # FIFO/device/dir — don't block or stream
        # The opened fd must be the exact file lstat saw before the open — same
        # inode and device — which catches a symlink/junction swapped in around
        # the open (Windows has no effective O_NOFOLLOW for junctions).
        if (st.st_ino, st.st_dev) != (lst.st_ino, lst.st_dev):
            return None
        # The file must belong to the home's owner. A pathname re-check (realpath,
        # stat) can't guarantee this: a parent junction can be swapped back before
        # it runs, so only the fd is trusted.
        if platform.system() == "Windows":
            # Windows ownership is unreliable (elevated writes are Administrators-
            # owned, not the user's), so verify the handle's REAL path instead:
            # GetFinalPathNameByHandle resolves every junction/symlink from the open
            # handle, so a redirect swapped in around the open resolves to its true
            # target and is refused when it falls outside the owner's home.
            final = _windows_final_path(fd)
            if final is None:
                return None
            real = os.path.normcase(_strip_extended_prefix(final))
            home = os.path.normcase(_strip_extended_prefix(os.path.realpath(str(owner_ref))))
            if not (real == home or real.startswith(home.rstrip("\\") + "\\")):
                return None
        else:
            # POSIX: the fd's own owner can't be forged by a path swap.
            try:
                if st.st_uid != os.stat(str(owner_ref)).st_uid:
                    return None
            except OSError:
                return None
        return os.read(fd, max_bytes).decode("utf-8", "replace")
    except OSError:
        return None
    finally:
        os.close(fd)


def _augment_tenant_host(url: str) -> Optional[str]:
    """Return the host if ``url`` is an ``https://…augmentcode.com`` tenant URL,
    else None. The only gate on where the token is sent, so it rejects anything
    that could resolve elsewhere or break the curl config. String parsing only
    (no urllib, for Zscaler)."""
    if not url or "://" not in url:
        return None
    # Reject spaces/control chars — they'd break the config line built from this.
    if any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return None
    scheme, rest = url.split("://", 1)
    if scheme.lower() != "https":
        return None
    host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in host:                      # strip user:pass@ credentials
        host = host.rsplit("@", 1)[1]
    if host.startswith("["):             # IPv6 literal — never an Augment tenant
        return None
    host = host.split(":", 1)[0].lower()   # strip :port
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", host):   # real host chars only
        return None
    if host == "augmentcode.com" or host.endswith(_AUGMENT_TENANT_HOST_SUFFIX):
        return host
    return None


def _read_auggie_session(user_home: Path) -> Optional[Tuple[str, str]]:
    """Return ``(tenant_base_url, access_token)`` from ``~/.augment/session.json``,
    or None. A plain file read, so it works for any user's home in an all-users
    scan; the base URL is rebuilt from the validated host only."""
    home = Path(user_home)
    raw = _read_own_regular_file(home / ".augment" / "session.json", home, _SESSION_MAX_BYTES)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    token = data.get("accessToken")
    tenant = data.get("tenantURL")
    if not isinstance(token, str) or not isinstance(tenant, str):
        return None
    # Usable as a header value: bounded, no control chars.
    if not 0 < len(token) <= 4096 or any(ord(c) < 32 or ord(c) == 127 for c in token):
        return None
    host = _augment_tenant_host(tenant)
    if host is None:
        return None
    return "https://" + host + "/", token


def _read_auggie_plan_via_cli(user_home: Path) -> Optional[str]:
    """Fallback plan lookup: ask the user's own ``auggie`` CLI. Only runs in a
    self-scan (``_is_scanning_users_own_home`` — same gate the detector uses), so
    a privileged all-users scan never executes another user's binary. Used when the
    stored token is expired/invalid and the billing API can't answer; the CLI reads
    (and can refresh) its own session. ``auggie account status --json`` prints
    ``planName``."""
    if not _is_scanning_users_own_home(user_home):
        return None
    auggie = _which_no_cwd("auggie")
    if auggie is None:
        return None
    try:
        result = subprocess.run(
            [auggie, "account", "status", "--json"],
            capture_output=True, text=True, timeout=AUTH_STATUS_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError) as e:
        logger.debug("auggie account status fallback failed: %s", e)
        return None
    if result.returncode != 0:
        logger.debug("auggie account status rc=%s", result.returncode)
        return None
    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    plan = parsed.get("planName")
    if not isinstance(plan, str):
        return None
    plan = plan.strip()
    if not plan or len(plan) > 100 or not plan.isprintable():
        return None
    return plan


def get_auggie_subscription_type(user_home: Optional[Path]) -> Optional[str]:
    """Get the Auggie (Augment) subscription plan for ``user_home``, or None.

    Auggie keeps no plan on disk. Primary path: read the session token from
    ``~/.augment/session.json`` and query Augment's billing endpoint with curl — a
    file read plus an HTTP call, never running a binary, so it works for any user in
    an all-users scan. If that can't answer (a dead/expired token, unreadable
    session, no curl) and we're scanning our OWN home, fall back to the user's
    ``auggie`` CLI. Best-effort, optional field.
    """
    if user_home is None:
        return None
    plan = _auggie_plan_via_billing_api(user_home)
    if plan is not None:
        return plan
    return _read_auggie_plan_via_cli(user_home)


def _auggie_plan_via_billing_api(user_home: Path) -> Optional[str]:
    """Query Augment's billing endpoint for the plan using the stored session
    token, or None. See ``get_auggie_subscription_type`` for the overall flow."""
    # Trusted curl only, never PATH (see _trusted_curl).
    curl = _trusted_curl()
    if curl is None:
        logger.debug("no trusted curl found; skipping auggie plan lookup")
        return None
    session = _read_auggie_session(user_home)
    if session is None:
        return None
    base_url, token = session

    # Token goes via the stdin config, never argv (not ps-visible). curl (not
    # urllib) uses the system cert store, for customer VPN/proxy CAs (Zscaler).
    def _cfg_quote(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')
    config = (
        'silent\n'
        'fail\n'                 # non-2xx -> non-zero exit, no error body to parse
        'proto = "=https"\n'     # https only, even if the URL were somehow rewritten
        'max-filesize = %d\n'    # bound the response so it can't inflate memory
        'request = "POST"\n'
        'header = "Authorization: Bearer %s"\n'
        'header = "Content-Type: application/json"\n'
        'data = "{}"\n'
        'max-time = %d\n'
        'url = "%s"\n'
    ) % (_SESSION_MAX_BYTES, _cfg_quote(token), AUTH_STATUS_TIMEOUT,
         _cfg_quote(base_url + "get-billing-summary"))

    try:
        result = subprocess.run(
            # -q: ignore any ambient ~/.curlrc on this token-bearing request.
            [curl, "-q", "--config", "-"],
            input=config,
            capture_output=True,
            text=True,
            timeout=AUTH_STATUS_TIMEOUT + 5,
        )
    except subprocess.TimeoutExpired:
        logger.debug("auggie billing lookup timed out")
        return None
    except OSError as e:
        logger.debug("Could not run auggie billing lookup: %s", e)
        return None

    if result.returncode != 0:
        # Log the code only — curl output can carry account details.
        logger.debug("auggie billing lookup curl rc=%s", result.returncode)
        return None
    try:
        parsed = json.loads(result.stdout.strip())
    except json.JSONDecodeError:
        logger.debug("auggie billing lookup returned non-JSON")
        return None
    if not isinstance(parsed, dict):
        return None
    plan = parsed.get("plan_name")
    if not isinstance(plan, str):
        return None
    plan = plan.strip()
    # Bound it before it enters logs/report: printable only (rejects control
    # chars, DEL, and line/paragraph separators that could smear a log line).
    if not plan or len(plan) > 100 or not plan.isprintable():
        return None
    return plan


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


# Low-cardinality no_tools_found discriminators (bools + small ints); duration_ms stays in extra.
_SENTRY_TAG_KEYS = (
    "device_id", "app_name", "system_user",
    "tool_name", "domain", "phase", "http_code",
    "is_root", "used_fallback_user", "homes_enumerated", "users_scanned",
)

# Per-run guards. report_to_sentry() is wired into ~20 previously log-only paths
# (including the detect_all_tools loop) and shells out to curl synchronously. On a
# machine where the Sentry endpoint is slow or blocked (the corporate-proxy / Zscaler
# fleets this tool targets), an unguarded fan-out of failures would add the curl
# timeout to every failing step and stretch a fast scan into minutes. These bound it:
#   - dedup by signature + a hard cap, since Sentry dedups server-side anyway, so N
#     identical curls buy nothing;
#   - a circuit breaker that stops calling Sentry for the rest of the run once the
#     transport looks dead.
# Single-threaded by design: only the main scan thread calls report_to_sentry() (the
# heartbeat thread never does), so no locking is needed. A discovery run is a
# short-lived process, so "per run" == process lifetime; reset_sentry_run_state()
# restores the clean starting point for long-lived test processes.
_SENTRY_MAX_EVENTS_PER_RUN = 30
_SENTRY_BREAKER_THRESHOLD = 3
_sentry_sent_signatures = set()
_sentry_event_count = 0
_sentry_consecutive_fails = 0
_sentry_dead_this_run = False


def reset_sentry_run_state() -> None:
    """Reset the per-run Sentry dedup / circuit-breaker state."""
    global _sentry_event_count, _sentry_consecutive_fails, _sentry_dead_this_run
    _sentry_sent_signatures.clear()
    _sentry_event_count = 0
    _sentry_consecutive_fails = 0
    _sentry_dead_this_run = False


def _ip_is_loopback(host: str) -> bool:
    """True when ``host`` is a loopback IP literal (IPv4 incl. shorthand, ::1, IPv4-mapped)."""
    try:
        return socket.inet_aton(host)[0] == 127
    except OSError:
        pass
    try:
        packed = socket.inet_pton(socket.AF_INET6, host)
    except (OSError, AttributeError):
        return False
    if packed == b"\x00" * 15 + b"\x01":
        return True
    if packed[:12] == b"\x00" * 10 + b"\xff\xff":
        return packed[12] == 127
    return False


def _event_domain_is_loopback(domain: str) -> bool:
    """True when ``domain``'s host is loopback. Plain string parsing, no urllib (Zscaler)."""
    if not domain:
        return False
    host = domain.strip().lower()
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in host:
        host = host.rsplit("@", 1)[1]
    if host.startswith("["):
        host = host[1:].split("]", 1)[0]
    elif host.count(":") <= 1:
        host = host.split(":", 1)[0]
    if host == "localhost" or host.endswith(".localhost") or host == "0.0.0.0":
        return True
    return _ip_is_loopback(host)


def _is_ci_or_local_event(ctx: Dict) -> bool:
    """True for CI/local-run events (loopback report domain). Never raises; defaults False."""
    try:
        return _event_domain_is_loopback(str(ctx.get("domain") or ""))
    except Exception:
        return False


def report_to_sentry(
    exception: Exception,
    context: Optional[Dict] = None,
    level: str = "error",
    priority: bool = False,
) -> None:
    """Send an event to Sentry using the raw HTTP store endpoint.

    Args:
        exception: The exception to report.
        context: Extra tags/context (e.g. phase, tool_name, http_code).
        level: Sentry level -- "error" for crashes, "warning" for HTTP send failures.
        priority: Best-effort guarantee a terminal once-per-run diagnostic
            (e.g. the no_tools_found summary) is delivered. Bypasses both the
            per-run event cap AND the circuit breaker so earlier transient
            per-tool send failures can't silently skip it -- it still gets ONE
            attempt at the end of the run (bounded: at most one ~4s curl). Dedup
            is still honored (no spam). Reserve for a single terminal event/run.
    """
    try:
        dsn = _parse_sentry_dsn(_SENTRY_DSN)
        if not dsn:
            logger.debug("Sentry reporting skipped (no valid DSN configured)")
            return

        ctx = context or {}

        if _is_ci_or_local_event(ctx):
            logger.debug("Sentry reporting skipped (CI/local run)")
            return

        global _sentry_event_count, _sentry_consecutive_fails, _sentry_dead_this_run
        # Circuit breaker: once the transport looks dead, stop calling Sentry for the
        # rest of the run so a blocked endpoint can't add its timeout to every failure.
        # priority events bypass it for ONE bounded attempt so a transient mid-scan
        # outage doesn't silently drop the terminal diagnostic.
        if _sentry_dead_this_run and not priority:
            return
        # Collapse duplicate events and hard-cap the synchronous curls per run.
        # priority events skip the count cap + breaker (but never dedup) so a
        # terminal once-per-run diagnostic isn't starved by earlier per-tool errors.
        signature = (type(exception).__name__, ctx.get("phase"), ctx.get("tool_name"))
        if signature in _sentry_sent_signatures:
            return
        if not priority and _sentry_event_count >= _SENTRY_MAX_EVENTS_PER_RUN:
            return
        _sentry_sent_signatures.add(signature)
        _sentry_event_count += 1

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
        sent_ok = False
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
                    "--max-time", "3",
                    dsn["store_url"],
                ],
                capture_output=True,
                text=True,
                timeout=4,
            )
            sent_ok = (result.returncode == 0)
            if sent_ok:
                logger.debug(f"Sentry event sent ({result.stdout.strip()})")
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            # Trip the breaker on any transport failure (non-zero curl, timeout, or a
            # raised exception that is about to propagate to the outer handler) so a
            # blackholed endpoint stops being retried after a few attempts.
            if sent_ok:
                _sentry_consecutive_fails = 0
            else:
                _sentry_consecutive_fails += 1
                if _sentry_consecutive_fails >= _SENTRY_BREAKER_THRESHOLD:
                    _sentry_dead_this_run = True
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

    # Write payload to a temp file and pass via -d @path to avoid OSError when
    # payload exceeds ARG_MAX (matches the mitigation in send_report_to_backend).
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="ai-discovery-metrics-", suffix=".json")
    except OSError as e:
        logger.debug(f"Discovery metrics tempfile failed: {e}")
        return False

    try:
        try:
            os.write(fd, json.dumps(payload).encode("utf-8"))
        finally:
            os.close(fd)

        result = subprocess.run(
            [
                "curl", "-s",
                "-X", "POST",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-H", "User-Agent: AI-Tools-Discovery/1.0",
                "-d", f"@{tmp_path}",
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
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
