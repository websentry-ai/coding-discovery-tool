"""Roo Code rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseRooRulesExtractor
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


def find_roo_project_root(rule_file: Path) -> Path:
    parent = rule_file.parent
    if parent.name == "rules" or parent.name.startswith("rules-"):
        if parent.parent.name == ".roo":
            return parent.parent.parent
    return parent


class LinuxRooRulesExtractor(BaseRooRulesExtractor):
    """Extractor for Roo Code rules on Linux systems."""

    def extract_all_roo_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            global_roo_dir = user_home / ".roo"
            if not global_roo_dir.exists() or not global_roo_dir.is_dir():
                return
            try:
                for rules_dir in global_roo_dir.iterdir():
                    if not rules_dir.is_dir():
                        continue
                    if rules_dir.name != "rules" and not rules_dir.name.startswith("rules-"):
                        continue
                    for rule_file in rules_dir.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, rules_dir):
                            rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Roo Code rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".roo",
                    self._extract_rules_from_roo_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_roo_directory(self, roo_dir: Path, projects_by_root: Dict) -> None:
        # Skip user-level ~/.roo so user-scope rules aren't misclassified as
        # project-scope — those are handled by _extract_global_rules.
        if is_user_level_tool_dir(roo_dir):
            return
        try:
            for item in roo_dir.iterdir():
                if item.is_dir() and (item.name == "rules" or item.name.startswith("rules-")):
                    for rule_file in item.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, roo_dir.parent):
                            rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {roo_dir}: {e}")
