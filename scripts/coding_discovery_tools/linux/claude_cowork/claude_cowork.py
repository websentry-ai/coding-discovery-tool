"""
Claude Cowork detection for Linux.

Cowork is the agentic feature of the Claude Desktop app. We treat it as a
distinct tool from Claude Code (which is the CLI). A device is considered
to have Cowork installed if BOTH:

    - A Claude Desktop installation is discoverable on disk, AND
    - The on-disk session tree exists at
      ~/.config/Claude/local-agent-mode-sessions/

(Claude Desktop is an Electron app and follows the XDG convention of
storing its data under ~/.config/Claude/ on Linux.)

If only the app is present (Cowork never enabled / never used) there is
nothing on disk to report, and if only the sessions tree is present (app
uninstalled, config residue left behind) it is not a real install — in both
cases we return None. When running as root we check every user's home (and
/root) so MDM-style deployments are covered.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...claude_cowork_skills_helpers import COWORK_SESSIONS_DIR
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


def _sessions_dir_for_user(user_home: Path) -> Path:
    """Path to Claude Desktop's on-disk Cowork sessions tree for a Linux user."""
    return user_home / ".config" / "Claude" / COWORK_SESSIONS_DIR


def _candidate_install_dirs() -> List[Path]:
    """Common locations where Claude Desktop is installed on Linux."""
    return [
        Path("/opt/Claude"),
        Path("/usr/lib/claude"),
        Path("/usr/lib/Claude"),
        Path("/usr/share/claude"),
    ]


class LinuxClaudeCoworkDetector(BaseToolDetector):
    """Claude Cowork detector for Linux."""

    @property
    def tool_name(self) -> str:
        return "Claude Cowork"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            sessions_dir = _sessions_dir_for_user(user_home)
            try:
                if not (sessions_dir.exists() and sessions_dir.is_dir()):
                    continue
            except OSError as e:
                logger.debug(f"Error checking Cowork sessions dir {sessions_dir}: {e}")
                continue

            # Require the Claude Desktop install to be present. The per-user
            # ``~/.config/Claude`` tree (which holds the sessions dir) survives
            # an uninstall (anthropics/claude-code#25013), so reporting on the
            # sessions dir alone produced false positives. Gate on a real
            # install dir.
            app_install = self._find_install_dir()
            if app_install is None:
                logger.debug(
                    "Cowork sessions present under %s but no Claude Desktop "
                    "install found; treating as residue (not installed).",
                    user_home,
                )
                continue

            return {
                "name": self.tool_name,
                "version": self.get_version(),
                # Report the sessions dir (consistent with the macOS detector and
                # the central ``_detect_claude_cowork`` path). ``app_install`` is
                # the gate, not the reported path.
                "install_path": str(sessions_dir),
            }
        return None

    def get_version(self) -> Optional[str]:
        """Best-effort version detection.

        Claude Desktop on Linux has no stable, documented version-metadata
        location, so rather than guessing wrong we return None and let the
        backend treat the version as unknown — matching the Windows detector.
        """
        return None

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        # ``user_home`` is accepted for a uniform call signature with the central
        # path; Linux install dirs are machine-global so it is unused here.
        for candidate in _candidate_install_dirs():
            try:
                if candidate.exists() and candidate.is_dir():
                    return candidate
            except OSError:
                continue
        return None
