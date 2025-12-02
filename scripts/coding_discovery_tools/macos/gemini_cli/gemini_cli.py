"""
Gemini CLI detection for macOS.

Gemini CLI is a command-line tool for interacting with Google's Gemini AI.
This module detects Gemini CLI installations by checking for the 'gemini' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSGeminiCliDetector(BaseToolDetector):
    """
    Detector for Gemini CLI installations on macOS systems.
    
    Detection involves:
    - Checking if 'gemini' command is available using 'which gemini'
    - Verifying installation by running 'gemini --version'
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Gemini CLI"

    def detect(self) -> Optional[Dict]:
        """
        Detect Gemini CLI installation on macOS.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Check if gemini command exists
        install_path = self._check_gemini_command()
        if not install_path:
            return None

        # Get version
        version = self.get_version()
        
        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": install_path
        }

    def get_version(self) -> Optional[str]:
        """
        Extract Gemini CLI version using 'gemini --version'.
        
        Returns:
            Version string or None if version cannot be determined
        """
        try:
            output = run_command(
                ["gemini", "--version"],
                VERSION_TIMEOUT
            )
            if output:
                # Version output might be just a number or include text
                # Clean up the output to extract version
                version = output.strip()
                return version if version else None
        except Exception as e:
            logger.debug(f"Could not extract Gemini CLI version: {e}")
        return None

    def _check_gemini_command(self) -> Optional[str]:
        """
        Check if 'gemini' command is available using 'which gemini'.
        
        Returns:
            Path to gemini executable if found, None otherwise
        """
        try:
            output = run_command(
                ["which", "gemini"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip()
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found Gemini CLI at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Gemini CLI command: {e}")
        return None

