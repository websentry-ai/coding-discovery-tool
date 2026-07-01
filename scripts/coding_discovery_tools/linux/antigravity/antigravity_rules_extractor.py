"""Antigravity rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseAntigravityRulesExtractor
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_linux_user_homes,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)


def find_antigravity_project_root(rule_file: Path) -> Path:
    parent = rule_file.parent
    if parent.name == "rules" and parent.parent.name == ".agent":
        return parent.parent.parent
    if parent.name == ".agent":
        return parent.parent
    if parent.name == ".gemini" and rule_file.name.upper() == "GEMINI.MD":
        return parent.parent
    return parent


class LinuxAntigravityRulesExtractor(BaseAntigravityRulesExtractor):
    """Extractor for Antigravity rules on Linux systems."""

    def extract_all_antigravity_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            global_rules_path = user_home / ".gemini" / "GEMINI.md"
            if global_rules_path.exists() and global_rules_path.is_file():
                try:
                    if should_process_file(global_rules_path, user_home):
                        rule_info = extract_single_rule_file(
                            global_rules_path, find_antigravity_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get("project_root")
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Antigravity rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".agent",
                    self._extract_rules_from_agent_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_agent_directory(self, agent_dir: Path, projects_by_root: Dict) -> None:
        rules_dir = agent_dir / "rules"
        if not rules_dir.exists() or not rules_dir.is_dir():
            return
        for rule_file in rules_dir.glob("*.md"):
            if rule_file.is_file() and should_process_file(rule_file, agent_dir.parent):
                rule_info = extract_single_rule_file(rule_file, find_antigravity_project_root)
                if rule_info:
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
