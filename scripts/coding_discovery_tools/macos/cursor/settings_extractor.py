"""Cursor IDE settings extraction for macOS systems."""

from pathlib import Path

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories


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
