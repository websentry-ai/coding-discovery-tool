"""
Kilo Code rules extraction for macOS systems.

Extracts Kilo Code configuration files from .kilocode/rules directories and
global rules directory on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseKiloCodeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    extract_project_level_rules_with_fallback,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_kilocode_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Kilo Code rule file.
    
    For Kilo Code rules:
    - Files in .kilocode/rules/ directory -> parent of .kilocode (project root)
    - Global rules in ~/.kilocode/rules/ -> home directory
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .kilocode/rules directory
    if parent.name == "rules" and parent.parent.name == ".kilocode":
        project_root = parent.parent.parent
        # Case 2: Global rules (in ~/.kilocode/rules/)
        if project_root == Path.home():
            return Path.home()
        # Case 3: Workspace rules (in project/.kilocode/rules/)
        return project_root
    
    # Default: return parent directory
    return parent


class MacOSKiloCodeRulesExtractor(BaseKiloCodeRulesExtractor):
    """Extractor for Kilo Code rules on macOS systems."""

    def extract_all_kilocode_rules(self) -> List[Dict]:
        """
        Extract all Kilo Code rules from all projects on macOS.
        
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
        
        logger.info(f"Searching for Kilo Code rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Kilo Code rules from ~/.kilocode/rules/.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        user_home = Path.home()
        global_rules_path = user_home / ".kilocode" / "rules"
        
        if global_rules_path.exists() and global_rules_path.is_dir():
            try:
                # Extract all .md files from global rules directory
                for rule_file in global_rules_path.glob("*.md"):
                    if rule_file.is_file() and should_process_file(rule_file, global_rules_path):
                        rule_info = extract_single_rule_file(rule_file, find_kilocode_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Kilo Code rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_kilocode_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            """Wrapper to use shared walk helper with tool-specific extraction."""
            walk_for_tool_directories(
                root, current, ".kilocode", self._extract_rules_from_kilocode_directory,
                projects, current_depth
            )
        
        extract_project_level_rules_with_fallback(
            root_path,
            ".kilocode",
            self._extract_rules_from_kilocode_directory,
            walk_for_kilocode_dirs,
            projects_by_root
        )

    def _extract_rules_from_kilocode_directory(
        self, kilocode_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .kilocode directory.
        
        Args:
            kilocode_dir: Path to .kilocode directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract all .md files from .kilocode/rules/ subdirectory
        rules_dir = kilocode_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for rule_file in rules_dir.glob("*.md"):
                if rule_file.is_file() and should_process_file(rule_file, kilocode_dir.parent):
                    rule_info = extract_single_rule_file(rule_file, find_kilocode_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

