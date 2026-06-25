"""
Claude Cowork detection for Windows.

A device is considered to have Cowork installed if BOTH:

    - A Claude Desktop installation is discoverable on disk, AND
    - The on-disk session tree exists at
      ``%APPDATA%/Claude/local-agent-mode-sessions/``.

If only the app is present (Cowork never enabled / never used), there is
nothing on disk to report so we return None.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...claude_cowork_skills_helpers import COWORK_SESSIONS_DIR

logger = logging.getLogger(__name__)


def _get_cowork_sessions_dir() -> Optional[Path]:
    """Path to Claude Desktop's on-disk Cowork sessions tree on Windows.

    Returns None when ``%APPDATA%`` is not set (e.g. SYSTEM context with no
    profile). The detector treats that the same as "not installed".
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "Claude" / COWORK_SESSIONS_DIR


def _candidate_install_dirs(user_home: Path) -> List[Path]:
    """Common locations where Claude Desktop is installed on Windows.

    ``user_home`` is the home of the user being scanned (not necessarily the
    scanner's own home): under an admin/MDM multi-user scan the per-user
    ``AppData\\Local\\Programs\\Claude`` install lives under the scanned user's
    profile, so probing ``Path.home()`` would miss it.
    """
    return [
        user_home / "AppData" / "Local" / "Programs" / "Claude",
        user_home / "AppData" / "Local" / "Programs" / "claude",
        user_home / "AppData" / "Local" / "AnthropicClaude",
        Path("C:\\Program Files") / "Claude",
        Path("C:\\Program Files (x86)") / "Claude",
    ]


class WindowsClaudeCoworkDetector(BaseToolDetector):
    """Claude Cowork detector for Windows."""

    @property
    def tool_name(self) -> str:
        return "Claude Cowork"

    def detect(self) -> Optional[Dict]:
        sessions_dir = _get_cowork_sessions_dir()
        if sessions_dir is None:
            return None

        try:
            sessions_present = sessions_dir.exists() and sessions_dir.is_dir()
        except OSError as e:
            logger.debug(f"Error checking Cowork sessions dir {sessions_dir}: {e}")
            return None

        if not sessions_present:
            return None

        # Require the Claude Desktop install to be present. The per-user
        # ``%APPDATA%\Claude`` tree (which holds the sessions dir) survives an
        # uninstall (anthropics/claude-code#25013), so reporting on the sessions
        # dir alone produced false positives. Gate on a real install dir.
        app_install = self._find_install_dir()
        if app_install is None:
            logger.debug(
                "Cowork sessions present but no Claude Desktop install found; "
                "treating as residue (not installed)."
            )
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(),
            # Report the sessions dir (consistent with the macOS detector and the
            # central ``_detect_claude_cowork`` path). ``app_install`` is the gate,
            # not the reported path.
            "install_path": str(sessions_dir),
        }

    def get_version(self) -> Optional[str]:
        """Best-effort version detection.

        Claude Desktop on Windows ships installer metadata in several
        possible locations; rather than guessing wrong we return None and
        let the backend treat the version as unknown. This matches the
        behavior we use when version detection fails for other tools.
        """
        return None

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        home = user_home or getattr(self, "user_home", None) or Path.home()
        for candidate in _candidate_install_dirs(Path(home)):
            try:
                if candidate.exists() and candidate.is_dir():
                    return candidate
            except OSError:
                continue
        return None
