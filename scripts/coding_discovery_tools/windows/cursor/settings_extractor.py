"""Cursor IDE settings extraction for Windows systems."""

import logging
from pathlib import Path

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...windows_extraction_helpers import is_running_as_admin

logger = logging.getLogger(__name__)

WINDOWS_SYSTEM_DIRS = {"Default", "Default User", "Public", "All Users", "TEMP"}


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
                        and user_dir.name not in WINDOWS_SYSTEM_DIRS):
                        try:
                            callback(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            callback(Path.home())
