"""
Cursor IDE detection for macOS
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from .cursor_rules_extractor import MacOSCursorRulesExtractor

logger = logging.getLogger(__name__)


class MacOSCursorDetector(BaseToolDetector):
    """Cursor IDE detector for macOS systems."""

    DEFAULT_APP_PATH = Path("/Applications/Cursor.app")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cursor"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cursor installation on macOS.
        
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
        Extract Cursor version from macOS Info.plist.
        
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
            logger.warning(f"Could not extract Cursor version: {e}")
        return None

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on the machine.
        
        Returns:
            List of rule file dicts with metadata
        """
        extractor = MacOSCursorRulesExtractor()
        return extractor.extract_all_cursor_rules()

