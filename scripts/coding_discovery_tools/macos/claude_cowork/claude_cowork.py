"""
Claude Cowork detection for macOS.

Cowork is the agentic feature of the Claude Desktop app. We treat it as a
distinct tool from Claude Code (which is the CLI). A device is considered
to have Cowork installed if BOTH:

    - The Claude Desktop app exists at /Applications/Claude.app, AND
    - The on-disk session tree exists at
      ~/Library/Application Support/Claude/local-agent-mode-sessions/

If only the app is present (Cowork never enabled / never used), there is
nothing to report on so we return None.
"""

import logging
import plistlib
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...claude_cowork_skills_helpers import COWORK_SESSIONS_DIR

logger = logging.getLogger(__name__)


CLAUDE_DESKTOP_APP_PATH = Path("/Applications/Claude.app")
CLAUDE_DESKTOP_INFO_PLIST = CLAUDE_DESKTOP_APP_PATH / "Contents" / "Info.plist"


def _get_cowork_sessions_dir() -> Path:
    """Path to Claude Desktop's on-disk Cowork sessions tree."""
    return (
        Path.home()
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

    def detect(self) -> Optional[Dict]:
        sessions_dir = _get_cowork_sessions_dir()
        try:
            app_present = CLAUDE_DESKTOP_APP_PATH.exists()
            sessions_present = sessions_dir.exists() and sessions_dir.is_dir()
        except OSError as e:
            logger.debug(f"Error checking Claude Cowork install: {e}")
            return None

        if not (app_present and sessions_present):
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
            if not CLAUDE_DESKTOP_INFO_PLIST.exists():
                return None
            with CLAUDE_DESKTOP_INFO_PLIST.open("rb") as fh:
                plist = plistlib.load(fh)
            version = plist.get("CFBundleShortVersionString")
            if isinstance(version, str) and version.strip():
                return version.strip()
            return None
        except Exception as e:
            logger.debug(f"Could not extract Claude Cowork version: {e}")
            return None
