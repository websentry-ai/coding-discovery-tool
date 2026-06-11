"""
GitHub Copilot CLI settings/permissions extraction for Linux.

The permission-config parsing is OS-agnostic and inherited from
``MacOSCopilotCliSettingsExtractor``. The only OS-specific seam is the all-users
scan, so this subclass overrides just ``_scan_all_user_homes``.
"""

from pathlib import Path

from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli_settings_extractor import (
    MacOSCopilotCliSettingsExtractor,
)


class LinuxCopilotCliSettingsExtractor(MacOSCopilotCliSettingsExtractor):
    """GitHub Copilot CLI settings extractor on Linux (overrides one seam)."""

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user`` for every Linux user home.

        ``get_linux_user_homes`` returns all human users when running as root
        (including ``/root``), else just the current user's home.
        """
        for user_home in get_linux_user_homes():
            extract_for_user(Path(user_home))
