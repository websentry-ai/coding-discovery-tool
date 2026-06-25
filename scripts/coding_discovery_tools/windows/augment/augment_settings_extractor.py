"""
Augment Code settings/permissions extraction for Windows.

The settings/permissions parsing is OS-agnostic and inherited from
``MacOSAugmentSettingsExtractor``. Only the OS seams differ: the all-users scan,
the managed-settings path (``C:\\ProgramData\\augment\\settings.json``), the
filesystem root, and the top-level enumeration.
"""

from pathlib import Path
from typing import List

from ...macos.augment.augment_settings_extractor import (
    MacOSAugmentSettingsExtractor,
)
from ...windows_extraction_helpers import (
    get_windows_system_directories,
    scan_windows_user_directories,
    should_skip_path,
)


class WindowsAugmentSettingsExtractor(MacOSAugmentSettingsExtractor):
    """Augment Code settings extractor on Windows (overrides OS seams only)."""

    def _user_settings_scan(self, extract_for_user) -> None:
        scan_windows_user_directories(extract_for_user)

    def _managed_settings_path(self) -> Path:
        return Path("C:\\ProgramData\\augment\\settings.json")

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
