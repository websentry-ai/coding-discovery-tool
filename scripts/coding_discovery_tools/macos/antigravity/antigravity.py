"""
Antigravity (Google Gemini) detection for macOS
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSAntigravityDetector(BaseToolDetector):
    """Antigravity (Google Gemini) detector for macOS systems."""

    # Common possible app paths for Antigravity/Gemini
    POSSIBLE_APP_PATHS = [
        Path("/Applications/Antigravity.app"),
        Path("/Applications/Gemini.app"),
        Path("/Applications/Google Gemini.app"),
    ]

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Antigravity"

    def detect(self) -> Optional[Dict]:
        """
        Detect Antigravity installation on macOS.
        
        Checks for .antigravity directories in common locations and also
        checks for installed applications.
        
        Returns:
            Dict with tool info or None if not found
        """
        # Check for installed application
        app_path = self._find_app_path()
        if app_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(app_path),
                "install_path": str(app_path)
            }
        
        # Also check if .antigravity directories exist (indicates tool usage)
        # This is similar to how cursor and windsurf work
        home_path = Path.home()
        if (home_path / ".antigravity").exists():
            return {
                "name": self.tool_name,
                "version": None,
                "install_path": None
            }
        
        return None

    def _find_app_path(self) -> Optional[Path]:
        """
        Find the Antigravity application path.
        
        Returns:
            Path to the app if found, None otherwise
        """
        for app_path in self.POSSIBLE_APP_PATHS:
            if app_path.exists():
                return app_path
        return None

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """
        Extract Antigravity version from macOS Info.plist.
        
        Args:
            app_path: Optional path to the app (if None, will try to find it)
        
        Returns:
            Version string or None
        """
        if app_path is None:
            app_path = self._find_app_path()
        
        if app_path is None:
            return None
        
        try:
            plist_path = app_path / "Contents" / "Info.plist"
            if not plist_path.exists():
                return None

            output = run_command(
                ["defaults", "read", str(plist_path), "CFBundleShortVersionString"],
                VERSION_TIMEOUT
            )
            return output if output else None
        except Exception as e:
            logger.warning(f"Could not extract Antigravity version: {e}")
        return None

