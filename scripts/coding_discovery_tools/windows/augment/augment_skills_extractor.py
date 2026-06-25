"""
Augment Code skills/commands extraction for Windows systems.

The per-tool config and project-grouping logic are OS-agnostic and inherited from
``MacOSAugmentSkillsExtractor`` (single-threaded, no thread pool — per plan). Only
the OS primitives are overridden via seams: the file-metadata read, the
walk-skip predicate, the all-users scan, the filesystem root, the top-level
enumeration, and the user-level-dir check.
"""

from pathlib import Path
from typing import List

from ...macos.augment.augment_skills_extractor import MacOSAugmentSkillsExtractor
from ...windows_extraction_helpers import (
    extract_single_rule_file,
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)
from ...claude_code_skills_helpers import is_user_level_claude_subdir


class WindowsAugmentSkillsExtractor(MacOSAugmentSkillsExtractor):
    """Augment Code skills extractor on Windows (single-threaded; OS seams only)."""

    def __init__(self) -> None:
        super().__init__()
        self._users_directory = str(Path.home().parent)

    def _extract_single_rule_file(self, *args, **kwargs):
        return extract_single_rule_file(*args, **kwargs)

    def _should_skip_walk_item(self, item: Path) -> bool:
        return should_skip_path(item, get_windows_system_directories())

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

    def _is_user_level_skill_dir(self, type_dir: Path) -> bool:
        return is_user_level_claude_subdir(type_dir, self._users_directory)
