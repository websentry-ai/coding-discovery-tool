"""
Windsurf rules extraction for Windows systems.

Extracts Windsurf configuration files from .windsurf/rules directories
on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseWindsurfRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


class WindowsWindsurfRulesExtractor(BaseWindsurfRulesExtractor):
    """Extractor for Windsurf rules on Windows systems."""

    def extract_all_windsurf_rules(self) -> List[Dict]:
        """
        Extract all Windsurf rules from all projects on Windows.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Extract project-level rules from root drive (for MDM deployment)
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        logger.info(f"Searching for Windsurf rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Windsurf rules from ~/.windsurf/global_rules.md.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        global_rules_path = Path.home() / "codeium" / ".windsurf" / "memories" / "global_rules.md"
        if global_rules_path.exists() and global_rules_path.is_file():
            try:
                rule_info = extract_single_rule_file(global_rules_path)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Windsurf rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects using optimized walker.
        
        Uses parallel processing for top-level directories to improve performance.
        
        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Use the shared directory index: the drive is walked once for all
        # tools, not walked again by each tool.
        walk_for_tool_directories(
            root_path, root_path, ".windsurf",
            self._extract_rules_from_windsurf_directory, projects_by_root,
        )

    def _extract_rules_from_windsurf_directory(self, windsurf_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .windsurf directory.
        
        Args:
            windsurf_dir: Path to .windsurf directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract files from .windsurf/rules/ subdirectory
        rules_dir = windsurf_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            # Extract all files from rules directory (typically .md files, but can be any format)
            for rule_file in rules_dir.iterdir():
                if rule_file.is_file():
                    rule_info = extract_single_rule_file(rule_file)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

