"""
Claude Code rules extraction for Linux
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClaudeRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_claude_project_root,
    should_process_directory
)

logger = logging.getLogger(__name__)


class LinuxClaudeRulesExtractor(BaseClaudeRulesExtractor):
    """Claude Code rules extractor for Linux systems."""

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.

        Returns:
            List of project dicts with rules
        """
        projects_by_root = {}

        # Start from home directory for Linux
        home_path = Path.home()

        logger.info(f"Searching for Claude Code rules from home: {home_path}")
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
        # Search for .clauderules files (current format)
        for rule_file in root_path.rglob(".clauderules"):
            try:
                if not should_process_directory(rule_file.parent, root_path):
                    continue

                rule_info = extract_single_rule_file(rule_file, find_claude_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {rule_file}: {e}")
                continue

        # Search for claude.md files (legacy format)
        for rule_file in root_path.rglob("claude.md"):
            try:
                if not should_process_directory(rule_file.parent, root_path):
                    continue

                rule_info = extract_single_rule_file(rule_file, find_claude_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {rule_file}: {e}")
                continue