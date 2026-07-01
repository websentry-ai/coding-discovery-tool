"""
Augment Code rules/guidelines extraction for Linux systems.

The source set and the depth-bounded walk are OS-agnostic and inherited from
``MacOSAugmentRulesExtractor`` (DRY). This subclass overrides only the
OS-specific seams via ``linux_extraction_helpers`` (note the Linux
``should_skip_system_path`` must NOT skip ``/home``).
"""

import logging
from pathlib import Path
from typing import List

from ...constants import traverses_other_tool_config_dir
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    get_top_level_directories,
    is_running_as_root,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.augment.augment_rules_extractor import MacOSAugmentRulesExtractor

logger = logging.getLogger(__name__)


class LinuxAugmentRulesExtractor(MacOSAugmentRulesExtractor):
    """Augment Code rules extractor on Linux (overrides OS seams only)."""

    def _is_privileged(self) -> bool:
        return is_running_as_root()

    def _scan_all_user_homes(self, extract_for_user) -> None:
        # Guard each user so one unreadable home (PermissionError/OSError) can't
        # abort the whole multi-user scan — parity with the Linux skills extractor.
        for user_home in get_linux_user_homes():
            try:
                extract_for_user(Path(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _filesystem_root(self) -> Path:
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        return list(get_top_level_directories(root_path))

    def _should_skip(self, item: Path) -> bool:
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )
