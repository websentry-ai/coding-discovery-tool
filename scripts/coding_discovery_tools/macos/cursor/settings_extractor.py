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

    # Security-relevant keys to include in raw_settings
    SECURITY_RELEVANT_KEYS = {
        # Core YOLO/permission mode
        "useYoloMode",
        "defaultMode2",
        "yoloEnableRunEverything",
        # Allow/deny lists
        "yoloCommandAllowlist",
        "yoloCommandDenylist",
        # MCP allowlist (tools that can auto-run)
        "mcpAllowlist",
        # Protection flags (disabled=True means protection OFF)
        "yoloDotFilesDisabled",
        "yoloDeleteFileDisabled",
        "yoloOutsideWorkspaceDisabled",
        "yoloMcpToolsDisabled",
        "playwrightProtection",
        # Auto-run settings
        "fullAutoRun",
        "autoFix",
        "autoApprovedModeTransitions",
        # MCP servers
        "enabledMcpServers",
        # Network access
        "isWebSearchToolEnabled",
        "isWebFetchToolEnabled",
        "webFetchDomainAllowlist",
    }

    # Keys to extract from modes4 entries
    MODE_SECURITY_KEYS = {"autoRun", "toolEnabled", "agentEnabled"}

    def _filter_raw_settings(self, composer_state: Dict) -> Dict:
        """
        Filter composerState to only include security-relevant keys.

        Reduces payload size by ~60% while preserving all risk-relevant data.
        """
        filtered = {}

        for key in self.SECURITY_RELEVANT_KEYS:
            if key in composer_state:
                filtered[key] = composer_state[key]

        # Filter modes4 to only include security-relevant properties
        modes4 = composer_state.get("modes4", [])
        if modes4:
            filtered_modes = []
            for mode in modes4:
                if isinstance(mode, dict):
                    filtered_mode = {"name": mode.get("name", "unknown")}
                    for key in self.MODE_SECURITY_KEYS:
                        if key in mode:
                            filtered_mode[key] = mode[key]
                    filtered_modes.append(filtered_mode)
            if filtered_modes:
                filtered["modes4"] = filtered_modes

        return filtered

    def _parse_composer_state(self, composer_state: Dict, db_path: Path) -> Dict:
        """
        Parse composerState into normalized backend format (same as Claude Code).

        Transforms Cursor-native fields to Claude Code format:
        - useYoloMode → permission_mode ("acceptEdits" or "default")
        - yoloCommandAllowlist → allow_rules with Bash(cmd *) format
        - yoloCommandDenylist → deny_rules with Bash(cmd *) format
        - mcpAllowlist → mcp_tool_allowlist (tools that can auto-run)
        - Protection flags ON → adds deny rules for protected operations
        - enabledMcpServers → mcp_servers and mcp_policies
        - sandbox_enabled → None (Cursor has no native sandbox support)
        """
        use_yolo_mode = composer_state.get("useYoloMode", False)
        permission_mode = "acceptEdits" if use_yolo_mode else "default"

        # Transform allowlist: ["cd", "npx"] → ["Bash(cd *)", "Bash(npx *)"]
        yolo_allowlist = composer_state.get("yoloCommandAllowlist", [])
        allow_rules = [f"Bash({cmd} *)" for cmd in yolo_allowlist]

        # Transform denylist to Bash(cmd *) format
        yolo_denylist = composer_state.get("yoloCommandDenylist", [])
        deny_rules = [f"Bash({cmd} *)" for cmd in yolo_denylist]

        if not composer_state.get("yoloDotFilesDisabled", False):
            deny_rules.extend(["Write(.*)", "Delete(.*)"])

        filtered_raw_settings = self._filter_raw_settings(composer_state)

        backend_settings = {
            "settings_source": "user",
            "scope": "user",
            "settings_path": str(db_path),
            "raw_settings": filtered_raw_settings,
            "permission_mode": permission_mode,
            "sandbox_enabled": None,  # Cursor has no native sandbox support
        }

        if allow_rules:
            backend_settings["allow_rules"] = allow_rules
        if deny_rules:
            backend_settings["deny_rules"] = deny_rules

        # Extract MCP allowlist if present (tools that can auto-run)
        # Format: 'server:tool', 'server:*', '*:tool', or '*:*'
        mcp_allowlist = composer_state.get("mcpAllowlist", [])
        if mcp_allowlist:
            backend_settings["mcp_tool_allowlist"] = mcp_allowlist

        # Extract enabled MCP servers if present
        enabled_mcp = composer_state.get("enabledMcpServers", [])
        if enabled_mcp:
            backend_settings["mcp_servers"] = enabled_mcp
            backend_settings["mcp_policies"] = {
                "allowedMcpServers": enabled_mcp,
                "deniedMcpServers": []
            }

        return backend_settings
