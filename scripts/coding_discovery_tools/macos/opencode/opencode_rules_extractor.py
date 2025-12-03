"""
OpenCode rules extraction for macOS systems.

Extracts OpenCode configuration files from:
- Global rules: ~/.config/opencode/agent/*.md
- Project-level rules: **/.opencode/agent/*.md (recursive)
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseOpenCodeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_process_file,
    is_running_as_root,
    scan_user_directories,
    extract_project_level_rules_with_fallback,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_opencode_project_root(rule_file: Path) -> Path:
    """
    Find the project root for an OpenCode rule file.
    
    For global rules: ~/.config/opencode/agent/*.md -> ~
    For project rules: <project>/.opencode/agent/*.md -> <project>
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Path to the project root
    """
    # Check if it's a global rule (in ~/.config/opencode/agent/)
    if ".config/opencode/agent" in str(rule_file):
        # Go up 3 levels: agent -> opencode -> config -> ~
        return rule_file.parent.parent.parent.parent
    else:
        # Project-level rule: go up 2 levels: agent -> .opencode -> project
        return rule_file.parent.parent.parent


class MacOSOpenCodeRulesExtractor(BaseOpenCodeRulesExtractor):
    """Extractor for OpenCode rules on macOS systems."""

    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects on macOS.
        
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
        
        logger.info(f"Searching for OpenCode rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global OpenCode rules from ~/.config/opencode/agent/*.md.
        
        When running as root, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_rules_dir = user_home / ".config" / "opencode" / "agent"
            
            if global_rules_dir.exists() and global_rules_dir.is_dir():
                try:
                    # Find all .md files in the agent directory
                    for rule_file in global_rules_dir.glob("*.md"):
                        if should_process_file(rule_file, user_home):
                            rule_info = extract_single_rule_file(
                                rule_file,
                                find_opencode_project_root
                            )
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global OpenCode rules for {user_home}: {e}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Searches for .opencode/agent/*.md files in all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_opencode_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            """Wrapper to use shared walk helper with tool-specific extraction."""
            walk_for_tool_directories(
                root, current, ".opencode", self._extract_rules_from_opencode_directory,
                projects, current_depth
            )
        
        extract_project_level_rules_with_fallback(
            root_path,
            ".opencode",
            self._extract_rules_from_opencode_directory,
            walk_for_opencode_dirs,
            projects_by_root
        )

    def _extract_rules_from_opencode_directory(self, opencode_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .opencode directory.
        
        Args:
            opencode_dir: Path to .opencode directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        agent_dir = opencode_dir / "agent"
        
        if not agent_dir.exists() or not agent_dir.is_dir():
            return
        
        try:
            # Find all .md files in the agent directory
            for rule_file in agent_dir.glob("*.md"):
                if should_process_file(rule_file, opencode_dir.parent):
                    rule_info = extract_single_rule_file(
                        rule_file,
                        find_opencode_project_root
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {opencode_dir}: {e}")

