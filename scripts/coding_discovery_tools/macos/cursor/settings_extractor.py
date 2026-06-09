"""Cursor IDE settings extraction for macOS systems."""

from pathlib import Path
from typing import Iterable

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...macos_extraction_helpers import (
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
    get_top_level_directories,
)

CURSOR_DIR_NAME = ".cursor"
PERMISSIONS_FILENAME = "permissions.json"


class MacOSCursorSettingsExtractor(BaseCursorSettingsExtractor):
    """Extractor for Cursor IDE settings on macOS systems."""

    def _get_db_path(self, user_home: Path = None) -> Path:
        """Get path to state.vscdb for a user."""
        if user_home is None:
            user_home = Path.home()
        return (
            user_home
            / "Library"
            / "Application Support"
            / "Cursor"
            / "User"
            / "globalStorage"
            / "state.vscdb"
        )

    def _scan_users(self, callback) -> None:
        """Scan macOS user directories."""
        if is_running_as_root():
            scan_user_directories(callback)
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

        root_path = Path("/")
        try:
            for top_dir in get_top_level_directories(root_path):
                try:
                    walk_for_tool_directories(
                        root_path, top_dir, CURSOR_DIR_NAME,
                        collect_from_cursor_dir, {}, current_depth=1,
                    )
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            walk_for_tool_directories(
                user_home, user_home, CURSOR_DIR_NAME,
                collect_from_cursor_dir, {}, current_depth=0,
            )

        return found
