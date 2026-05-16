"""Cursor IDE settings extraction for Linux."""

from pathlib import Path

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...linux_extraction_helpers import get_linux_user_homes, is_running_as_root


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
