"""
Augment Code settings/permissions extraction for Linux.

The settings/permissions parsing is OS-agnostic and inherited from
``MacOSAugmentSettingsExtractor``. The managed path
(``/etc/augment/settings.json``) is shared with macOS and inherited; only the
all-users scan and the filesystem-walk primitives are Linux-specific.
"""

from pathlib import Path
from typing import List

from ...linux_extraction_helpers import (
    get_linux_user_homes,
    get_top_level_directories,
)
from ...macos.augment.augment_settings_extractor import (
    MacOSAugmentSettingsExtractor,
)


class LinuxAugmentSettingsExtractor(MacOSAugmentSettingsExtractor):
    """Augment Code settings extractor on Linux (overrides OS seams only)."""

    def _user_settings_scan(self, extract_for_user) -> None:
        for user_home in get_linux_user_homes():
            extract_for_user(Path(user_home))

    def _filesystem_root(self) -> Path:
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        return list(get_top_level_directories(root_path))
