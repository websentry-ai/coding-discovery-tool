"""
Antigravity rules extraction for macOS systems.

Extracts Antigravity configuration files from:
- Project-level: .agent/rules/*.md
- Global-level: ~/.gemini/GEMINI.md
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseAntigravityRulesExtractor
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


class MacOSAntigravityRulesExtractor(BaseAntigravityRulesExtractor):
    """Extractor for Antigravity rules on macOS systems."""

    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects on macOS.
        
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
                rule_info = extract_single_rule_file(global_rules_path, find_antigravity_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Antigravity rules: {e}")
        
        # When running as root, also check all user directories
        from ...macos_extraction_helpers import is_running_as_root
        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        user_global_rules = user_dir / ".gemini" / "GEMINI.md"
                        if user_global_rules.exists() and user_global_rules.is_file():
                            try:
                                rule_info = extract_single_rule_file(user_global_rules, find_antigravity_project_root)
                                if rule_info:
                                    project_root = rule_info.get('project_root')
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                            except Exception as e:
                                logger.debug(f"Error extracting global Antigravity rules for {user_dir}: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_agent_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            """Wrapper to use shared walk helper with tool-specific extraction."""
            walk_for_tool_directories(
                root, current, ".agent", self._extract_rules_from_agent_directory,
                projects, current_depth
            )
        
        extract_project_level_rules_with_fallback(
            root_path,
            ".agent",
            self._extract_rules_from_agent_directory,
            walk_for_agent_dirs,
            projects_by_root
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
                if rule_file.is_file() and should_process_file(rule_file, agent_dir.parent):
                    rule_info = extract_single_rule_file(rule_file, find_antigravity_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

