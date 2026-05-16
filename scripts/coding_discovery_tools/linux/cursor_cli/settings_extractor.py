"""Cursor CLI settings extraction for Linux systems."""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict

from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
    read_file_content,
)

logger = logging.getLogger(__name__)


class LinuxCursorCliSettingsExtractor:
    """Extractor for Cursor CLI settings on Linux systems."""

    CLI_CONFIG_FILENAME = "cli-config.json"
    PROJECT_CONFIG_FILENAME = "cli.json"
    CURSOR_DIR_NAME = ".cursor"

    def extract_settings(self) -> Optional[List[Dict]]:
        all_settings = []

        user_settings = self._extract_user_settings()
        if user_settings:
            all_settings.extend(user_settings)

        project_settings = self._extract_project_settings()
        if project_settings:
            all_settings.extend(project_settings)

        return all_settings if all_settings else None

    def _extract_user_settings(self) -> List[Dict]:
        settings_list = []

        def extract_for_user(user_home: Path) -> None:
            config_path = user_home / self.CURSOR_DIR_NAME / self.CLI_CONFIG_FILENAME
            if config_path.exists() and config_path.is_file():
                try:
                    if should_process_file(config_path, user_home):
                        settings_dict = self._parse_settings_file(config_path, "user")
                        if settings_dict:
                            settings_list.append(settings_dict)
                except Exception as e:
                    logger.debug(f"Error extracting Cursor CLI settings from {config_path}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

        return settings_list

    def _extract_project_settings(self) -> List[Dict]:
        settings_list = []

        global_cursor_dirs = set()
        for user_home in get_linux_user_homes():
            global_cursor = user_home / self.CURSOR_DIR_NAME
            if global_cursor.exists():
                global_cursor_dirs.add(global_cursor)

        def extract_from_cursor_dir(cursor_dir: Path, _: Dict) -> None:
            if cursor_dir in global_cursor_dirs:
                return
            self._extract_settings_from_cursor_directory(cursor_dir, settings_list)

        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, self.CURSOR_DIR_NAME,
                    extract_from_cursor_dir, {}, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return settings_list

    def _extract_settings_from_cursor_directory(
        self, cursor_dir: Path, settings_list: List[Dict]
    ) -> None:
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
            settings_dict["permission_mode"] = "default" if has_rules else "ask"
            settings_dict["sandbox_enabled"] = None

            return settings_dict

        except PermissionError as e:
            logger.warning(f"Permission denied reading settings file {settings_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading settings file {settings_path}: {e}")
            return None
