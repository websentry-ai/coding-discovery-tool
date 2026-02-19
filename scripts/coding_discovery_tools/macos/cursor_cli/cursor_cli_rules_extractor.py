"""
Cursor CLI rules extraction for macOS systems.

Extracts Cursor CLI configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.

- user: Global user rules in ~/.cursor/rules/*.mdc
- project: Project-specific rules in .cursor/*.mdc, .cursor/rules/*.mdc, .cursorrules
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorCliRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_cursor_project_root,
    extract_project_level_rules_with_fallback,
    walk_for_tool_directories,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)


class MacOSCursorCliRulesExtractor(BaseCursorCliRulesExtractor):
    """Extractor for Cursor CLI rules on macOS systems."""

    def extract_all_cursor_cli_rules(self) -> List[Dict]:
        """
        Extract all Cursor CLI rules from all projects on macOS.
        """
        projects_by_root = {}

        logger.info("Extracting user-level Cursor CLI rules...")
        self._extract_user_level_rules(projects_by_root)

        root_path = Path("/")

        logger.info(f"Searching for Cursor CLI rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_user_level_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract user-level Cursor CLI rules from ~/.cursor/.
        """
        def extract_for_user(user_home: Path) -> None:
            user_cursor_dir = user_home / ".cursor"

            if not user_cursor_dir.exists() or not user_cursor_dir.is_dir():
                return

            for mdc_file in user_cursor_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                if rule_info:
                    project_root = str(user_home)
                    add_rule_to_project(rule_info, project_root, projects_by_root)

            rules_dir = user_cursor_dir / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for mdc_file in rules_dir.glob("*.mdc"):
                    rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        """
        def walk_for_cursor_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            walk_for_tool_directories(
                root, current, ".cursor", self._extract_rules_from_cursor_directory,
                projects, current_depth
            )

        extract_project_level_rules_with_fallback(
            root_path,
            ".cursor",
            self._extract_rules_from_cursor_directory,
            walk_for_cursor_dirs,
            projects_by_root
        )

    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .cursor directory (project scope).
        """
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
