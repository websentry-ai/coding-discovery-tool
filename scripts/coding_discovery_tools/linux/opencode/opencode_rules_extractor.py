"""OpenCode rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseOpenCodeRulesExtractor
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


def find_opencode_project_root(rule_file: Path) -> Path:
    if ".config/opencode/agent" in str(rule_file):
        return rule_file.parent.parent.parent.parent
    return rule_file.parent.parent.parent


class LinuxOpenCodeRulesExtractor(BaseOpenCodeRulesExtractor):
    """Extractor for OpenCode rules on Linux systems."""

    def extract_all_opencode_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            global_rules_dir = user_home / ".config" / "opencode" / "agent"
            if global_rules_dir.exists() and global_rules_dir.is_dir():
                try:
                    for rule_file in global_rules_dir.glob("*.md"):
                        if should_process_file(rule_file, user_home):
                            rule_info = extract_single_rule_file(rule_file, find_opencode_project_root)
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global OpenCode rules for {user_home}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".opencode",
                    self._extract_rules_from_opencode_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_rules_from_opencode_directory(self, opencode_dir: Path, projects_by_root: Dict) -> None:
        agent_dir = opencode_dir / "agent"
        if not agent_dir.exists() or not agent_dir.is_dir():
            return
        try:
            for rule_file in agent_dir.glob("*.md"):
                if should_process_file(rule_file, opencode_dir.parent):
                    rule_info = extract_single_rule_file(rule_file, find_opencode_project_root)
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {opencode_dir}: {e}")
