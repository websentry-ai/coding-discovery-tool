"""
Shared helper functions for Linux rules/MCP/settings extraction.

Linux-specific overrides for scan_user_directories, is_user_level_tool_dir,
should_skip_system_path, and get_top_level_directories.
All other helpers are re-exported from macos_extraction_helpers unchanged —
they are platform-agnostic (path ops, file reading, project-root detection).
"""

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)

# Linux virtual/system filesystems and package-manager paths to skip when walking from /
# Note: /root is intentionally excluded — it is root's home directory and must be
# scannable when the tool runs as root (e.g. in Docker/CI containers).
_LINUX_SKIP_SYSTEM_DIRS = frozenset({
    '/proc', '/sys', '/dev', '/run', '/boot', '/snap',
    '/lost+found', '/tmp', '/var', '/usr', '/bin',
    '/sbin', '/lib', '/lib32', '/lib64', '/libx32', '/etc',
    '/media', '/mnt', '/srv', '/swapfile',
})

# Re-export all platform-agnostic helpers from macos_extraction_helpers so
# Linux extractors only need to import from this module.
from .macos_extraction_helpers import (  # noqa: F401
    is_running_as_root,
    add_rule_to_project,
    build_project_list,
    extract_and_add_rule,
    extract_single_rule_file,
    get_file_metadata,
    read_file_content,
    should_process_directory,
    should_process_file,
    should_skip_path,
    find_cursor_project_root,
    find_claude_project_root,
    find_windsurf_project_root,
)


# ---------------------------------------------------------------------------
# Linux-specific overrides
# ---------------------------------------------------------------------------

def should_skip_system_path(path: Path) -> bool:
    """Return True for Linux virtual-filesystem and system directories."""
    path_str = str(path)
    return any(path_str == d or path_str.startswith(d + '/') for d in _LINUX_SKIP_SYSTEM_DIRS)


def get_top_level_directories(root_path: Path) -> List[Path]:
    """Get top-level directories from root_path, skipping Linux system dirs."""
    top_level_dirs = []
    try:
        for item in root_path.iterdir():
            try:
                if item.is_dir() and not should_skip_system_path(item):
                    top_level_dirs.append(item)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError) as e:
        logger.warning(f"Cannot list {root_path}: {e}")
    return top_level_dirs


def scan_user_directories(check_func: Callable) -> Optional[Path]:
    """
    Scan /home directory for tool installations when running as root.

    Linux equivalent of the macOS scan_user_directories (which scans /Users).
    When not running as root this returns None — callers fall back to Path.home().

    Args:
        check_func: Receives a user home Path, returns a Path if the tool is
                    found there, or None otherwise.

    Returns:
        First non-None result from check_func, or None.
    """
    if not is_running_as_root():
        return None

    home_dir = Path("/home")

    if home_dir.exists():
        for user_dir in home_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    result = check_func(user_dir)
                    if result:
                        return result
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")

    # Always also check /root itself — root is its own user regardless of /home contents
    root_home = Path("/root")
    if root_home.is_dir():
        try:
            result = check_func(root_home)
            if result:
                return result
        except (PermissionError, OSError) as e:
            logger.debug(f"Skipping {root_home}: {e}")

    return None


def is_user_level_tool_dir(tool_dir: Path) -> bool:
    """
    Return True if tool_dir is directly inside a user home on Linux.

    Patterns:
      /home/<username>/.<tool>  →  parent.parent == /home
      /root/.<tool>             →  parent == /root
    """
    try:
        if tool_dir.parent.parent == Path("/home"):
            return True
        if tool_dir.parent == Path("/root"):
            return True
    except Exception:
        pass
    return False


def walk_for_tool_directories(
    root_path: Path,
    current_dir: Path,
    tool_dir_name: str,
    extract_from_dir_func,
    projects_by_root: Dict,
    current_depth: int = 0,
) -> None:
    """Linux-aware walk: uses Linux should_skip_system_path, not the macOS one.

    The macOS version skips '/home' entirely (it's in macOS SKIP_SYSTEM_DIRS),
    which would silently drop all project-level configs under /home/*.
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return
    try:
        for item in current_dir.iterdir():
            try:
                if should_skip_path(item) or should_skip_system_path(item):
                    continue
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue
                if item.is_dir():
                    if item.name == tool_dir_name:
                        extract_from_dir_func(item, projects_by_root)
                        continue
                    if item.is_symlink():
                        continue
                    walk_for_tool_directories(
                        root_path, item, tool_dir_name,
                        extract_from_dir_func, projects_by_root, current_depth + 1,
                    )
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass


def get_linux_user_homes() -> List[Path]:
    """
    Return all human user home directories to scan.

    When running as root, returns every directory under /home.
    Otherwise returns only Path.home().
    """
    if not is_running_as_root():
        return [Path.home()]

    home_dir = Path("/home")
    if not home_dir.exists():
        return [Path.home()]

    homes: List[Path] = []
    try:
        for user_dir in home_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                homes.append(user_dir)
    except (PermissionError, OSError) as e:
        logger.warning(f"Could not list /home: {e}")

    # /root is root's own home — always include it when running as root
    root_home = Path("/root")
    if root_home.is_dir() and root_home not in homes:
        homes.append(root_home)

    return homes or [Path.home()]
