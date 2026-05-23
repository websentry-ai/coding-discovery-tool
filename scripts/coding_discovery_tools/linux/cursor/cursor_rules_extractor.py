"""Cursor rules extraction for Linux."""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseCursorRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...cursor_rules_helpers import extract_cursor_rules_from_dir
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_and_add_rule,
    extract_single_rule_file,
    find_cursor_project_root,
    get_linux_user_homes,
    is_user_level_tool_dir,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)

CURSOR_DIR_NAME = ".cursor"


class LinuxCursorRulesExtractor(BaseCursorRulesExtractor):
    """Extractor for Cursor rules on Linux systems."""

    def extract_all_cursor_rules(self) -> List[Dict]:
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_rules(projects_by_root)
        self._extract_project_level_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_user_level_rules(
        self, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        def extract_for_user(user_home: Path) -> None:
            cursor_dir = user_home / CURSOR_DIR_NAME
            if not cursor_dir.exists() or not cursor_dir.is_dir():
                return
            try:
                extract_cursor_rules_from_dir(
                    cursor_dir, projects_by_root,
                    extract_single_rule_file, find_cursor_project_root,
                    add_rule_to_project, scope="user",
                )
            except Exception as e:
                logger.debug(f"Error extracting user Cursor rules for {user_home}: {e}")

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
                self._walk_for_cursor_files(
                    user_home, user_home, projects_by_root, current_depth=0
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_cursor_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
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

                    if item.is_dir():
                        if item.name == CURSOR_DIR_NAME:
                            if is_user_level_tool_dir(item):
                                continue
                            try:
                                extract_cursor_rules_from_dir(
                                    item, projects_by_root,
                                    extract_single_rule_file, find_cursor_project_root,
                                    add_rule_to_project, scope="project",
                                )
                            except Exception as e:
                                logger.debug(f"Error extracting from {item}: {e}")
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_cursor_files(
                            root_path, item, projects_by_root, current_depth + 1
                        )
                    elif item.is_file() and item.name == ".cursorrules":
                        if should_process_file(item, root_path):
                            extract_and_add_rule(
                                item, find_cursor_project_root, add_rule_to_project,
                                projects_by_root, scope="project",
                            )
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass
