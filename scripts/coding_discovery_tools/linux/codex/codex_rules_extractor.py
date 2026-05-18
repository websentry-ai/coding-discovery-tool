"""Codex rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCodexRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_linux_user_homes,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

AGENTS_MD = "AGENTS.md"
AGENTS_OVERRIDE_MD = "AGENTS.override.md"


def find_codex_project_root(agents_file: Path) -> Path:
    parent = agents_file.parent
    if parent.name == ".codex":
        return parent.parent
    return parent


class LinuxCodexRulesExtractor(BaseCodexRulesExtractor):
    """Extractor for Codex rules on Linux systems."""

    def extract_all_codex_rules(self) -> List[Dict]:
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            global_agents_path = user_home / ".codex" / AGENTS_MD
            if global_agents_path.exists() and global_agents_path.is_file():
                try:
                    if should_process_file(global_agents_path, user_home):
                        rule_info = extract_single_rule_file(
                            global_agents_path, find_codex_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get("project_root")
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Codex rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_agents_files(user_home, user_home, projects_by_root, current_depth=0)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_agents_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict,
        current_depth: int = 0,
    ) -> None:
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_file():
                        if item.name in (AGENTS_MD, AGENTS_OVERRIDE_MD):
                            if item.parent.name == ".codex":
                                continue
                            self._extract_agents_file(item, projects_by_root)
                    elif item.is_dir():
                        if item.is_symlink():
                            continue
                        self._walk_for_agents_files(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass

    def _extract_agents_file(self, agents_file: Path, projects_by_root: Dict) -> None:
        try:
            rule_info = extract_single_rule_file(agents_file, find_codex_project_root)
            if rule_info:
                project_root = rule_info.get("project_root")
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting AGENTS.md from {agents_file}: {e}")
