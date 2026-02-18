"""
Cursor CLI detection for macOS.

Cursor CLI is a command-line tool for the Cursor IDE.
This module detects Cursor CLI installations by checking for the 'cursor' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number

logger = logging.getLogger(__name__)


class MacOSCursorCliDetector(BaseToolDetector):
    """
    Detector for Cursor CLI installations on macOS systems.

    Detection involves:
    - Checking if 'cursor' command is available using 'which cursor'
    - Verifying installation by running 'cursor --version'
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cursor CLI"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cursor CLI installation on macOS.

        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        install_path = self._check_cursor_command()
        if not install_path:
            return None

        version = self.get_version()

        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": install_path
        }

    def get_version(self) -> Optional[str]:
        """
        Extract Cursor CLI version using 'cursor --version'.

        Returns:
            Version string or None if version cannot be determined
        """
        try:
            output = run_command(["cursor", "--version"], VERSION_TIMEOUT)
            if output:
                return extract_version_number(output.strip())
        except Exception as e:
            logger.debug(f"Could not extract Cursor CLI version: {e}")
        return None

    def _check_cursor_command(self) -> Optional[str]:
        """
        Check if 'cursor' command is available using 'which cursor'.

        Returns:
            Path to cursor executable if found, None otherwise
        """
        try:
            output = run_command(["which", "cursor"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found Cursor CLI at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Cursor CLI command: {e}")
        return None
