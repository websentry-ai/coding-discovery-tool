"""pi coding agent config extraction for Linux systems.

Paths are identical to macOS (``~/.pi/agent`` global, ``<project>/.pi`` project),
so only three OS seams are overridden: the user-home enumeration, the Linux-aware
project walk, and the user-level-dir test. The gates, allow-lists and the
``auth.json`` never-read rule all stay in the shared macOS body.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_user_level_tool_dir,
    walk_for_tool_directories,
)
from ...macos.pi.pi_rules_extractor import MacOSPiRulesExtractor

logger = logging.getLogger(__name__)


class LinuxPiRulesExtractor(MacOSPiRulesExtractor):
    """Extractor for pi coding agent config on Linux systems."""

    def _for_each_user_home(self, fn) -> None:
        for user_home in get_linux_user_homes():
            try:
                fn(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _is_user_level_dir(self, tool_dir: Path) -> bool:
        return is_user_level_tool_dir(tool_dir)

    def _extract_project_level_rules(
        self, root_path: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Walk each Linux user home with the Linux-aware walker.

        ``root_path`` is accepted for signature compatibility with the macOS
        base; the Linux walk is always rooted at the user homes (the macOS
        ``should_skip_system_path`` skips ``/home`` outright).
        """
        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".pi",
                    self._extract_rules_from_pi_directory,
                    projects_by_root, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")
