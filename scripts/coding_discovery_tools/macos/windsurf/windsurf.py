"""
Windsurf IDE detection for macOS
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSWindsurfDetector(BaseToolDetector):
    """Windsurf IDE detector for macOS systems."""

    DEFAULT_APP_PATH = Path("/Applications/Windsurf.app")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Windsurf"

    def detect(self) -> Optional[Dict]:
        """
        Detect Windsurf installation on macOS.
        
        Returns:
            Dict with tool info or None if not found
        """
        if not self.DEFAULT_APP_PATH.exists():
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(),
            "install_path": str(self.DEFAULT_APP_PATH)
        }

    def get_version(self) -> Optional[str]:
        """
        Extract Windsurf version from macOS Info.plist.
        
        Returns:
            Version string or None
        """
        try:
            plist_path = self.DEFAULT_APP_PATH / "Contents" / "Info.plist"
            if not plist_path.exists():
                return None

            output = run_command(
                ["defaults", "read", str(plist_path), "CFBundleShortVersionString"],
                VERSION_TIMEOUT
            )
            return output if output else None
        except Exception as e:
            logger.warning(f"Could not extract Windsurf version: {e}")
        return None

