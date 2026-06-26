"""
Augment Code settings/permissions extraction for Linux.

The settings/permissions parsing is OS-agnostic and inherited from
``MacOSAugmentSettingsExtractor``. The managed path
(``/etc/augment/settings.json``) is shared with macOS and inherited; only the
all-users scan is Linux-specific.
"""

from pathlib import Path

from ...linux_extraction_helpers import (
    get_linux_user_homes,
)
from ...macos.augment.augment_settings_extractor import (
    MacOSAugmentSettingsExtractor,
)


class LinuxAugmentSettingsExtractor(MacOSAugmentSettingsExtractor):
    """Augment Code settings extractor on Linux (overrides OS seams only)."""

    def _user_settings_scan(self, extract_for_user) -> None:
        for user_home in get_linux_user_homes():
            extract_for_user(Path(user_home))
