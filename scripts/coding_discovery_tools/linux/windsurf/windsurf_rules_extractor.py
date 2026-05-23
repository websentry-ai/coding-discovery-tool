"""Windsurf rules extraction for Linux."""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseWindsurfRulesExtractor
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_windsurf_project_root,
    get_linux_user_homes,
    is_user_level_tool_dir,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

WINDSURF_DIR_NAME = ".windsurf"


class LinuxWindsurfRulesExtractor(BaseWindsurfRulesExtractor):
    """Extractor for Windsurf rules on Linux systems."""

    def extract_all_windsurf_rules(self) -> List[Dict]:
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(
        self, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        def extract_for_user(user_home: Path) -> None:
            # Windsurf global rules live at ~/codeium/.windsurf/memories/global_rules.md
            global_rules_path = (
                user_home / "codeium" / ".windsurf" / "memories" / "global_rules.md"
            )
            if global_rules_path.exists() and global_rules_path.is_file():
                try:
                    if should_process_file(global_rules_path, user_home):
                        rule_info = extract_single_rule_file(
                            global_rules_path, find_windsurf_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get("project_root")
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Windsurf rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(
        self, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, WINDSURF_DIR_NAME,
                    self._extract_rules_from_windsurf_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_windsurf_directory(
        self, windsurf_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        # Skip user-level ~/.windsurf so its rules aren't misclassified as
        # project-scope — user-scope rules live at ~/codeium/.windsurf/ and
        # are handled by _extract_global_rules.
        if is_user_level_tool_dir(windsurf_dir):
            return
        rules_dir = windsurf_dir / "rules"
        if not rules_dir.exists() or not rules_dir.is_dir():
            return
        try:
            for rule_file in rules_dir.iterdir():
                if rule_file.is_file() and should_process_file(rule_file, windsurf_dir.parent):
                    rule_info = extract_single_rule_file(rule_file, find_windsurf_project_root)
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting Windsurf rules from {rules_dir}: {e}")
