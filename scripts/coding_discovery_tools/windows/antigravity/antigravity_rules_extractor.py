"""
Antigravity rules extraction for Windows systems.

Extracts Antigravity configuration files from:
- Project-level: .agent/rules/*.md
- Global-level: ~/.gemini/GEMINI.md
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseAntigravityRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_antigravity_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for an Antigravity rule file.
    
    For Antigravity rules:
    - Files in .agent/rules/ directory -> parent of .agent (project root)
    - Global rules in ~/.gemini/GEMINI.md -> home directory
    
    Args:
        rule_file: Path to the rule file
    
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .agent/rules/ directory (project-level rules)
    if parent.name == "rules" and parent.parent.name == ".agent":
        return parent.parent.parent
    
    # Case 2: File is directly in .agent directory (shouldn't happen per spec, but handle it)
    if parent.name == ".agent":
        return parent.parent
    
    # Case 3: Global rules in ~/.gemini/GEMINI.md
    # Return the .gemini directory's parent (which would be home directory)
    if parent.name == ".gemini" and rule_file.name.upper() == "GEMINI.MD":
        return parent.parent
    
    # Fallback: use the directory containing the file
    return parent


class WindowsAntigravityRulesExtractor(BaseAntigravityRulesExtractor):
    """Extractor for Antigravity rules on Windows systems."""

    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects on Windows.
        
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
        
        logger.info(f"Searching for Antigravity rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Antigravity rules from ~/.gemini/GEMINI.md.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Check current user's home directory
        global_rules_path = Path.home() / ".gemini" / "GEMINI.md"
        if global_rules_path.exists() and global_rules_path.is_file():
            try:
                rule_info = extract_single_rule_file(global_rules_path)
                if rule_info:
                    # Override project_root using our custom function
                    project_root = find_antigravity_project_root(global_rules_path)
                    rule_info['project_root'] = str(project_root)
                    add_rule_to_project(rule_info, str(project_root), projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Antigravity rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.

        Uses the shared directory index so the drive is walked once for all tools,
        not walked again by each tool.

        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        walk_for_tool_directories(
            root_path, root_path, ".agent",
            self._extract_rules_from_agent_directory, projects_by_root,
        )

    def _extract_rules_from_agent_directory(self, agent_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .agent directory.
        
        Args:
            agent_dir: Path to .agent directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract files from .agent/rules/ subdirectory
        rules_dir = agent_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            # Extract all .md files from rules directory
            for rule_file in rules_dir.glob("*.md"):
                if rule_file.is_file():
                    try:
                        rule_info = extract_single_rule_file(rule_file)
                        if rule_info:
                            # Override project_root using our custom function
                            project_root = find_antigravity_project_root(rule_file)
                            rule_info['project_root'] = str(project_root)
                            add_rule_to_project(rule_info, str(project_root), projects_by_root)
                    except Exception as e:
                        logger.debug(f"Error extracting rule from {rule_file}: {e}")

