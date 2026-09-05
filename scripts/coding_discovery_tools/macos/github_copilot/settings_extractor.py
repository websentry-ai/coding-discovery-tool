"""VS Code GitHub Copilot settings/permission extraction for macOS."""

from pathlib import Path
from typing import List

from ...coding_tool_base import BaseGitHubCopilotSettingsExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

# VS Code ``User`` config dirs under ~/Library/Application Support (stable +
# Insiders) — the parents of settings.json and profiles/. Stable Code first.
_USER_CONFIG_SUBPATHS = (
    ("Library", "Application Support", "Code", "User"),
    ("Library", "Application Support", "Code - Insiders", "User"),
)


class MacOSGitHubCopilotSettingsExtractor(BaseGitHubCopilotSettingsExtractor):
    """Extractor for VS Code GitHub Copilot permissions on macOS."""

    def _scan_users(self, callback) -> None:
        if is_running_as_root():
            scan_user_directories(callback)
        else:
            callback(Path.home())

    def _user_config_dirs(self, user_home: Path) -> List[Path]:
        return [user_home.joinpath(*parts) for parts in _USER_CONFIG_SUBPATHS]
