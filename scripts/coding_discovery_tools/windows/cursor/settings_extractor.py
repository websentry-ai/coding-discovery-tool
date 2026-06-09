"""Cursor IDE settings extraction for Windows systems."""

import logging
from pathlib import Path
from typing import Iterable, List

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...constants import MAX_SEARCH_DEPTH, SKIP_DIRS, WINDOWS_SKIP_USER_DIRS
from ...windows_extraction_helpers import (
    is_running_as_admin,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)

CURSOR_DIR_NAME = ".cursor"
PERMISSIONS_FILENAME = "permissions.json"


class WindowsCursorSettingsExtractor(BaseCursorSettingsExtractor):
    """Extractor for Cursor IDE settings on Windows systems."""

    def _get_db_path(self, user_home: Path = None) -> Path:
        """Get path to state.vscdb for a user."""
        if user_home is None:
            user_home = Path.home()
        return (
            user_home
            / "AppData"
            / "Roaming"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )

    def _scan_users(self, callback) -> None:
        """Scan Windows user directories, filtering out system directories."""
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if (user_dir.is_dir()
                        and not user_dir.name.startswith(".")
                        and user_dir.name not in WINDOWS_SKIP_USER_DIRS):
                        try:
                            callback(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            callback(Path.home())

    def _get_user_permissions_path(self, user_home: Path) -> Path:
        """Return ~/.cursor/permissions.json for a user (under home, not AppData)."""
        return user_home / CURSOR_DIR_NAME / PERMISSIONS_FILENAME

    def _iter_workspace_permissions_files(self, user_home: Path) -> Iterable[Path]:
        """Yield <workspace>/.cursor/permissions.json paths, skipping the global one."""
        global_cursor = user_home / CURSOR_DIR_NAME
        system_dirs = get_windows_system_directories()
        found: List[Path] = []

        self._walk_for_permissions(user_home, global_cursor, system_dirs, found)

        return found

    def _walk_for_permissions(
        self,
        root: Path,
        global_cursor: Path,
        system_dirs: set,
        found: List[Path],
        current_depth: int = 0,
    ) -> None:
        """Walk directory tree looking for .cursor/permissions.json files."""
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue

                if entry.name in SKIP_DIRS or entry.name in system_dirs:
                    continue

                if entry.name == CURSOR_DIR_NAME:
                    if entry != global_cursor:
                        perms_file = entry / PERMISSIONS_FILENAME
                        if perms_file.exists() and perms_file.is_file():
                            found.append(perms_file)
                else:
                    if entry.is_symlink():
                        continue
                    self._walk_for_permissions(
                        entry, global_cursor, system_dirs, found, current_depth + 1
                    )
        except (PermissionError, OSError) as e:
            logger.debug(f"Skipping directory {root}: {e}")
