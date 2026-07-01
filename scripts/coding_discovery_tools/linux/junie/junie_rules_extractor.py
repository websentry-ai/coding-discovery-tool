"""
Junie rules extraction for Linux systems.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseJunieRulesExtractor
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_linux_user_homes,
    is_user_level_tool_dir,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

JUNIE_DIR_NAME = ".junie"


def find_junie_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a Junie rule file.

    For Junie:
    - Rules in project/.junie/*.md -> parent of .junie is project root
    - Global rules in ~/.junie/*.md -> home directory is project root
    """
    parent = rule_file.parent

    if parent.name == JUNIE_DIR_NAME:
        return parent.parent

    return parent


class LinuxJunieRulesExtractor(BaseJunieRulesExtractor):
    """Extractor for Junie rules on Linux systems."""

    def extract_all_junie_rules(self) -> List[Dict]:
        """Extract all Junie rules from all projects on Linux."""
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract global Junie rules from ~/.junie/ directory."""
        def extract_for_user(user_home: Path) -> None:
            junie_dir = user_home / JUNIE_DIR_NAME

            if not junie_dir.exists() or not junie_dir.is_dir():
                return

            try:
                for md_file in junie_dir.glob("*.md"):
                    if md_file.is_file() and should_process_file(md_file, junie_dir):
                        rule_info = extract_single_rule_file(md_file, find_junie_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Junie rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk each user home recursively for project-level .junie directories."""
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, JUNIE_DIR_NAME,
                    self._extract_junie_dir_rules,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_junie_dir_rules(self, junie_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract all .md files from a project-level .junie directory."""
        # Skip user-level ~/.junie so its rules aren't misclassified as
        # project-scope — those are handled by _extract_global_rules.
        if is_user_level_tool_dir(junie_dir):
            return
        try:
            for md_file in junie_dir.glob("*.md"):
                if md_file.is_file():
                    rule_info = extract_single_rule_file(md_file, find_junie_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {junie_dir}: {e}")
