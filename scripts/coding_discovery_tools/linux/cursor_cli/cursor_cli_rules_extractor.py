"""Cursor CLI rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorCliRulesExtractor
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_cursor_project_root,
    get_linux_user_homes,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


class LinuxCursorCliRulesExtractor(BaseCursorCliRulesExtractor):
    """Extractor for Cursor CLI rules on Linux systems."""

    def extract_all_cursor_cli_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_user_level_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_user_level_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            user_cursor_dir = user_home / ".cursor"
            if not user_cursor_dir.exists() or not user_cursor_dir.is_dir():
                return

            for mdc_file in user_cursor_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                if rule_info:
                    add_rule_to_project(rule_info, str(user_home), projects_by_root)

            rules_dir = user_cursor_dir / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for mdc_file in rules_dir.glob("*.mdc"):
                    rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                    if rule_info:
                        add_rule_to_project(rule_info, str(user_home), projects_by_root)

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".cursor",
                    self._extract_rules_from_cursor_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict) -> None:
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get("project_root")
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get("project_root")
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
