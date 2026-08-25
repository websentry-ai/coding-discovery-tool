"""
Claude Cowork detection for macOS.

Cowork is the agentic feature of the Claude Desktop app. We treat it as a
distinct tool from Claude Code (which is the CLI). A device is considered
to have Cowork installed if BOTH:

    - A Claude Desktop app bundle is discoverable on disk (machine-wide in
      /Applications, or the user's own ~/Applications for a non-admin
      install), AND
    - The on-disk session tree exists at
      ~/Library/Application Support/Claude/local-agent-mode-sessions/

If only the app is present (Cowork never enabled / never used), there is
nothing to report on so we return None.
"""

import logging
import plistlib
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...claude_cowork_skills_helpers import COWORK_SESSIONS_DIR

logger = logging.getLogger(__name__)


CLAUDE_DESKTOP_APP_PATH = Path("/Applications/Claude.app")


def _candidate_install_dirs(user_home: Path) -> List[Path]:
    """Where macOS puts Claude.app: machine-wide, then the scanned user's own."""
    return [
        CLAUDE_DESKTOP_APP_PATH,
        user_home / "Applications" / "Claude.app",
    ]


def _get_cowork_sessions_dir(user_home: Path) -> Path:
    """Path to Claude Desktop's on-disk Cowork sessions tree."""
    return (
        user_home
        / "Library"
        / "Application Support"
        / "Claude"
        / COWORK_SESSIONS_DIR
    )


class MacOSClaudeCoworkDetector(BaseToolDetector):
    """Claude Cowork detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "Claude Cowork"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        for candidate in _candidate_install_dirs(self._scan_home(user_home)):
            try:
                if candidate.exists() and candidate.is_dir():
                    return candidate
            except OSError:
                continue
        return None

    def detect(self) -> Optional[Dict]:
        sessions_dir = _get_cowork_sessions_dir(self._scan_home())
        try:
            sessions_present = sessions_dir.exists() and sessions_dir.is_dir()
        except OSError as e:
            logger.debug(f"Error checking Claude Cowork install: {e}")
            return None

        if not (sessions_present and self._find_install_dir()):
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(),
            "install_path": str(sessions_dir),
        }

    def get_version(self) -> Optional[str]:
        """
        Read CFBundleShortVersionString from Claude Desktop's Info.plist.
        Returns None on any error — version is informational and must not
        block detection.
        """
        try:
            app_bundle = self._find_install_dir()
            if app_bundle is None:
                return None
            info_plist = app_bundle / "Contents" / "Info.plist"
            if not info_plist.exists():
                return None
            with info_plist.open("rb") as fh:
                plist = plistlib.load(fh)
            version = plist.get("CFBundleShortVersionString")
            if isinstance(version, str) and version.strip():
                return version.strip()
            return None
        except Exception as e:
            logger.debug(f"Could not extract Claude Cowork version: {e}")
            return None
