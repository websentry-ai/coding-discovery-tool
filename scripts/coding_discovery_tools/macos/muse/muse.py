"""
Muse detection for macOS.

Muse is Meta's personal agent desktop app (``/Applications/Muse.app``,
CFBundleIdentifier ``com.meta.endo``). The agent runs on Meta's cloud computer:
its skills are stored there, and it has no user-configurable MCP servers. On the
device it keeps only a preferences plist (which also carries identity data) and
analytics, so the bundle is reported and nothing else is collected.

"Muse.app" is also the name of an unrelated whiteboard app, so the bundle
identifier gates detection.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import (
    macos_app_candidates,
    read_bundle_identifier,
    read_bundle_version,
)

logger = logging.getLogger(__name__)

MUSE_BUNDLE_ID = "com.meta.endo"


class MacOSMuseDetector(BaseToolDetector):
    """Detector for Meta's Muse desktop app on macOS systems."""

    # A class attribute so tests can point it at a temp bundle.
    APP_PATH = Path("/Applications/Muse.app")

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Muse"

    def detect(self) -> Optional[Dict]:
        """
        Detect a Muse installation on macOS.

        Returns:
            Dict with name/version/install_path, or None when no Meta Muse bundle exists.
        """
        app_path = self._resolve_app_path()
        if app_path is None:
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version(app_path) or "Unknown",
            "install_path": str(app_path),
        }

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """Read ``CFBundleShortVersionString`` from the bundle's Info.plist."""
        try:
            if app_path is None:
                app_path = self._resolve_app_path()
            if app_path is None:
                return None
            return read_bundle_version(app_path)
        except Exception as exc:
            logger.debug(f"Could not extract Muse version: {exc}")
        return None

    def _resolve_app_path(self) -> Optional[Path]:
        """The installed Meta Muse bundle: machine-wide, else the scanned user's own."""
        for candidate in macos_app_candidates(self.APP_PATH, self.user_home):
            try:
                if candidate.exists() and read_bundle_identifier(candidate) == MUSE_BUNDLE_ID:
                    return candidate
            except (PermissionError, OSError):
                continue
        return None
