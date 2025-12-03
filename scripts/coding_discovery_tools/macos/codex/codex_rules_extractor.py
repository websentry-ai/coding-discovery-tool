"""
Codex rules extraction for macOS systems.

Extracts Codex configuration files (config.toml) from:
- Global config: ~/.codex/config.toml (contains rules/execpolicy configuration)
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCodexRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_process_file,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)


def find_codex_project_root(config_file: Path) -> Path:
    """
    Find the project root for a Codex config file.
    
    For Codex, the project root is the user's home directory since
    config.toml is stored in ~/.codex/.
    
    Args:
        config_file: Path to the config.toml file
        
    Returns:
        Path to the project root (user's home directory)
    """
    # Codex config is always in ~/.codex/config.toml
    # So the project root is the user's home directory
    return config_file.parent.parent  # ~/.codex/config.toml -> ~


class MacOSCodexRulesExtractor(BaseCodexRulesExtractor):
    """Extractor for Codex rules on macOS systems."""

    def extract_all_codex_rules(self) -> List[Dict]:
        """
        Extract all Codex rules from all projects on macOS.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Codex rules from ~/.codex/config.toml.
        
        When running as root, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_config_path = user_home / ".codex" / "config.toml"
            
            if global_config_path.exists() and global_config_path.is_file():
                try:
                    if should_process_file(global_config_path, user_home):
                        rule_info = extract_single_rule_file(
                            global_config_path, 
                            find_codex_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Codex rules for {user_home}: {e}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

