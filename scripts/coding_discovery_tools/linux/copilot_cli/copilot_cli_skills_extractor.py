"""
GitHub Copilot CLI skills extraction for Linux systems.

Subclasses the macOS extractor, overriding the two OS-specific methods:

  - ``_extract_user_level_skills`` → iterates ``get_linux_user_homes()``
  - ``_extract_project_level_skills`` → walks from ``/`` (same as macOS)
    using ``get_linux_user_homes()`` for the top-level fallback guard.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli_skills_extractor import MacOSCopilotCliSkillsExtractor
from ...macos_extraction_helpers import (
    extract_single_rule_file,
    should_process_directory,
)
from ...copilot_cli_skills_helpers import (
    COPILOT_CLI_ITEM_CONFIGS,
    extract_copilot_cli_user_level_items,
)

logger = logging.getLogger(__name__)


class LinuxCopilotCliSkillsExtractor(MacOSCopilotCliSkillsExtractor):
    """Extractor for GitHub Copilot CLI skills on Linux systems."""

    def _extract_user_level_skills(self, user_skills: List[Dict]) -> None:
        """Extract user-level skills from ~/.copilot/skills and ~/.agents/skills.

        Scans every home returned by ``get_linux_user_homes()``.
        """
        for user_home in get_linux_user_homes():
            try:
                extract_copilot_cli_user_level_items(
                    user_home, user_skills, extract_single_rule_file, COPILOT_CLI_ITEM_CONFIGS
                )
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping {user_home}: {exc}")

    def _extract_project_level_skills(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Walk for project-level skills from ``/`` on Linux."""
        if root_path == Path("/"):
            try:
                from ...macos_extraction_helpers import get_top_level_directories
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_skills(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as exc:
                logger.warning(f"Error accessing root directory: {exc}")
                for user_home in get_linux_user_homes():
                    try:
                        self._walk_for_skills(user_home, user_home, projects_by_root, current_depth=0)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {user_home}: {e}")
        else:
            self._walk_for_skills(root_path, root_path, projects_by_root, current_depth=0)
