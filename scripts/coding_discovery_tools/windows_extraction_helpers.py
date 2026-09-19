"""
Shared helper functions for rules extraction across all platforms.

These functions are used by both Cursor and Claude Code rules extractors
on Windows and macOS to avoid code duplication.
"""

import logging
import ntpath
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable

from .constants import MAX_CONFIG_FILE_SIZE, SKIP_DIRS

logger = logging.getLogger(__name__)

_PROFILE_LIST_KEY = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
# LOCAL SYSTEM / LOCAL SERVICE / NETWORK SERVICE — S-1-5-18 is what an MDM runs as.
_SERVICE_PROFILE_SIDS = frozenset({"S-1-5-18", "S-1-5-19", "S-1-5-20"})
# Font Driver Host / UMFD, IIS application pools and DWM each get their own profile,
# one per session, so they are SID families rather than the fixed SIDs above. Real
# accounts are never matched: local and domain users are S-1-5-21-*, Entra S-1-12-1-*.
_SERVICE_PROFILE_SID_PREFIXES = ("S-1-5-96-", "S-1-5-82-", "S-1-5-90-")


def _is_service_profile_sid(sid: str) -> bool:
    """True for a profile Windows keeps for itself rather than for a person."""
    return sid in _SERVICE_PROFILE_SIDS or sid.startswith(_SERVICE_PROFILE_SID_PREFIXES)


def registry_profile_paths() -> Tuple[List[Path], bool]:
    """Profile paths Windows records in ``ProfileList``, and whether the read was
    complete.

    This is the authoritative answer to "who has a profile here", and unlike a
    ``C:\\Users`` listing it carries the real location, so a profile relocated to
    another drive is still found. ``.bak`` keys are kept — Windows renames a key
    that way when a profile fails to load, and the profile is still a real user's.

    UNC paths are returned too, even though they must not be scanned: the caller
    needs them to recognise a roaming profile's local cache under ``C:\\Users``.

    ``complete`` is False when any entry could not be read, so the caller can
    tell a whole profile list from a partial one and never treat a partial list
    as proof that a walked profile is not real.
    """
    try:
        import winreg
    except ImportError:
        return [], False

    paths: List[Path] = []
    complete = True
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                sid = None
                try:
                    sid = winreg.EnumKey(key, index)
                    base_sid = sid[:-4] if sid.endswith(".bak") else sid
                    if _is_service_profile_sid(base_sid):
                        continue
                    with winreg.OpenKey(key, sid) as sub_key:
                        raw, kind = winreg.QueryValueEx(sub_key, "ProfileImagePath")
                except OSError as exc:
                    complete = False
                    logger.debug(f"Could not read profile {sid or index}: {exc}", exc_info=True)
                    continue
                if not isinstance(raw, str) or not raw:
                    complete = False
                    logger.debug(f"Profile {sid} has no usable ProfileImagePath")
                    continue
                if kind == winreg.REG_EXPAND_SZ:
                    raw = ntpath.expandvars(raw)
                paths.append(Path(raw))
    except OSError as exc:
        logger.debug(f"Could not read {_PROFILE_LIST_KEY}: {exc}", exc_info=True)
        return [], False
    return paths, complete


_PROFILE_VARS = ("USERPROFILE", "HOMEPATH", "APPDATA", "LOCALAPPDATA")
_LOCAL_DRIVE = re.compile(r"^[A-Za-z]:\\")
_DRIVE_REMOTE = 4
# Where a package manager installs. Anywhere else is someone's own directory, whose
# name can carry a customer or project, so it is counted rather than sent.
_MACHINE_ROOT_VARS = ("ProgramFiles", "ProgramFiles(x86)", "ProgramData", "SystemRoot")


def _under(path: str, root: str) -> bool:
    """Whether ``path`` is ``root`` or inside it, on a separator boundary."""
    path = ntpath.normcase(ntpath.normpath(path)).rstrip("\\")
    root = ntpath.normcase(ntpath.normpath(root)).rstrip("\\")
    return path == root or path.startswith(root + "\\")


def _is_local_drive(path: str) -> bool:
    """Whether probing ``path`` stays off the network.

    A drive letter is not enough: ``Z:\\`` can be a mapped share, so the drive type
    is asked before anything touches the filesystem.
    """
    if not _LOCAL_DRIVE.match(path) or path.startswith("\\\\"):
        return False
    try:
        import ctypes
        return ctypes.windll.kernel32.GetDriveTypeW(path[:3]) != _DRIVE_REMOTE
    except (AttributeError, OSError):
        return True


def _is_machine_root(entry: str) -> bool:
    """Whether ``entry`` sits under a Windows-owned root, whose names are not the customer's."""
    for var in _MACHINE_ROOT_VARS:
        root = os.environ.get(var)
        if root and _under(entry, root):
            return True
    return False


def _expand_for_profile(entry: str, profile: Optional[str]) -> str:
    """Expand ``entry``, resolving per-user variables against ``profile`` not the scanner."""
    if profile:
        for var, value in (("USERPROFILE", profile), ("HOMEPATH", profile),
                           ("APPDATA", ntpath.join(profile, "AppData", "Roaming")),
                           ("LOCALAPPDATA", ntpath.join(profile, "AppData", "Local"))):
            entry = re.sub(rf"%{var}%", lambda _, v=value: v, entry, flags=re.IGNORECASE)
    elif any(f"%{var}%".lower() in entry.lower() for var in _PROFILE_VARS):
        return entry
    return ntpath.expandvars(entry)


def _profile_image_paths(winreg) -> Dict[str, str]:
    """``{SID: profile dir}`` from ``ProfileList``."""
    profiles: Dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _PROFILE_LIST_KEY) as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                sid = None
                try:
                    sid = winreg.EnumKey(key, index)
                    with winreg.OpenKey(key, sid) as sub_key:
                        raw, kind = winreg.QueryValueEx(sub_key, "ProfileImagePath")
                except OSError as exc:
                    logger.debug(f"Could not read profile {sid or index}: {exc}", exc_info=True)
                    continue
                if not isinstance(raw, str) or not raw:
                    continue
                if kind == winreg.REG_EXPAND_SZ:
                    raw = ntpath.expandvars(raw)
                profiles[sid[:-4] if sid.endswith(".bak") else sid] = ntpath.normpath(raw).rstrip("\\")
    except OSError as exc:
        logger.debug(f"Could not read {_PROFILE_LIST_KEY}: {exc}", exc_info=True)
    return profiles


def registry_user_path_dirs() -> List[str]:
    """Existing PATH directories from every loaded user hive, profile-relative.

    A CLI is invoked by name, so its directory is on the user's PATH whatever prefix
    installed it, and the candidate list can only name prefixes it already knows.
    Only loaded hives answer: mounting a logged-out profile's NTUSER.DAT is a side
    effect a probe has no business causing.
    """
    try:
        import winreg
    except ImportError:
        return []

    profiles = _profile_image_paths(winreg)
    roots = list(profiles.values())
    dirs: List[str] = []
    seen = set()
    withheld = 0
    try:
        with winreg.OpenKey(winreg.HKEY_USERS, "") as users:
            for index in range(winreg.QueryInfoKey(users)[0]):
                sid = None
                try:
                    sid = winreg.EnumKey(users, index)
                    if sid.endswith("_Classes") or sid in _SERVICE_PROFILE_SIDS:
                        continue
                    with winreg.OpenKey(users, sid + r"\Environment") as env:
                        raw, kind = winreg.QueryValueEx(env, "Path")
                except OSError as exc:
                    logger.debug(f"Could not read PATH for {sid or index}: {exc}", exc_info=True)
                    continue
                if not isinstance(raw, str):
                    continue
                profile = profiles.get(sid)
                for entry in raw.split(";"):
                    entry = entry.strip().rstrip("\\")
                    if not entry:
                        continue
                    if kind == winreg.REG_EXPAND_SZ:
                        entry = _expand_for_profile(entry, profile)
                    # Collapse first: ~\..\..\bob\bin would otherwise redact to a path naming bob.
                    entry = ntpath.normpath(entry).rstrip("\\")
                    key = ntpath.normcase(entry)
                    if key in seen:
                        continue
                    seen.add(key)
                    # Before any filesystem call: isdir on \\host\share authenticates
                    # this scan's token, which under MDM is Local System.
                    if not _is_local_drive(entry):
                        logger.debug(f"Skipping non-local PATH entry: {entry}")
                        continue
                    try:
                        if not os.path.isdir(entry):
                            continue
                    except OSError as exc:
                        logger.debug(f"Could not stat PATH entry {entry}: {exc}", exc_info=True)
                        continue
                    if profile and _under(entry, profile):
                        dirs.append("~" + entry[len(profile):])
                    elif _is_machine_root(entry) and not any(_under(entry, r) for r in roots):
                        dirs.append(entry)
                    else:
                        withheld += 1
                        logger.debug(f"Withholding PATH entry outside a known root: {entry}")
    except OSError as exc:
        logger.debug(f"Could not read HKEY_USERS Environment: {exc}", exc_info=True)
    if withheld:
        dirs.append(f"<{withheld} withheld>")
    return dirs


# Maps the globalStorage IDE-folder key (as used by Cline/Roo ``SUPPORTED_IDES``)
# to the host editor's Windows ``Programs``/``Program Files`` install-dir names
# and the executable names it puts on PATH. Used to gate Cline/Roo rows on the
# host editor actually being installed (the ``globalStorage/<ext-id>`` dir
# survives an editor uninstall, so it alone is not proof of install).
_WINDOWS_IDE_INSTALL_INFO: Dict[str, Dict[str, Tuple[str, ...]]] = {
    "Code": {
        "dir_names": ("Microsoft VS Code",),
        "exe_names": ("code", "code.cmd", "code.exe"),
    },
    "Cursor": {
        "dir_names": ("Cursor",),
        "exe_names": ("cursor", "cursor.cmd", "cursor.exe"),
    },
    "Windsurf": {
        "dir_names": ("Windsurf",),
        "exe_names": ("windsurf", "windsurf.cmd", "windsurf.exe"),
    },
}


def is_windows_ide_installed(ide_folder: str, user_home: Path) -> Tuple[bool, Optional[str]]:
    """Return ``(installed, path)`` for a host editor (VS Code / Cursor /
    Windsurf) on Windows, scanning every documented install location:

    * the user's per-user ``%LOCALAPPDATA%\\Programs\\<IDE>`` install,
    * machine-wide ``C:\\Program Files\\<IDE>`` and
      ``C:\\Program Files (x86)\\<IDE>``,
    * the editor's launcher on PATH (``shutil.which``).

    The thorough probe is deliberate: a too-narrow host check would HIDE a real
    Cline/Roo user whose editor lives somewhere the check forgot, trading the
    residue false-positive for a false-negative. ANY hit counts as installed.
    Never raises — every filesystem/PATH probe is wrapped.

    Args:
        ide_folder: The ``SUPPORTED_IDES`` key (``Code`` / ``Cursor`` /
            ``Windsurf``).
        user_home: The home dir of the user being scanned (so a SYSTEM/admin
            scan checks THAT user's per-user install, not the scanner's).

    Returns:
        Tuple of (is_installed, install_path_or_exe_path) — path is None when
        not installed.
    """
    info = _WINDOWS_IDE_INSTALL_INFO.get(ide_folder)
    if not info:
        return False, None

    install_bases = [
        user_home / "AppData" / "Local" / "Programs",
        Path("C:\\Program Files"),
        Path("C:\\Program Files (x86)"),
    ]
    for base in install_bases:
        for dir_name in info["dir_names"]:
            app_dir = base / dir_name
            try:
                if app_dir.exists() and app_dir.is_dir():
                    return True, str(app_dir)
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check IDE dir {app_dir}: {e}")
                continue

    # ``shutil.which`` resolves the SCANNER's PATH, not ``user_home``'s. Under an
    # elevated admin scan the admin's own VS Code/Cursor on PATH would be
    # attributed to every user with extension residue (the cross-user FP this PR
    # fixes everywhere else). Skip it when admin; the user_home-scoped and
    # machine-wide dir checks above already cover real installs. Mirrors the
    # Linux guard and ``find_claude_binary_for_user``'s ``which`` guard.
    if not is_running_as_admin():
        for exe_name in info["exe_names"]:
            try:
                found = shutil.which(exe_name)
                if found:
                    return True, found
            except (OSError, Exception) as e:  # noqa: BLE001 - which must never crash
                logger.debug(f"PATH lookup for {exe_name} failed: {e}")
                continue

    return False, None


def add_rule_to_project(
    rule_info: Dict,
    project_root: str,
    projects_by_root: Dict[str, List[Dict]]
) -> None:
    """
    Add a rule to the appropriate project in the dictionary.
    
    Args:
        rule_info: Rule file information dict
        project_root: Project root path as string
        projects_by_root: Dictionary to update
    """
    if project_root not in projects_by_root:
        projects_by_root[project_root] = []

    # Remove project_root from rule since it's now at project level
    rule_without_root = {k: v for k, v in rule_info.items() if k != 'project_root'}
    projects_by_root[project_root].append(rule_without_root)


def build_project_list(projects_by_root: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Convert projects dictionary to list format.
    
    Args:
        projects_by_root: Dictionary mapping project_root to list of rules
        
    Returns:
        List of project dicts with project_root and rules
    """
    return [
        {
            "project_root": project_root,
            "rules": rules
        }
        for project_root, rules in projects_by_root.items()
    ]


def should_skip_path(path: Path, system_dirs: Optional[set] = None) -> bool:
    """
    Check if path should be skipped during search.
    
    Skips paths containing directories like node_modules, .git, venv, etc.
    Optionally skips system directories (Windows-specific).
    
    Args:
        path: Path to check
        system_dirs: Optional set of system directory names to skip (Windows-specific)
        
    Returns:
        True if path should be skipped, False otherwise
    """
    # Skip common project directories (check all path parts for nested matches)
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    
    # Skip system directories if provided (Windows-specific)
    if system_dirs and path.name in system_dirs:
        return True
    
    return False


def extract_and_add_rule(
    file_path: Path,
    find_project_root_func,
    add_func,
    projects_by_root: Dict[str, List[Dict]],
    scope: str = "project"
) -> Optional[Dict]:
    """
    Extract a rule file and add it to the projects dictionary.

    Args:
        file_path: Path to the rule file
        find_project_root_func: Function to find project root (tool-specific)
        add_func: Function to add rule to project (e.g., add_rule_to_project)
        projects_by_root: Dictionary to update
        scope: Scope of the rule ("user", "project", etc.)

    Returns:
        The extracted rule_info dict, or None if extraction failed
    """
    rule_info = extract_single_rule_file(file_path, find_project_root_func, scope=scope)
    if rule_info:
        project_root = rule_info.get('project_root')
        if project_root:
            add_func(rule_info, project_root, projects_by_root)
    return rule_info


def is_user_level_tool_dir(tool_dir: Path) -> bool:
    """
    Check if a tool directory (e.g., .cursor, .claude) is at the user level.

    On Windows, user-level means directly under <drive>:\\Users\\<username>\\.

    Args:
        tool_dir: Path to the tool directory

    Returns:
        True if this is a user-level tool directory
    """
    parent = tool_dir.parent
    try:
        users_dir = Path(parent.anchor) / "Users"
        if parent.parent == users_dir:
            return True
    except Exception:
        pass
    return False


def scan_windows_user_directories(callback) -> None:
    """
    Scan Windows user directories and invoke callback for each.

    When running as admin, iterates all user directories under C:\\Users.
    Otherwise, invokes callback with the current user's home directory.

    Args:
        callback: Function that takes a user_dir Path argument
    """
    if is_running_as_admin():
        users_dir = Path("C:\\Users")
        if users_dir.exists():
            excluded = {'public', 'default', 'default user', 'all users'}
            for user_dir in users_dir.iterdir():
                if user_dir.is_dir() and not user_dir.name.startswith('.'):
                    if user_dir.name.lower() in excluded:
                        continue
                    try:
                        callback(user_dir)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping user directory {user_dir}: {e}")
    else:
        callback(Path.home())


def extract_single_rule_file(
    rule_file: Path,
    find_project_root_func: Optional[Callable[[Path], Path]] = None,
    scope: str = None
) -> Optional[Dict]:
    """
    Extract a single rule file with metadata.
    
    Args:
        rule_file: Path to the rule file
        find_project_root_func: Optional function to find project root (tool-specific).
                                If None, uses default find_project_root function.
        
    Returns:
        Dict with file info (file_path, file_name, project_root, content,
        size, last_modified, truncated) or None if extraction fails
    """
    try:
        if not rule_file.exists() or not rule_file.is_file():
            return None

        file_metadata = get_file_metadata(rule_file)
        project_root = (find_project_root_func or find_project_root)(rule_file)
        content, truncated = read_file_content(rule_file, file_metadata['size'])

        if scope is None or scope == "project":
            scope = _detect_rule_scope(rule_file)

        return {
            "file_path": str(rule_file),
            "file_name": rule_file.name,
            "project_root": str(project_root) if project_root else None,
            "content": content,
            "size": file_metadata['size'],
            "last_modified": file_metadata['last_modified'],
            "truncated": truncated,
            "scope": scope
        }

    except PermissionError as e:
        logger.warning(f"Permission denied reading {rule_file}: {e}")
        return None
    except UnicodeDecodeError as e:
        logger.warning(f"Unable to decode {rule_file} as text: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading rule file {rule_file}: {e}")
        return None


def _detect_rule_scope(rule_file: Path) -> str:
    """
    Detect the scope of a rule file based on its location.

    Uses path structure (C:\\Users\\<anyone>\\.<config_dir>\\...) instead of
    Path.home() so that scope detection works correctly when running
    as admin via MDM (where Path.home() may not match the actual user).
    """
    config_dir_names = {".cursor", ".claude", ".windsurf", ".antigravity", ".roo", ".cline", ".clinerules", ".kilocode", ".gemini", ".junie"}
    try:
        parts = rule_file.resolve().parts
        # On Windows: ('C:\\', 'Users', '<username>', '.<config_dir>', ...)
        if len(parts) >= 4 and parts[1] == "Users" and parts[3].startswith(".") and parts[3] in config_dir_names:
            return "user"
        return "project"
    except Exception:
        return "project"


def get_file_metadata(rule_file: Path) -> Dict[str, int]:
    """
    Get file metadata (size and last modified timestamp).
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Dict with 'size' and 'last_modified' keys
    """
    stat = rule_file.stat()
    return {
        'size': stat.st_size,
        'last_modified': datetime.utcfromtimestamp(stat.st_mtime).isoformat() + "Z"
    }


def read_file_content(rule_file: Path, file_size: int) -> Tuple[str, bool]:
    """
    Read file content, truncating if necessary.
    
    Args:
        rule_file: Path to the rule file
        file_size: Size of the file in bytes
        
    Returns:
        Tuple of (content, truncated) where truncated is True if file was truncated
    """
    if file_size > MAX_CONFIG_FILE_SIZE:
        logger.warning(
            f"Rule file {rule_file} exceeds size limit "
            f"({file_size} > {MAX_CONFIG_FILE_SIZE} bytes). Truncating."
        )
        return read_truncated_file(rule_file), True
    
    return rule_file.read_text(encoding='utf-8', errors='replace'), False


def find_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a rule file.
    
    Determines project root based on file location:
    - .clauderules/.cursorrules in root -> directory containing the file
    - .claude/.clauderules -> parent of .claude (2 levels up)
    - claude.md in root -> directory containing the file
    - .claude/claude.md -> parent of .claude (2 levels up)
    - .cursor/*.mdc -> parent of .cursor (2 levels up)
    - .cursor/rules/*.mdc -> parent of .cursor (3 levels up from file)
    - .windsurf/rules/* -> parent of .windsurf (2 levels up from rules)
    - ~/.windsurf/global_rules.md -> home directory
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent

    # Case 1: File is in .windsurf/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".windsurf":
        return parent.parent.parent

    # Case 2: Global Windsurf rules file in ~/.windsurf/global_rules.md
    if parent.name == ".windsurf" and rule_file.name == "global_rules.md":
        return parent.parent

    # Case 3: File is in .cursor/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".cursor":
        return parent.parent.parent

    if parent.name == "rules" and parent.parent.name == ".claude":
        return parent.parent.parent

    if parent.name in (".claude", ".cursor", ".windsurf"):
        return parent.parent

    if rule_file.name == ".cursorrules":
        return parent

    for ancestor in rule_file.parents:
        if ancestor.name == ".claude":
            return ancestor.parent

    return parent


def find_gemini_cli_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Gemini CLI rule file.
    
    For Gemini CLI rules:
    - Global rules in ~/.gemini/GEMINI.md -> home directory
    - Project rules: GEMINI.md in current directory or parent -> directory containing GEMINI.md
    - Sub-directory rules: GEMINI.md in subdirectories -> directory containing GEMINI.md
    
    Args:
        rule_file: Path to the rule file (GEMINI.md)
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: Global rules in ~/.gemini/GEMINI.md
    # Return the .gemini directory's parent (which would be home directory)
    if parent.name == ".gemini" and rule_file.name.upper() == "GEMINI.MD":
        return parent.parent  # Home directory
    
    # Case 2: Project or sub-directory rules
    # For Gemini CLI, the directory containing GEMINI.md is the project root
    # (could be actual project root or a subdirectory)
    return parent


def read_truncated_file(file_path: Path) -> str:
    """
    Read file content up to max size bytes.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Truncated file content as string
    """
    try:
        with open(file_path, 'rb') as f:
            content_bytes = f.read(MAX_CONFIG_FILE_SIZE)
            return content_bytes.decode('utf-8', errors='replace')
    except Exception as e:
        logger.warning(f"Error reading truncated file {file_path}: {e}")
        return ""


def windows_admin_state() -> Optional[bool]:
    """
    Whether the current process holds administrator rights.

    Returns:
        True or False, or None when the check itself could not run.
    """
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return None


def _running_as_local_system() -> bool:
    """
    True when the process runs as NT AUTHORITY\\SYSTEM (SID S-1-5-18).

    IsUserAnAdmin() only reports enabled Administrators-group membership, which
    SYSTEM does not have — its rights come from its own SID — so IsUserAnAdmin()
    returns False/undetermined for SYSTEM. Recognize SYSTEM separately, otherwise
    an MDM/all-users scan run as SYSTEM collapses to the empty systemprofile.
    """
    # Read the token's user SID through the Win32 API rather than spawning
    # whoami: a subprocess running as SYSTEM is an execute-as-SYSTEM vector and
    # can fail silently. windows_admin_state() above already uses ctypes.
    try:
        import ctypes
        from ctypes import wintypes

        TOKEN_QUERY = 0x0008
        TokenUser = 1
        LOCAL_SYSTEM_SID = "S-1-5-18"

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD)]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.ConvertSidToStringSidW.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
        advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)):
            return False
        try:
            size = wintypes.DWORD()
            advapi32.GetTokenInformation(token, TokenUser, None, 0, ctypes.byref(size))
            if not size.value:
                return False
            buf = (ctypes.c_byte * size.value)()
            if not advapi32.GetTokenInformation(
                    token, TokenUser, buf, size, ctypes.byref(size)):
                return False
            # TOKEN_USER begins with SID_AND_ATTRIBUTES whose first member is the PSID.
            psid = ctypes.cast(buf, ctypes.POINTER(ctypes.c_void_p))[0]
            str_sid = wintypes.LPWSTR()
            if not advapi32.ConvertSidToStringSidW(psid, ctypes.byref(str_sid)):
                return False
            try:
                return str_sid.value == LOCAL_SYSTEM_SID
            finally:
                kernel32.LocalFree(str_sid)
        finally:
            kernel32.CloseHandle(token)
    except Exception as e:
        # A silent failure reverts SYSTEM to non-admin and can empty an all-users
        # scan, so log it rather than leaving it invisible.
        logger.debug("SYSTEM SID detection failed: %s", e)
        return False


def is_running_as_admin() -> bool:
    """
    Check if the current process is running as administrator.

    Returns:
        True if running as administrator, False otherwise
    """
    # Administrators-group membership is the primary signal. SYSTEM is root but
    # is NOT in that group, so IsUserAnAdmin() returns False/undetermined for it;
    # recognize SYSTEM by its SID so an MDM/all-users scan still enumerates every
    # profile instead of collapsing to the empty systemprofile.
    if windows_admin_state() is True:
        return True
    return _running_as_local_system()


def _other_user_appdata_local_dirs() -> List[Path]:
    """Enumerate ``C:\\Users\\<user>\\AppData\\Local`` for every real user (the
    shared base for the Programs subdir and for Squirrel direct installs that
    live directly under ``AppData\\Local\\<name>``). Skips well-known service /
    template accounts. Never raises — directory enumeration is wrapped."""
    local_roots: List[Path] = []
    users_dir = Path("C:\\Users")
    try:
        if not users_dir.exists():
            return local_roots
        for user_dir in users_dir.iterdir():
            try:
                if not user_dir.is_dir() or user_dir.name.startswith("."):
                    continue
                if user_dir.name.lower() in (
                    "public", "default", "default user", "all users",
                ):
                    continue
                local_roots.append(user_dir / "AppData" / "Local")
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not inspect user dir {user_dir}: {e}")
                continue
    except (PermissionError, OSError) as e:
        logger.debug(f"Could not enumerate C:\\Users: {e}")
    return local_roots


def other_user_program_dirs() -> List[Path]:
    """Enumerate ``C:\\Users\\<user>\\AppData\\Local\\Programs`` for every real
    user, so a SYSTEM/admin (MDM) scan reaches per-user squirrel installs that
    belong to other users. Skips the well-known service / template accounts.
    Never raises — directory enumeration is wrapped."""
    return [local / "Programs" for local in _other_user_appdata_local_dirs()]


def other_user_local_appdata_dirs() -> List[Path]:
    """Enumerate ``C:\\Users\\<user>\\AppData\\Local`` for every real user, so a
    SYSTEM/admin (MDM) scan reaches Squirrel direct installs that land directly
    under ``AppData\\Local\\<name>`` (e.g. Electron Forge apps like Replit) for
    OTHER users. Skips well-known service / template accounts. Never raises."""
    return _other_user_appdata_local_dirs()


def get_windows_system_directories() -> set:
    """
    Get Windows system directories to skip during file searches.
    
    Returns:
        Set of system directory names
    """
    return {
        'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
        'System Volume Information', '$Recycle.Bin', 'Recovery',
        'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
        'Config.Msi', 'Documents and Settings', 'MSOCache'
    }


def scan_user_directories_for_file(
    file_path_func: Callable[[Path], Path],
    extract_func: Callable[[Path], Optional[Dict]],
    users_dir: Optional[Path] = None
) -> Optional[Dict]:
    """
    Scan all user directories for a file when running as administrator.
    
    Args:
        file_path_func: Function that takes user_home Path and returns file path to check
        extract_func: Function that extracts data from the file path
        users_dir: Optional users directory path (defaults to C:\\Users)
        
    Returns:
        Extracted data dict or None if not found
    """
    if not is_running_as_admin():
        return None
    
    if users_dir is None:
        users_dir = Path("C:\\Users")
    
    if not users_dir.exists():
        return None
    
    for user_dir in users_dir.iterdir():
        if user_dir.is_dir() and not user_dir.name.startswith('.'):
            try:
                file_path = file_path_func(user_dir)
                if file_path.exists():
                    result = extract_func(file_path)
                    if result:
                        return result
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_dir}: {e}")
                continue
            except Exception as e:
                logger.debug(f"Error processing user directory {user_dir}: {e}")
                continue
    
    return None