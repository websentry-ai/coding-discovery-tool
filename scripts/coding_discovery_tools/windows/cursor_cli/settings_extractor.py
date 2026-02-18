"""
Cursor CLI settings extraction for Windows systems.

Extracts permission settings from:
1. User scope (global): ~/.cursor/cli-config.json (C:\\Users\\<user>\\.cursor\\cli-config.json)
2. Project scope: **/.cursor/cli.json

Permission format:
{
  "permissions": {
    "allow": ["Shell(npm *)", "Read(*.ts)"],
    "deny": ["Shell(rm *)"]
  }
}

Permission types:
- Shell(commandBase *) - shell command execution
- Read(pathOrGlob) - file read access
- Write(pathOrGlob) - file write access
- WebFetch(domainOrPattern) - web fetch access
- Mcp(server:tool) - MCP tool access
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict

from ...constants import MAX_CONFIG_FILE_SIZE, MAX_SEARCH_DEPTH, SKIP_DIRS
from ...windows_extraction_helpers import (
    is_running_as_admin,
    read_file_content,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)

WINDOWS_SYSTEM_DIRS = {"Default", "Default User", "Public", "All Users", "TEMP"}


class WindowsCursorCliSettingsExtractor:
    """Extractor for Cursor CLI settings on Windows systems."""

    CLI_CONFIG_FILENAME = "cli-config.json"
    PROJECT_CONFIG_FILENAME = "cli.json"
    CURSOR_DIR_NAME = ".cursor"

    def extract_settings(self) -> Optional[List[Dict]]:
        """
        Extract Cursor CLI permission settings from all sources.

        Returns:
            List of settings dicts or None if no settings found
        """
        all_settings = []

        user_settings = self._extract_user_settings()
        if user_settings:
            all_settings.extend(user_settings)

        project_settings = self._extract_project_settings()
        if project_settings:
            all_settings.extend(project_settings)

        return all_settings if all_settings else None

    def _extract_user_settings(self) -> List[Dict]:
        """
        Extract user settings from ~/.cursor/cli-config.json.

        When running as admin, scans all user directories.

        Returns:
            List of settings dicts
        """
        settings_list = []

        def extract_for_user(user_home: Path) -> None:
            config_path = user_home / self.CURSOR_DIR_NAME / self.CLI_CONFIG_FILENAME

            if config_path.exists() and config_path.is_file():
                try:
                    settings_dict = self._parse_settings_file(config_path, "user")
                    if settings_dict:
                        logger.info(f"  ✓ Extracted Cursor CLI settings from {config_path}")
                        settings_list.append(settings_dict)
                except Exception as e:
                    logger.debug(f"Error extracting Cursor CLI settings from {config_path}: {e}")

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if (user_dir.is_dir()
                        and not user_dir.name.startswith(".")
                        and user_dir.name not in WINDOWS_SYSTEM_DIRS):
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            extract_for_user(Path.home())

        return settings_list

    def _extract_project_settings(self) -> List[Dict]:
        """
        Extract project settings from **/.cursor/cli.json.

        Returns:
            List of settings dicts
        """
        settings_list = []

        global_cursor_dirs = set()
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and user_dir.name not in WINDOWS_SYSTEM_DIRS:
                        global_cursor = user_dir / self.CURSOR_DIR_NAME
                        if global_cursor.exists():
                            global_cursor_dirs.add(global_cursor)
        else:
            global_cursor = Path.home() / self.CURSOR_DIR_NAME
            if global_cursor.exists():
                global_cursor_dirs.add(global_cursor)

        search_roots = self._get_search_roots()
        system_dirs = get_windows_system_directories()

        for search_root in search_roots:
            try:
                self._walk_for_cursor_cli_settings(
                    search_root, settings_list, global_cursor_dirs, system_dirs
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Error searching {search_root}: {e}")
                continue

        return settings_list

    def _get_search_roots(self) -> List[Path]:
        """Get list of root directories to search for project settings."""
        roots = []

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if (user_dir.is_dir()
                        and not user_dir.name.startswith(".")
                        and user_dir.name not in WINDOWS_SYSTEM_DIRS):
                        roots.append(user_dir)
        else:
            roots.append(Path.home())

        return roots

    def _walk_for_cursor_cli_settings(
        self,
        root: Path,
        settings_list: List[Dict],
        global_cursor_dirs: set,
        system_dirs: set,
        current_depth: int = 0
    ) -> None:
        """
        Walk directory tree looking for .cursor/cli.json files.

        Args:
            root: Directory to start walking from
            settings_list: List to populate with settings
            global_cursor_dirs: Set of global .cursor directories to skip
            system_dirs: Set of system directories to skip
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for entry in root.iterdir():
                if not entry.is_dir():
                    continue

                if entry.name in SKIP_DIRS or entry.name in system_dirs:
                    continue

                if entry.name == self.CURSOR_DIR_NAME:
                    if entry not in global_cursor_dirs:
                        self._extract_settings_from_cursor_directory(entry, settings_list)
                else:
                    self._walk_for_cursor_cli_settings(
                        entry, settings_list, global_cursor_dirs,
                        system_dirs, current_depth + 1
                    )
        except (PermissionError, OSError) as e:
            logger.debug(f"Skipping directory {root}: {e}")

    def _extract_settings_from_cursor_directory(
        self, cursor_dir: Path, settings_list: List[Dict]
    ) -> None:
        """
        Extract settings from a .cursor directory.

        Args:
            cursor_dir: Path to .cursor directory
            settings_list: List to populate with settings
        """
        cli_config = cursor_dir / self.PROJECT_CONFIG_FILENAME
        if cli_config.exists() and cli_config.is_file():
            try:
                settings_dict = self._parse_settings_file(cli_config, "project")
                if settings_dict:
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting settings from {cli_config}: {e}")

    def _parse_settings_file(self, settings_path: Path, scope: str) -> Optional[Dict]:
        """
        Parse a Cursor CLI config file and extract permission settings.

        Args:
            settings_path: Path to the config file
            scope: Scope type ("user" or "project")

        Returns:
            Settings dict or None if parsing fails
        """
        try:
            if not settings_path.exists() or not settings_path.is_file():
                return None

            file_size = settings_path.stat().st_size
            content, truncated = read_file_content(settings_path, file_size)

            if truncated:
                logger.warning(f"Settings file {settings_path} was truncated due to size limit")

            try:
                settings_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in settings file {settings_path}: {e}")
                return None

            permissions = settings_data.get("permissions", {})
            allow_rules = permissions.get("allow", [])
            deny_rules = permissions.get("deny", [])

            settings_dict = {
                "tool_name": "Cursor CLI",
                "settings_source": "user" if scope == "user" else scope,
                "scope": scope,
                "settings_path": str(settings_path),
                "raw_settings": settings_data,
            }

            if allow_rules:
                settings_dict["allow_rules"] = allow_rules
            if deny_rules:
                settings_dict["deny_rules"] = deny_rules

            has_rules = bool(allow_rules or deny_rules)
            if has_rules:
                settings_dict["permission_mode"] = "default"
            else:
                settings_dict["permission_mode"] = "ask"

            settings_dict["sandbox_enabled"] = None

            return settings_dict

        except PermissionError as e:
            logger.warning(f"Permission denied reading settings file {settings_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading settings file {settings_path}: {e}")
            return None
