"""Cursor skills extraction for Linux."""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseCursorSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    extract_single_rule_file,
    get_linux_user_homes,
    is_user_level_tool_dir,
    should_skip_path,
    should_skip_system_path,
)
from ...cursor_skills_helpers import (
    CURSOR_PARENT_DIR_NAMES,
    CURSOR_ITEM_CONFIGS,
    extract_cursor_items_from_directory,
    extract_cursor_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
)

logger = logging.getLogger(__name__)


class LinuxCursorSkillsExtractor(BaseCursorSkillsExtractor):
    """Extractor for Cursor skills on Linux systems."""

    def extract_all_skills(self) -> Dict:
        user_skills: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_skills(user_skills)
        self._extract_project_level_skills(projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        def extract_for_user(user_home: Path) -> None:
            extract_cursor_user_level_items(
                user_home, user_skills, extract_single_rule_file, CURSOR_ITEM_CONFIGS
            )

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_skills(
        self, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_skills(user_home, user_home, projects_by_root, current_depth=0)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_skills(
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
                        if item.name in CURSOR_PARENT_DIR_NAMES:
                            for config in CURSOR_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir():
                                    if not is_user_level_tool_dir(item):
                                        extract_cursor_items_from_directory(
                                            type_dir, projects_by_root,
                                            extract_single_rule_file, add_skill_to_project, config,
                                        )
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_skills(root_path, item, projects_by_root, current_depth + 1)
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass
