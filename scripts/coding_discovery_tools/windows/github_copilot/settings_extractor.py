"""VS Code GitHub Copilot settings/permission extraction for Windows."""

import logging
from pathlib import Path
from typing import List

from ...coding_tool_base import BaseGitHubCopilotSettingsExtractor
from ...constants import WINDOWS_SKIP_USER_DIRS
from ...windows_extraction_helpers import is_running_as_admin

logger = logging.getLogger(__name__)

# VS Code ``User`` config dirs under %APPDATA% (stable + Insiders), stable first.
# Resolved per-user from the user's home — NOT from %APPDATA% of the running
# process, which under a service/SYSTEM token points at systemprofile, not a user.
_USER_CONFIG_SUBPATHS = (
    ("AppData", "Roaming", "Code", "User"),
    ("AppData", "Roaming", "Code - Insiders", "User"),
)


class WindowsGitHubCopilotSettingsExtractor(BaseGitHubCopilotSettingsExtractor):
    """Extractor for VS Code GitHub Copilot permissions on Windows."""

    def _scan_users(self, callback) -> None:
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if (user_dir.is_dir()
                            and not user_dir.name.startswith(".")
                            and user_dir.name not in WINDOWS_SKIP_USER_DIRS):
                        try:
                            callback(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            callback(Path.home())

    def _user_config_dirs(self, user_home: Path) -> List[Path]:
        return [user_home.joinpath(*parts) for parts in _USER_CONFIG_SUBPATHS]
