"""
Augment Code settings/permissions extraction for Windows.

The settings/permissions parsing is OS-agnostic and inherited from
``MacOSAugmentSettingsExtractor``. Only the OS seams differ: the all-users scan
and the managed-settings path (``C:\\ProgramData\\augment\\settings.json``).
"""

from pathlib import Path

from ...macos.augment.augment_settings_extractor import (
    MacOSAugmentSettingsExtractor,
)
from ...windows_extraction_helpers import (
    scan_windows_user_directories,
)


class WindowsAugmentSettingsExtractor(MacOSAugmentSettingsExtractor):
    """Augment Code settings extractor on Windows (overrides OS seams only)."""

    def _user_settings_scan(self, extract_for_user) -> None:
        scan_windows_user_directories(extract_for_user)

    def _managed_settings_path(self) -> Path:
        return Path("C:\\ProgramData\\augment\\settings.json")
