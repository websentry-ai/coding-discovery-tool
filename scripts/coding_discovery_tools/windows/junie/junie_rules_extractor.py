"""
Junie rules extraction for Windows systems.

Extracts Junie rules from .md files:
- Global rules: %USERPROFILE%\\.junie\\*.md
- Project-level rules: <project>\\.junie\\*.md
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseJunieRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    is_user_level_tool_dir,
    scan_windows_user_directories,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

JUNIE_DIR_NAME = ".junie"


def find_junie_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a Junie rule file.

    - Rules in project\\.junie\\*.md -> parent of .junie is project root
    - Global rules in ~\\.junie\\*.md -> home directory is project root
    """
    parent = rule_file.parent

    if parent.name == JUNIE_DIR_NAME:
        return parent.parent

    return parent


class WindowsJunieRulesExtractor(BaseJunieRulesExtractor):
    """Extractor for Junie rules on Windows systems."""

    def extract_all_junie_rules(self) -> List[Dict]:
        """Extract all Junie rules from all projects on Windows."""
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path(Path.home().anchor)  # e.g. "C:\\"
        logger.info(f"Searching for Junie rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract global Junie rules from ~\\.junie\\, scanning all users when admin.

        Uses the shared scan_windows_user_directories helper, which centralises
        the admin/non-admin branching, excludes system/default accounts
        (Public, Default, etc.), and handles PermissionError.
        """
        def extract_for_user(user_home: Path) -> None:
            junie_dir = user_home / JUNIE_DIR_NAME
            if not junie_dir.exists() or not junie_dir.is_dir():
                return
            try:
                for md_file in junie_dir.glob("*.md"):
                    if md_file.is_file() and not should_skip_path(md_file, set()):
                        rule_info = extract_single_rule_file(
                            md_file, find_junie_project_root, scope="user"
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Junie rules for {user_home}: {e}")

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Find project-level .junie directories via the shared directory index.

        Routes through the shared single-pass index so every tool reuses ONE
        memoized walk of the drive instead of each re-walking it independently.
        """
        walk_for_tool_directories(
            root_path, root_path, JUNIE_DIR_NAME,
            self._extract_project_junie_dir, projects_by_root,
        )

    def _extract_project_junie_dir(self, junie_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract a matched .junie directory, skipping the user-level one."""
        # Skip user-level ~\.junie — handled by _extract_global_rules.
        if is_user_level_tool_dir(junie_dir):
            return
        self._extract_junie_dir_rules(junie_dir, projects_by_root)

    def _extract_junie_dir_rules(self, junie_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract all .md files from a project-level .junie directory."""
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
