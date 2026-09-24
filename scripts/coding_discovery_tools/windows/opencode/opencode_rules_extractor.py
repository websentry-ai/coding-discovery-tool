r"""
OpenCode rules extraction for Windows systems.

Extracts OpenCode configuration files from:
- Global rules: %APPDATA%\.config\opencode\agent\*.md (AppData\Roaming\.config\opencode\agent\*.md)
- Project-level rules: **\.opencode\agent\*.md (recursive)
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseOpenCodeRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_opencode_project_root(rule_file: Path) -> Path:
    r"""
    Find the project root for an OpenCode rule file.
    
    For global rules: AppData\Roaming\.config\opencode\agent\*.md -> home directory
    For project rules: <project>\.opencode\agent\*.md -> <project>
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Path to the project root
    """
    parent = rule_file.parent
    
    # Check if it's a global rule (in .config/opencode/agent/)
    # Path structure: <home>\AppData\Roaming\.config\opencode\agent\*.md
    if parent.name == "agent":
        parent2 = parent.parent
        if parent2.name == "opencode" and parent2.parent.name == ".config":
            # This is a global rule - go up to home directory
            # agent -> opencode -> .config -> Roaming -> AppData -> home
            return parent2.parent.parent.parent.parent
    
    # Project-level rule: go up 2 levels: agent -> .opencode -> project
    if parent.name == "agent" and parent.parent.name == ".opencode":
        return parent.parent.parent
    
    # Fallback: return parent directory
    return parent


class WindowsOpenCodeRulesExtractor(BaseOpenCodeRulesExtractor):
    """Extractor for OpenCode rules on Windows systems."""

    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects on Windows.
        
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
        
        logger.info(f"Searching for OpenCode rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        r"""
        Extract global OpenCode rules from AppData\Roaming\.config\opencode\agent\*.md.
        
        When running as administrator, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_rules_dir = user_home / "AppData" / "Roaming" / ".config" / "opencode" / "agent"
            
            if global_rules_dir.exists() and global_rules_dir.is_dir():
                try:
                    # Check if directory should be processed
                    if not should_skip_path(global_rules_dir):
                        # Find all .md files in the agent directory
                        for rule_file in global_rules_dir.glob("*.md"):
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
        
        # When running as administrator, scan all user directories
        if self._is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Searches for .opencode/agent/*.md files in all projects.
        
        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Route through the shared single-pass directory index so every tool reuses
        # ONE memoized walk of the drive instead of each re-walking it independently.
        walk_for_tool_directories(
            root_path, root_path, ".opencode",
            self._extract_rules_from_opencode_directory, projects_by_root,
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
                if not should_skip_path(rule_file):
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

    def _is_running_as_admin(self) -> bool:
        """
        Check if the current process is running as administrator.
        
        Returns:
            True if running as administrator, False otherwise
        """
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                return current_user in ["administrator", "system"]
            except Exception:
                return False

