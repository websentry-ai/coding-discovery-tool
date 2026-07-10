"""
Windsurf (Cascade) skills extraction for Windows systems.

Global skills:  ~/.codeium/windsurf/skills/<name>/SKILL.md (+ ~/.agents, ~/.claude compat)
Project skills: **/.windsurf/skills/<name>/SKILL.md
                (+ **/{.agents,.claude}/skills compat)
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseWindsurfSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir, is_symlink_or_junction
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)
from ...windsurf_skills_helpers import (
    WINDSURF_PARENT_DIR_NAMES,
    WINDSURF_ITEM_CONFIGS,
    extract_windsurf_items_from_directory,
    extract_windsurf_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class WindowsWindsurfSkillsExtractor(BaseWindsurfSkillsExtractor):
    """Extractor for Windsurf skills on Windows systems."""

    def __init__(self):
        super().__init__()
        self._lock = threading.Lock()
        self._users_directory = str(Path.home().parent)

    def extract_all_skills(self) -> Dict:
        user_skills = []
        projects_by_root = {}

        self._extract_user_level_skills(user_skills)

        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Windsurf skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        def extract_for_user(user_home: Path) -> None:
            extract_windsurf_user_level_items(user_home, user_skills, extract_single_rule_file, WINDSURF_ITEM_CONFIGS)

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        try:
            top_level_dirs = [item for item in root_path.iterdir()
                              if item.is_dir() and not should_skip_path(item, get_windows_system_directories())
                              and not is_symlink_or_junction(item)]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_skills, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        if current_depth > MAX_SEARCH_DEPTH:
            return
        if is_symlink_or_junction(current_dir):
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if (
                        should_skip_path(item, get_windows_system_directories())
                        or traverses_other_tool_config_dir(item, allow=SHARED_SKILL_DIRS | set(WINDSURF_PARENT_DIR_NAMES))
                    ):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if is_symlink_or_junction(item):
                        continue
                    if item.is_dir():
                        if item.name in WINDSURF_PARENT_DIR_NAMES:
                            for config in WINDSURF_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir() and not is_symlink_or_junction(type_dir):
                                    if not is_user_level_claude_subdir(type_dir, self._users_directory):
                                        extract_windsurf_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            self._add_skill_to_project_threadsafe,
                                            config,
                                        )
                            continue

                        self._walk_for_skills(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _add_skill_to_project_threadsafe(
        self,
        skill_info: Dict,
        project_root: str,
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
