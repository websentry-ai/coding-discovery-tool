"""
Replit skills extraction for macOS systems.

Extracts Replit skills from all projects, grouping them by project root.

Project skills: **/.agents/skills/<name>/SKILL.md
(no user path; global skills live server-side)
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseReplitSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
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


class MacOSReplitSkillsExtractor(BaseReplitSkillsExtractor):
    """Extractor for Replit skills on macOS systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract all Replit skills from all projects on macOS.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills = []
        projects_by_root = {}

        self._extract_user_level_skills(user_skills)

        root_path = Path("/")
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
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search for Replit skills")
                home_path = Path.home()
                self._walk_for_skills(home_path, home_path, projects_by_root, current_depth=0)
        else:
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

        try:
            for item in current_dir.iterdir():
                try:
                    if (
                        should_skip_path(item)
                        or should_skip_system_path(item)
                        or traverses_other_tool_config_dir(item, allow=SHARED_SKILL_DIRS | set(REPLIT_PARENT_DIR_NAMES))
                    ):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_symlink():
                        continue
                    if item.is_dir():
                        if item.name in REPLIT_PARENT_DIR_NAMES:
                            for config in REPLIT_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir() and not type_dir.is_symlink():
                                    if not is_user_level_claude_subdir(type_dir):
                                        extract_replit_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            add_skill_to_project,
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
