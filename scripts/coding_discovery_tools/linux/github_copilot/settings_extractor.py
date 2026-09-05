"""VS Code GitHub Copilot settings/permission extraction for Linux."""

from pathlib import Path
from typing import List

from ...coding_tool_base import BaseGitHubCopilotSettingsExtractor
from ...linux_extraction_helpers import get_linux_user_homes, is_running_as_root

# VS Code ``User`` config dirs under ~/.config (stable + Insiders), stable first.
_USER_CONFIG_SUBPATHS = (
    (".config", "Code", "User"),
    (".config", "Code - Insiders", "User"),
)


class LinuxGitHubCopilotSettingsExtractor(BaseGitHubCopilotSettingsExtractor):
    """Extractor for VS Code GitHub Copilot permissions on Linux."""

    def _scan_users(self, callback) -> None:
        if is_running_as_root():
            for user_home in get_linux_user_homes():
                try:
                    callback(user_home)
                except Exception:
                    continue
        else:
            callback(Path.home())

    def _user_config_dirs(self, user_home: Path) -> List[Path]:
        return [user_home.joinpath(*parts) for parts in _USER_CONFIG_SUBPATHS]
