"""VS Code GitHub Copilot settings/permission extraction for macOS."""

from pathlib import Path
from typing import List

from ...coding_tool_base import BaseGitHubCopilotSettingsExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

SETTINGS_FILENAME = "settings.json"

# User-scope VS Code settings.json under ~/Library/Application Support (stable +
# Insiders). Stable Code is preferred, so it is listed first.
_USER_SETTINGS_SUBPATHS = (
    ("Library", "Application Support", "Code", "User", SETTINGS_FILENAME),
    ("Library", "Application Support", "Code - Insiders", "User", SETTINGS_FILENAME),
)


class MacOSGitHubCopilotSettingsExtractor(BaseGitHubCopilotSettingsExtractor):
    """Extractor for VS Code GitHub Copilot permissions on macOS."""

    def _scan_users(self, callback) -> None:
        if is_running_as_root():
            scan_user_directories(callback)
        else:
            callback(Path.home())

    def _user_settings_candidates(self, user_home: Path) -> List[Path]:
        return [user_home.joinpath(*parts) for parts in _USER_SETTINGS_SUBPATHS]
