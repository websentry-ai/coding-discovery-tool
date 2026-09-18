"""
GitHub Copilot app detection for Windows.

The GitHub Copilot app is the agent-native desktop client, distinct from the
Copilot CLI and from the VS Code extension. It ships as an NSIS installer, so it
lands in a dedicated directory machine-wide or under the user's Programs dir.

The directory alone is not the signal: an interrupted uninstall can leave it
behind, so a live install must also hold a binary. The executable name is not
documented, so the directory is listed but nothing inside it is run.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import dir_state, windows_program_files_roots

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "GitHubCopilot"
USER_INSTALL_DIR = Path("AppData") / "Local" / "Programs" / "GitHub Copilot"


def _install_state(install_dir: Path) -> str:
    """``present`` only when the directory holds a binary, else ``absent``/``unreadable``."""
    state = dir_state(install_dir)
    if state != "present":
        return state
    try:
        return "present" if any(install_dir.glob("*.exe")) else "absent"
    except (PermissionError, OSError) as exc:
        logger.debug(f"Could not list {install_dir}: {exc}")
        return "unreadable"


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
        """Raises when a candidate was unreadable: a clean absence lets the backend
        prune a live install (incident 326)."""
        user_home = Path(getattr(self, "user_home", None) or Path.home())
        outcome = "absent"
        for install_dir in self._install_dirs(user_home):
            state = _install_state(install_dir)
            if state == "present":
                return {
                    "name": self.tool_name,
                    "version": None,
                    "install_path": str(install_dir),
                }
            if state == "unreadable":
                outcome = "unreadable"
        if outcome == "unreadable":
            raise PermissionError("GitHub Copilot app install dir unreadable")
        return None

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """The installer records no version we can read without running the app."""
        return None
