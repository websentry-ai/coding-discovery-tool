"""
Cursor rules extraction for Linux
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_cursor_project_root,
    should_process_directory
)

logger = logging.getLogger(__name__)


class LinuxCursorRulesExtractor(BaseCursorRulesExtractor):
    """Cursor rules extractor for Linux systems."""

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.

        Returns:
            List of project dicts with rules
        """
        projects_by_root = {}

        # Start from home directory for Linux
        home_path = Path.home()

        logger.info(f"Searching for Cursor rules from home: {home_path}")
        self._extract_project_level_rules(home_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.

        Args:
            root_path: Root directory to search from
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Search for .cursor directories
        for cursor_dir in root_path.rglob(".cursor"):
            try:
                if not should_process_directory(cursor_dir, root_path):
                    continue

                # Extract rules from this .cursor directory
                self._extract_rules_from_cursor_directory(cursor_dir, projects_by_root)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {cursor_dir}: {e}")
                continue

        # Also search for standalone .cursorrules files
        for cursorrules_file in root_path.rglob(".cursorrules"):
            try:
                if not should_process_directory(cursorrules_file.parent, root_path):
                    continue

                rule_info = extract_single_rule_file(cursorrules_file, find_cursor_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {cursorrules_file}: {e}")
                continue

    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .cursor directory.

        Args:
            cursor_dir: Path to .cursor directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract .mdc files directly from .cursor directory
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        # Also check .cursor/rules/ subdirectory (if it exists)
        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)