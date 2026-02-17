"""
Cursor IDE settings extraction for macOS systems.

Extracts permission settings from SQLite database:
- ~/Library/Application Support/Cursor/User/globalStorage/state.vscdb

Settings are stored in ItemTable as JSON under the key:
'src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser'

"""

import json
import logging
import sqlite3
import shutil
import tempfile
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseCursorSettingsExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


class MacOSCursorSettingsExtractor(BaseCursorSettingsExtractor):
    """Extractor for Cursor IDE settings on macOS systems."""

    # Database key containing composerState
    STORAGE_KEY = "src.vs.platform.reactivestorage.browser.reactiveStorageServiceImpl.persistentStorage.applicationUser"

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

    def extract_settings(self) -> Optional[Dict]:
        """
        Extract Cursor IDE permission settings from SQLite database.

        Returns:
            Dict with Cursor settings or None if not found
        """
        settings_list = []

        def extract_for_user(user_home: Path) -> None:
            """Extract settings for a specific user."""
            db_path = self._get_db_path(user_home)

            if not db_path.exists():
                logger.debug(f"Cursor database not found at: {db_path}")
                return

            try:
                settings_dict = self._extract_from_database(db_path)
                if settings_dict:
                    logger.info(f"  ✓ Successfully extracted Cursor settings from {db_path}")
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.error(
                    f"Error extracting Cursor settings from {db_path}: {e}",
                    exc_info=True,
                )

        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

        # Return first found settings (or None)
        return settings_list[0] if settings_list else None

    def _extract_from_database(self, db_path: Path) -> Optional[Dict]:
        """
        Extract composerState from the SQLite database.

        Creates a temporary copy to avoid database locking issues when Cursor is running.
        """
        temp_db_path = None
        try:
            # Copy database to temp location (handles database locks)
            with tempfile.NamedTemporaryFile(
                suffix=".vscdb", delete=False
            ) as temp_db:
                temp_db_path = temp_db.name

            shutil.copy2(db_path, temp_db_path)

            with sqlite3.connect(temp_db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT value FROM ItemTable WHERE key = ?", (self.STORAGE_KEY,)
                )
                row = cursor.fetchone()

            if not row:
                logger.debug(f"No settings found in database at: {db_path}")
                return None

            # Parse JSON value
            try:
                storage_data = json.loads(row[0])
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in Cursor settings: {e}")
                return None

            composer_state = storage_data.get("composerState", {})
            if not composer_state:
                logger.debug("No composerState found in storage data")
                return None

            return self._parse_composer_state(composer_state, db_path)

        except sqlite3.Error as e:
            logger.warning(f"SQLite error reading {db_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading Cursor database {db_path}: {e}")
            return None
        finally:
            # Clean up temp file
            if temp_db_path:
                try:
                    Path(temp_db_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _parse_composer_state(self, composer_state: Dict, db_path: Path) -> Dict:
        """
        Parse composerState into backend-ready format.

        Note: Protection flags in Cursor use "Disabled" suffix, meaning:
        - yoloDotFilesDisabled=True means dotfiles protection is OFF
        - yoloDotFilesDisabled=False means dotfiles protection is ON
        So we invert the boolean for clarity (protection: True = enabled).

        Returns dict in backend format (same structure as Claude Code):
        - settings_source, scope: Always "user" (Cursor only has global settings)
        - settings_path: Path to the database
        - raw_settings: Full composerState JSON
        - Cursor-native permission fields (flat structure)
        """
        mcp_allowed_tools = composer_state.get("mcpAllowedTools", [])

        backend_settings = {
            # Common metadata (same structure as Claude Code)
            "settings_source": "user",
            "scope": "user",
            "settings_path": str(db_path),
            "raw_settings": composer_state,

            # Cursor-native permission fields (flat structure)
            "yolo_mode_enabled": composer_state.get("useYoloMode", False),
            "yolo_run_everything": composer_state.get("yoloEnableRunEverything", False),

            # Protection flags (inverted from "Disabled" suffix)
            "dotfiles_protection": not composer_state.get("yoloDotFilesDisabled", False),
            "delete_file_protection": not composer_state.get("yoloDeleteFileDisabled", False),
            "outside_workspace_protection": not composer_state.get("yoloOutsideWorkspaceDisabled", False),
            "mcp_tools_protection": not composer_state.get("yoloMcpToolsDisabled", False),
        }

        # Only include lists if non-empty
        yolo_allowlist = composer_state.get("yoloCommandAllowlist", [])
        if yolo_allowlist:
            backend_settings["yolo_allowlist"] = yolo_allowlist

        yolo_denylist = composer_state.get("yoloCommandDenylist", [])
        if yolo_denylist:
            backend_settings["yolo_denylist"] = yolo_denylist

        # MCP (only if present)
        if mcp_allowed_tools:
            backend_settings["mcp_servers"] = mcp_allowed_tools
            backend_settings["mcp_policies"] = {
                "allowedMcpServers": mcp_allowed_tools,
                "deniedMcpServers": []
            }

        return backend_settings
