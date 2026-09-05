"""
Cursor IDE detection for macOS
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...macos_extraction_helpers import macos_app_candidates
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
        app_path = self._resolve_app_path()
        if app_path is None:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(app_path),
            "install_path": str(app_path)
        }

    def _resolve_app_path(self) -> Optional[Path]:
        """The installed bundle: machine-wide, else the scanned user's own."""
        user_home = getattr(self, 'user_home', None)
        for candidate in macos_app_candidates(self.DEFAULT_APP_PATH, user_home):
            try:
                if candidate.exists():
                    return candidate
            except (PermissionError, OSError):
                continue
        return None

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """
        Extract Cursor version from macOS Info.plist.
        
        Returns:
            Version string or None
        """
        try:
            if app_path is None:
                app_path = self._resolve_app_path()
            if app_path is None:
                return None
            plist_path = app_path / "Contents" / "Info.plist"
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

