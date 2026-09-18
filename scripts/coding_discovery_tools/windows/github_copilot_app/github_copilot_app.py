"""
GitHub Copilot app detection for Windows.

The GitHub Copilot app is the agent-native desktop client, distinct from the
Copilot CLI and from the VS Code extension. It ships as an NSIS installer, so it
lands in a dedicated directory machine-wide or under the user's Programs dir.

The directory is the signal: it is created by the installer and removed on
uninstall, so unlike a config dir it cannot survive as residue. The executable
name is not documented, so nothing inside is probed or run.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import dir_state, windows_program_files_roots

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "GitHubCopilot"
USER_INSTALL_DIR = Path("AppData") / "Local" / "Programs" / "GitHub Copilot"


class WindowsGitHubCopilotAppDetector(BaseToolDetector):
    """GitHub Copilot app detector for Windows."""

    @property
    def tool_name(self) -> str:
        return "GitHub Copilot App"

    def _install_dirs(self, user_home: Path):
        for root in windows_program_files_roots():
            yield root / INSTALL_DIR_NAME
        yield user_home / USER_INSTALL_DIR

    def detect(self) -> Optional[Dict]:
        user_home = Path(getattr(self, "user_home", None) or Path.home())
        for install_dir in self._install_dirs(user_home):
            if dir_state(install_dir) == "present":
                return {
                    "name": self.tool_name,
                    "version": None,
                    "install_path": str(install_dir),
                }
        return None

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """The installer records no version we can read without running the app."""
        return None
