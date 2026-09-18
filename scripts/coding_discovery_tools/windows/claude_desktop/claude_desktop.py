"""
Claude Desktop detection for Windows.

Claude Desktop is the claude.ai client. Cowork and the Claude Code it hosts are
reported as their own tools, so this row is the desktop app itself. A device
counts as having it when BOTH:

    - A Claude Desktop installation is discoverable on disk, AND
    - The per-user data directory exists at ``%APPDATA%/Claude/``.

Anthropic documents claude_desktop_config.json as living in that directory, and
it is created on first run, so an install with no data directory has never been
opened and there is nothing to report on.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import dir_state, fail_if_anomalous
from ..claude_cowork.claude_cowork import claude_install_candidates

logger = logging.getLogger(__name__)


def claude_data_dir(user_home: Path) -> Path:
    """Claude Desktop's per-user data directory (``%APPDATA%/Claude``)."""
    return user_home / "AppData" / "Roaming" / "Claude"


class WindowsClaudeDesktopDetector(BaseToolDetector):
    """Claude Desktop detector for Windows."""

    @property
    def tool_name(self) -> str:
        return "Claude Desktop"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under an MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        outcome = "absent"
        for candidate in claude_install_candidates(self._scan_home(user_home)):
            state = dir_state(candidate)
            if state == "present":
                return candidate
            if state == "unreadable":
                outcome = "unreadable"
        if outcome == "unreadable":
            raise PermissionError("Claude Desktop install dir unreadable")
        return None

    def detect(self) -> Optional[Dict]:
        home = self._scan_home()
        data_dir = claude_data_dir(home)

        state = dir_state(data_dir)
        if state == "unreadable":
            fail_if_anomalous(home, f"Claude Desktop data dir unreadable: {data_dir}")
            return None
        if state != "present":
            logger.debug("No Claude Desktop data dir at %s", data_dir)
            return None

        try:
            app_install = self._find_install_dir()
        except OSError as e:
            fail_if_anomalous(home, str(e))
            return None
        if app_install is None:
            logger.debug("Claude data dir %s has no install; residue, not an install", data_dir)
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(app_install),
            "install_path": str(data_dir),
        }

    def get_version(self, app_install: Optional[Path] = None) -> Optional[str]:
        """Windows ships no single documented version source, so the backend
        treats it as unknown. Matches the Windows Cowork detector."""
        return None
