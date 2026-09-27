"""
Cline skills extraction for Windows systems.

Extracts Cline skills from all projects, grouping them by project root.

Global skills:  ~/.cline/skills/<name>/SKILL.md
Project skills: **/.cline/skills/<name>/SKILL.md
                **/.clinerules/skills/<name>/SKILL.md
                **/.claude/skills/<name>/SKILL.md
"""

import logging
import threading
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClineSkillsExtractor
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    scan_windows_user_directories,
    walk_for_tool_directories,
)
from ...cline_skills_helpers import (
    CLINE_PARENT_DIR_NAMES,
    CLINE_ITEM_CONFIGS,
    extract_cline_items_from_directory,
    extract_cline_user_level_items,
)
from ...claude_code_skills_helpers import (
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_claude_subdir,
)

logger = logging.getLogger(__name__)


class WindowsClineSkillsExtractor(BaseClineSkillsExtractor):
    """Extractor for Cline skills on Windows systems."""

    def __init__(self):
        """Initialize the extractor with thread synchronization."""
        super().__init__()
        self._lock = threading.Lock()
        self._users_directory = str(Path.home().parent)

    def extract_all_skills(self) -> Dict:
        """
        Extract all Cline skills from all projects on Windows.

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

        logger.info(f"Searching for Cline skills from root: {root_path}")
        self._extract_project_level_skills(root_path, projects_by_root)

        return {
            "user_skills": user_skills,
            "project_skills": build_skills_project_list(projects_by_root)
        }

    def _get_users_directory(self) -> str:
        """
        Get the cached Users directory path.

        Returns:
            String path to the Users directory (e.g., C:\\Users or D:\\Users)
        """
        return self._users_directory

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """
        Extract user-level skills from ~/.cline/skills/ directory.

        Args:
            user_skills: List to populate with user-level skills
        """
        def extract_for_user(user_home: Path) -> None:
            extract_cline_user_level_items(user_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level skills recursively from all projects.

        Args:
            root_path: Root directory to search from
            projects_by_root: Dictionary to populate with skills grouped by project root
        """
        walk_for_tool_directories(
            root_path, root_path, CLINE_PARENT_DIR_NAMES,
            self._extract_skills_from_parent_dir, projects_by_root,
        )

    def _extract_skills_from_parent_dir(
        self, parent_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract skills from a matched Cline parent dir (e.g. ``.clinerules``)."""
        for config in CLINE_ITEM_CONFIGS:
            type_dir = parent_dir / config.dir_name
            if type_dir.exists() and type_dir.is_dir():
                # is_user_level_claude_subdir works generically for any tool dir
                if not is_user_level_claude_subdir(type_dir, self._users_directory):
                    extract_cline_items_from_directory(
                        type_dir,
                        projects_by_root,
                        extract_single_rule_file,
                        self._add_skill_to_project_threadsafe,
                        config,
                    )

    def _add_skill_to_project_threadsafe(
        self,
        skill_info: Dict,
        project_root: str,
        projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Add a skill to the appropriate project in the dictionary (thread-safe).

        Args:
            skill_info: Skill file information dict
            project_root: Project root path as string
            projects_by_root: Dictionary to update
        """
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
