"""
Cursor skills extraction for macOS systems.

Extracts Cursor skills from all projects,
grouping them by project root.

Skills:  ~/.cursor/skills/<name>/SKILL.md, **/.cursor/skills/<name>/SKILL.md
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorSkillsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
    is_running_as_root,
    scan_user_directories,
)
from ...cursor_skills_helpers import (
    CURSOR_DIR_NAME,
    AGENTS_DIR_NAME,
    CURSOR_PARENT_DIR_NAMES,
    CURSOR_ITEM_CONFIGS,
    extract_cursor_items_from_directory,
    extract_cursor_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class MacOSCursorSkillsExtractor(BaseCursorSkillsExtractor):
    """Extractor for Cursor skills on macOS systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract all Cursor skills from all projects on macOS.

        Returns:
            Dict with:
            - user_skills: List of user-level skill dicts (global, scope: "user")
            - project_skills: List of project dicts with project_root and skills
        """
        user_skills = []
        projects_by_root = {}

        # Extract user-level skills from ~/.cursor/skills/
        self._extract_user_level_skills(user_skills)

        # Extract project-level skills from **/.cursor/skills/
        root_path = Path("/")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root)
        }

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """
        Extract user-level skills from ~/.cursor/skills/ and ~/.agents/skills/ directories.

        Args:
            user_skills: List to populate with user-level skills
        """
        def extract_for_user(user_home: Path) -> None:
            extract_cursor_user_level_items(user_home, user_skills, extract_single_rule_file, CURSOR_ITEM_CONFIGS)

        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level skills recursively from all projects.

        Args:
            root_path: Root directory to search from
            projects_by_root: Dictionary to populate with skills grouped by project root
        """
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                # Fallback to home directory
                logger.info("Falling back to home directory search for Cursor skills")
                home_path = Path.home()
                self._walk_for_skills(home_path, home_path, projects_by_root, current_depth=0)
        else:
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_skills(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .cursor/skills directories.

        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects_by_root: Dictionary to populate with skills
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
                        continue

                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        # Check if this is a .cursor or .agents directory
                        if item.name in CURSOR_PARENT_DIR_NAMES:
                            for config in CURSOR_ITEM_CONFIGS:
                                type_dir = item / config.dir_name
                                if type_dir.exists() and type_dir.is_dir():
                                    if not is_user_level_claude_subdir(type_dir):
                                        extract_cursor_items_from_directory(
                                            type_dir,
                                            projects_by_root,
                                            extract_single_rule_file,
                                            add_skill_to_project,
                                            config,
                                        )
                            continue

                        if item.is_symlink():
                            continue

                        # Recurse into other directories
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
