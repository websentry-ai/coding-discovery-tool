"""
Replit skills extraction for Windows systems.

Extracts Replit skills from all projects, grouping them by project root.

Project skills: **/.agents/skills/<name>/SKILL.md
(no user path; global skills live server-side)
"""

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseReplitSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir, is_symlink_or_junction
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    get_windows_system_directories,
    should_skip_path,
)
from ...replit_skills_helpers import (
    REPLIT_PARENT_DIR_NAMES,
    REPLIT_ITEM_CONFIGS,
    extract_replit_items_from_directory,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class WindowsReplitSkillsExtractor(BaseReplitSkillsExtractor):
    """Extractor for Replit skills on Windows systems."""

    def __init__(self):
        """Initialize the extractor with thread synchronization."""
        super().__init__()
        self._lock = threading.Lock()
        self._users_directory = str(Path.home().parent)

    def extract_all_skills(self) -> Dict:
        """
        Extract all Replit skills from all projects on Windows.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills = []
        projects_by_root = {}

        self._extract_user_level_skills(user_skills)

        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Replit skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root),
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        # Replit has no local user/global skills path (global skills live server-side
        # in Workspace Settings), so there is nothing to collect at user scope. Skip
        # the (potentially expensive) all-user enumeration entirely.
        return

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract project-level skills recursively from all projects."""
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
        """Recursively walk the tree collecting Replit skills from .agents/skills/."""
        if current_depth > MAX_SEARCH_DEPTH:
            return
        if is_symlink_or_junction(current_dir):
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if (
                        should_skip_path(item, get_windows_system_directories())
                        or traverses_other_tool_config_dir(item, allow=SHARED_SKILL_DIRS | set(REPLIT_PARENT_DIR_NAMES))
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
                        if item.name in REPLIT_PARENT_DIR_NAMES:
                            for config in REPLIT_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir() and not is_symlink_or_junction(type_dir):
                                    if not is_user_level_claude_subdir(type_dir, self._users_directory):
                                        extract_replit_items_from_directory(
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
        """Add a skill to the appropriate project in the dictionary (thread-safe)."""
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
