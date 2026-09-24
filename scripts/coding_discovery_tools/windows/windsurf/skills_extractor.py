"""
Windsurf (Cascade) skills extraction for Windows systems.

Global skills:  ~/.codeium/windsurf/skills/<name>/SKILL.md (+ ~/.agents, ~/.claude compat)
Project skills: **/.windsurf/skills/<name>/SKILL.md
                (+ **/{.agents,.claude}/skills compat)
"""

import logging
import threading
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseWindsurfSkillsExtractor
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    scan_windows_user_directories,
    walk_for_tool_directories,
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
        """Extract project-level skills via the shared single-pass directory index.

        Every tool reuses ONE memoized walk of the drive instead of each
        re-walking it independently.
        """
        walk_for_tool_directories(
            root_path, root_path, WINDSURF_PARENT_DIR_NAMES,
            self._extract_skills_from_parent_dir, projects_by_root,
        )

    def _extract_skills_from_parent_dir(
        self, parent_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract skills from a matched tool parent dir."""
        for config in WINDSURF_ITEM_CONFIGS:
            type_dir = parent_dir / config.dir_name
            if type_dir.exists() and type_dir.is_dir():
                if not is_user_level_claude_subdir(type_dir, self._users_directory):
                    extract_windsurf_items_from_directory(
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
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        with self._lock:
            add_skill_to_project(skill_info, project_root, projects_by_root)
