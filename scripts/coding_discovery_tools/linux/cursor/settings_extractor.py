"""Cursor IDE settings extraction for Linux."""

from pathlib import Path
from typing import Iterable

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_running_as_root,
    should_process_file,
    walk_for_tool_directories,
)

CURSOR_DIR_NAME = ".cursor"
PERMISSIONS_FILENAME = "permissions.json"


class LinuxCursorSettingsExtractor(BaseCursorSettingsExtractor):
    """Extractor for Cursor IDE settings on Linux systems."""

    def _get_db_path(self, user_home: Path = None) -> Path:
        """Return path to Cursor's SQLite settings DB for a user."""
        if user_home is None:
            user_home = Path.home()
        # Cursor on Linux stores settings under ~/.config/Cursor/
        return (
            user_home
            / ".config"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )

    def _scan_users(self, callback) -> None:
        """Iterate user home dirs and invoke callback for each."""
        if is_running_as_root():
            for user_home in get_linux_user_homes():
                try:
                    callback(user_home)
                except Exception:
                    continue
        else:
            callback(Path.home())

    def _get_user_permissions_path(self, user_home: Path) -> Path:
        """Return ~/.cursor/permissions.json for a user."""
        return user_home / CURSOR_DIR_NAME / PERMISSIONS_FILENAME

    def _iter_workspace_permissions_files(self, user_home: Path) -> Iterable[Path]:
        """Yield <workspace>/.cursor/permissions.json paths, skipping the global one."""
        global_cursor = user_home / CURSOR_DIR_NAME
        found = []

        def collect_from_cursor_dir(cursor_dir: Path, _: dict) -> None:
            if cursor_dir == global_cursor:
                return
            perms_file = cursor_dir / PERMISSIONS_FILENAME
            if perms_file.exists() and perms_file.is_file():
                if should_process_file(perms_file, cursor_dir.parent):
                    found.append(perms_file)

        try:
            walk_for_tool_directories(
                user_home, user_home, CURSOR_DIR_NAME,
                collect_from_cursor_dir, {}, current_depth=0,
            )
        except (PermissionError, OSError):
            pass

        return found
