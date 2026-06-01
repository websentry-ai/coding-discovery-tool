"""
GitHub Copilot CLI settings/permissions extraction for Windows.

The durable permission config (``config.json`` / ``settings.json`` carrying
``trusted_folders`` / ``allowed_urls`` / ``denied_urls``) is OS-agnostic, and all
the parsing lives in ``MacOSCopilotCliSettingsExtractor``. The ONLY OS-specific
seam is the all-users scan, so this subclass overrides just
``_scan_all_user_homes`` — there is no filesystem walk here (unlike the rules
extractor, which needs five seams).
"""

from ...macos.copilot_cli.copilot_cli_settings_extractor import (
    MacOSCopilotCliSettingsExtractor,
)
from ...windows_extraction_helpers import scan_windows_user_directories


class WindowsCopilotCliSettingsExtractor(MacOSCopilotCliSettingsExtractor):
    """GitHub Copilot CLI settings extractor on Windows (overrides one seam)."""

    def _scan_all_user_homes(self, extract_for_user) -> None:
        # scan_windows_user_directories gates on admin internally: every C:\Users
        # user when admin, else just the current user.
        scan_windows_user_directories(extract_for_user)
