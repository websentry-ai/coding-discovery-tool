"""
Cursor CLI settings extraction for macOS systems.

Extracts permission settings from:
1. User scope (global): ~/.cursor/cli-config.json
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

from ...constants import MAX_CONFIG_FILE_SIZE
from ...macos_extraction_helpers import (
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
    read_file_content,
    get_top_level_directories,
)

logger = logging.getLogger(__name__)


class MacOSCursorCliSettingsExtractor:
    """Extractor for Cursor CLI settings on macOS systems."""

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

        When running as root, scans all user directories.

        Returns:
            List of settings dicts
        """
        settings_list = []

        def extract_for_user(user_home: Path) -> None:
            config_path = user_home / self.CURSOR_DIR_NAME / self.CLI_CONFIG_FILENAME

            if config_path.exists() and config_path.is_file():
                try:
                    if should_process_file(config_path, user_home):
                        settings_dict = self._parse_settings_file(config_path, "user")
                        if settings_dict:
                            logger.info(f"  ✓ Extracted Cursor CLI settings from {config_path}")
                            settings_list.append(settings_dict)
                except Exception as e:
                    logger.debug(f"Error extracting Cursor CLI settings from {config_path}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

        return settings_list

    def _extract_project_settings(self) -> List[Dict]:
        """
        Extract project settings from **/.cursor/cli.json.

        Skips the global user .cursor directory to avoid duplicates.

        Returns:
            List of settings dicts
        """
        settings_list = []
        root_path = Path("/")

        global_cursor_dirs = set()
        if is_running_as_root():
            def collect_global_dirs(user_home: Path) -> None:
                global_cursor = user_home / self.CURSOR_DIR_NAME
                if global_cursor.exists():
                    global_cursor_dirs.add(global_cursor)
            scan_user_directories(collect_global_dirs)
        else:
            global_cursor = Path.home() / self.CURSOR_DIR_NAME
            if global_cursor.exists():
                global_cursor_dirs.add(global_cursor)

        projects_by_root = {}

        def extract_from_cursor_dir(cursor_dir: Path, projects_by_root: Dict) -> None:
            if cursor_dir in global_cursor_dirs:
                return
            self._extract_settings_from_cursor_directory(cursor_dir, settings_list)

        try:
            if root_path == Path("/"):
                top_level_dirs = get_top_level_directories(root_path)
                for top_dir in top_level_dirs:
                    try:
                        walk_for_tool_directories(
                            root_path, top_dir, self.CURSOR_DIR_NAME, extract_from_cursor_dir,
                            projects_by_root, current_depth=1
                        )
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            else:
                walk_for_tool_directories(
                    root_path, root_path, self.CURSOR_DIR_NAME, extract_from_cursor_dir,
                    projects_by_root, current_depth=0
                )
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            logger.info("Falling back to home directory search for project settings")
            home_path = Path.home()
            walk_for_tool_directories(
                home_path, home_path, self.CURSOR_DIR_NAME, extract_from_cursor_dir,
                projects_by_root, current_depth=0
            )

        return settings_list

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
                if should_process_file(cli_config, cursor_dir.parent):
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
