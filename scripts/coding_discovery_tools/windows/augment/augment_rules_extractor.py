"""
Augment Code rules/guidelines extraction for Windows systems.

The source set and the depth-bounded walk are OS-agnostic and live in
``MacOSAugmentRulesExtractor``. Only the five OS primitives differ — the
privilege check, the all-users scan, the filesystem root, top-level enumeration,
and the system-dir skip predicate — so this subclass overrides exactly those.
"""

from pathlib import Path
from typing import List

from ...constants import traverses_other_tool_config_dir
from ...macos.augment.augment_rules_extractor import MacOSAugmentRulesExtractor
from ...windows_extraction_helpers import (
    get_windows_system_directories,
    is_running_as_admin,
    scan_windows_user_directories,
    should_skip_path,
)


class WindowsAugmentRulesExtractor(MacOSAugmentRulesExtractor):
    """Augment Code rules extractor on Windows (overrides OS seams only)."""

    def _is_privileged(self) -> bool:
        return is_running_as_admin()

    def _scan_all_user_homes(self, extract_for_user) -> None:
        scan_windows_user_directories(extract_for_user)

    def _filesystem_root(self) -> Path:
        return Path(Path.home().anchor)

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        system_dirs = get_windows_system_directories()
        try:
            return [
                item
                for item in root_path.iterdir()
                if item.is_dir() and not should_skip_path(item, system_dirs)
            ]
        except (PermissionError, OSError):
            return []

    def _should_skip(self, item: Path) -> bool:
        return (
            should_skip_path(item, get_windows_system_directories())
            or traverses_other_tool_config_dir(item)
        )
