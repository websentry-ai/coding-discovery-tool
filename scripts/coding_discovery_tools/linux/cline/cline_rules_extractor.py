"""Cline rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClineRulesExtractor
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_linux_user_homes,
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_cline_project_root(rule_file: Path) -> Path:
    parent = rule_file.parent
    if parent.name == ".clinerules":
        return parent.parent
    if parent.name == "Rules":
        if parent.parent.name == "Cline":
            return parent.parent.parent
    return parent


class LinuxClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on Linux systems."""

    def extract_all_cline_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            global_rules_path = user_home / "Documents" / "Cline" / "Rules"
            if not global_rules_path.exists():
                global_rules_path = user_home / "Cline" / "Rules"

            if global_rules_path.exists() and global_rules_path.is_dir():
                try:
                    for rule_file in global_rules_path.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, global_rules_path):
                            rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Cline rules for {user_home}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".clinerules",
                    self._extract_rules_from_clinerules_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_clinerules_directory(
        self, clinerules_dir: Path, projects_by_root: Dict
    ) -> None:
        for rule_file in clinerules_dir.glob("*.md"):
            if rule_file.is_file() and should_process_file(rule_file, clinerules_dir.parent):
                rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                if rule_info:
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
