"""
Roo Code skills extraction for macOS systems.

Global skills:  ~/.roo/skills/<name>/SKILL.md, ~/.roo/skills-{mode}/<name>/SKILL.md
                ~/.agents/skills/..., ~/.agents/skills-{mode}/...
Project skills: **/.roo/skills/, **/.roo/skills-{mode}/, **/.agents/skills/, **/.agents/skills-{mode}/
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseRooSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH, SHARED_SKILL_DIRS, traverses_other_tool_config_dir
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
    is_running_as_root,
    scan_user_directories,
)
from ...roo_skills_helpers import (
    ROO_PARENT_DIR_NAMES,
    iter_roo_skill_type_dirs,
    extract_roo_items_from_directory,
    extract_roo_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class MacOSRooSkillsExtractor(BaseRooSkillsExtractor):
    """Extractor for Roo Code skills on macOS systems."""

    def extract_all_skills(self) -> Dict:
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
        def extract_for_user(user_home: Path) -> None:
            extract_roo_user_level_items(user_home, user_skills, extract_single_rule_file)

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search for Roo Code skills")
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
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if (
                        should_skip_path(item)
                        or should_skip_system_path(item)
                        or traverses_other_tool_config_dir(item, allow=SHARED_SKILL_DIRS | set(ROO_PARENT_DIR_NAMES))
                    ):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name in ROO_PARENT_DIR_NAMES:
                            for type_dir in iter_roo_skill_type_dirs(item):
                                if not is_user_level_claude_subdir(type_dir):
                                    extract_roo_items_from_directory(
                                        type_dir,
                                        projects_by_root,
                                        extract_single_rule_file,
                                        add_skill_to_project,
                                    )
                            continue

                        if item.is_symlink():
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
