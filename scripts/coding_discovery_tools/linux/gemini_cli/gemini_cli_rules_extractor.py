"""Gemini CLI rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseGeminiCliRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_linux_user_homes,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)
from ...macos_extraction_helpers import find_gemini_cli_project_root

logger = logging.getLogger(__name__)


class LinuxGeminiCliRulesExtractor(BaseGeminiCliRulesExtractor):
    """Extractor for Gemini CLI rules on Linux systems."""

    def extract_all_gemini_cli_rules(self) -> List[Dict]:
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
                            global_rules_path, find_gemini_cli_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get("project_root")
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Gemini CLI rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_gemini_md(user_home, user_home, projects_by_root, current_depth=0)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_gemini_md(
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

                    if item.is_file() and item.name == "GEMINI.md":
                        if item.parent.name == ".gemini":
                            continue
                        if should_process_file(item, item.parent):
                            try:
                                rule_info = extract_single_rule_file(
                                    item, find_gemini_cli_project_root
                                )
                                if rule_info:
                                    project_root = rule_info.get("project_root")
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                            except Exception as e:
                                logger.debug(f"Error extracting GEMINI.md from {item}: {e}")
                    elif item.is_dir():
                        if item.is_symlink():
                            continue
                        self._walk_for_gemini_md(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass
