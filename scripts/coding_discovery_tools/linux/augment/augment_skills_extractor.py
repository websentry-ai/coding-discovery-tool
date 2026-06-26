"""
Augment Code skills/commands extraction for Linux systems.

The per-tool config and project-grouping logic are inherited from
``MacOSAugmentSkillsExtractor`` (DRY); only the OS primitives are overridden via
seams. Note the Linux ``should_skip_system_path`` must NOT skip ``/home`` (unlike
macOS), or project skills under user homes are dropped, and the user-level-dir
check must handle both ``/home/<user>`` and ``/root``.
"""

import logging
from pathlib import Path
from typing import List

from ...constants import traverses_other_tool_config_dir
from ...linux_extraction_helpers import (
    extract_single_rule_file,
    get_linux_user_homes,
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...claude_code_skills_helpers import is_user_level_claude_subdir
from ...macos.augment.augment_skills_extractor import MacOSAugmentSkillsExtractor

logger = logging.getLogger(__name__)


class LinuxAugmentSkillsExtractor(MacOSAugmentSkillsExtractor):
    """Augment Code skills extractor on Linux (overrides OS seams only)."""

    def _extract_single_rule_file(self, *args, **kwargs):
        return extract_single_rule_file(*args, **kwargs)

    def _should_skip_walk_item(self, item: Path) -> bool:
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )

    def _scan_all_user_homes(self, extract_for_user) -> None:
        for user_home in get_linux_user_homes():
            try:
                extract_for_user(Path(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _filesystem_root(self) -> Path:
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        return list(get_top_level_directories(root_path))

    def _is_user_level_skill_dir(self, type_dir: Path) -> bool:
        """Linux has two home shapes: ``/home/<user>`` and ``/root``. Pin the
        users-root to ``/home`` and add an explicit ``/root`` check."""
        if is_user_level_claude_subdir(type_dir, users_root_path="/home"):
            return True
        try:
            return type_dir.parent.parent == Path("/root")
        except (OSError, ValueError):
            return False
