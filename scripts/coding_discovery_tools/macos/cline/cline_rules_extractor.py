"""
Cline rules extraction for macOS systems.

Extracts Cline configuration files from .clinerules directories and global rules
on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClineRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    extract_project_level_rules_with_fallback,
    should_process_file,
    walk_for_tool_directories,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)


def find_cline_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Cline rule file.

    For Cline rules:
    - Files in .clinerules/ directory -> parent of .clinerules (project root)
    - Global rules -> home directory

    Args:
        rule_file: Path to the rule file

    Returns:
        Project root path
    """
    parent = rule_file.parent

    # Case 1: File is in .clinerules directory (workspace rules)
    if parent.name == ".clinerules":
        return parent.parent

    # Case 2: Global rules (in ~/Documents/Cline/Rules or ~/Cline/Rules)
    # Return the directory containing the Rules folder as project root
    if parent.name == "Rules":
        # Check if it's in Documents/Cline or just Cline
        if parent.parent.name == "Cline":
            return parent.parent.parent  # ~/Documents or ~

    # Default: return parent directory
    return parent


class MacOSClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on macOS systems."""

    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on macOS.

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")

        logger.info(f"Searching for Cline rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Cline rules from ~/Documents/Cline/Rules or ~/Cline/Rules.

        When running as root, scans all user directories.

        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            # Primary location: ~/Documents/Cline/Rules
            global_rules_path = user_home / "Documents" / "Cline" / "Rules"

            # Fallback location: ~/Cline/Rules
            if not global_rules_path.exists():
                global_rules_path = user_home / "Cline" / "Rules"

            if global_rules_path.exists() and global_rules_path.is_dir():
                try:
                    for rule_file in global_rules_path.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, global_rules_path):
                            rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Cline rules for {user_home}: {e}")

        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.

        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_cline_dirs(root, current, projects, current_depth=0):
            walk_for_tool_directories(
                root, current, ".clinerules", self._extract_rules_from_clinerules_directory,
                projects, current_depth
            )

        extract_project_level_rules_with_fallback(
            root_path, ".clinerules", self._extract_rules_from_clinerules_directory,
            walk_for_cline_dirs, projects_by_root
        )

    def _extract_rules_from_clinerules_directory(
        self, clinerules_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .clinerules directory.

        Args:
            clinerules_dir: Path to .clinerules directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        for rule_file in clinerules_dir.glob("*.md"):
            if rule_file.is_file() and should_process_file(rule_file, clinerules_dir.parent):
                rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
