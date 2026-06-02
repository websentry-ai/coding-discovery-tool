"""
GitHub Copilot CLI rules/instructions extraction for Linux.

Subclasses the macOS extractor, overriding the five OS-specific seams:

  - ``_is_privileged``      → always True on Linux (running as root for discovery)
  - ``_scan_all_user_homes`` → uses ``get_linux_user_homes()``
  - ``_filesystem_root``     → ``/`` (same as macOS, different from Windows)
  - ``_iter_top_level_dirs`` → delegates to ``get_top_level_directories``
  - ``_should_skip``         → uses linux ``should_skip_path`` / ``should_skip_system_path``

All rule-walking logic (G/E/P sources, depth-bounded walk) is inherited
unchanged from the macOS base.
"""

import logging
from pathlib import Path
from typing import List

from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.copilot_cli_rules_extractor import MacOSCopilotCliRulesExtractor
from ...macos_extraction_helpers import get_top_level_directories

logger = logging.getLogger(__name__)


class LinuxCopilotCliRulesExtractor(MacOSCopilotCliRulesExtractor):
    """Extractor for GitHub Copilot CLI rules on Linux systems."""

    def _is_privileged(self) -> bool:
        """Discovery on Linux always runs as root — gates E1 (per-user env)."""
        return True

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every Linux user home."""
        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping {user_home}: {exc}")

    def _filesystem_root(self) -> Path:
        """Linux filesystem root is ``/``."""
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under ``/``, system dirs excluded."""
        return list(get_top_level_directories(root_path))

    def _should_skip(self, item: Path) -> bool:
        """Whether a path is skipped during the project walk."""
        return should_skip_path(item) or should_skip_system_path(item)
