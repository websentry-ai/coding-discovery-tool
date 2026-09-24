"""
Skills extraction for Muse Code on macOS systems.

Muse Code installs personal skills (``muse skills install``) into
``~/.config/muse/skills/<name>/SKILL.md``; the ``.muse`` lock directory beside
them holds no skills. Project skills come from the shared ``.claude``,
``.codex`` and ``.agents`` directories and are reported under those agents.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseMuseCodeSkillsExtractor
from ...claude_code_skills_helpers import (
    ItemTypeConfig,
    extract_user_level_items,
    is_skill_md_file,
)
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories
from ...rule_read_helpers import extract_rule_file_contained

logger = logging.getLogger(__name__)

MUSE_CODE_CONFIG_DIR_NAME = str(Path(".config") / "muse")

MUSE_CODE_SKILL_CONFIG = ItemTypeConfig(
    type_name="skill",
    dir_name="skills",
    layout="nested",
    file_filter=is_skill_md_file,
    name_extractor=lambda f: f.parent.name,
)


class MacOSMuseCodeSkillsExtractor(BaseMuseCodeSkillsExtractor):
    """Extractor for Muse Code personal skills on macOS systems."""

    def extract_all_skills(self) -> Dict:
        """
        Extract Muse Code personal skills for every user home in scope.

        Returns:
            Dict with ``user_skills`` and an empty ``project_skills`` list.
        """
        user_skills: List[Dict] = []
        if is_running_as_root():
            scan_user_directories(lambda home: self._extract_for_user(home, user_skills))
        else:
            self._extract_for_user(Path.home(), user_skills)
        return {"user_skills": user_skills, "project_skills": []}

    def _extract_for_user(self, user_home: Path, user_skills: List[Dict]) -> None:
        """Extract ~/.config/muse/skills for one user home."""

        # A personal skill belongs to the home, not to ~/.config (which the
        # generic tool-dir walk would otherwise resolve as its project root).
        # Strict read, so a linked SKILL.md cannot pull in auth.json.
        def read_skill(skill_file: Path, _find_root, scope: str = "user"):
            rule_info = extract_rule_file_contained(skill_file, lambda _f: user_home)
            if rule_info:
                rule_info["scope"] = "user"
            return rule_info

        try:
            extract_user_level_items(
                user_home,
                user_skills,
                read_skill,
                [MUSE_CODE_SKILL_CONFIG],
                user_dir_names=(MUSE_CODE_CONFIG_DIR_NAME,),
                parent_dir_names=("muse",),
            )
        except Exception as e:
            logger.debug(f"Error extracting Muse Code skills for {user_home}: {e}")
