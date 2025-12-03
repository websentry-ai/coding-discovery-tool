"""
OpenCode detection for macOS.

OpenCode is an AI-powered coding assistant.
This module detects OpenCode installations by checking for the 'opencode' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSOpenCodeDetector(BaseToolDetector):
    """
    Detector for OpenCode installations on macOS systems.
    
    Detection involves:
    - Checking if 'opencode' command is available using 'which opencode'
    - Verifying installation by running 'opencode --version'
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "OpenCode"

    def detect(self) -> Optional[Dict]:
        """
        Detect OpenCode installation on macOS.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Check if opencode command exists
        install_path = self._check_opencode_command()
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
        Extract OpenCode version using 'opencode --version'.
        
        Returns:
            Version string or None if version cannot be determined
        """
        try:
            output = run_command(
                ["opencode", "--version"],
                VERSION_TIMEOUT
            )
            if output:
                # Version output might be just a number or include text
                # Clean up the output to extract version
                version = output.strip()
                return version if version else None
        except Exception as e:
            logger.debug(f"Could not extract OpenCode version: {e}")
        return None

    def _check_opencode_command(self) -> Optional[str]:
        """
        Check if 'opencode' command is available using 'which opencode'.
        
        Returns:
            Path to opencode executable if found, None otherwise
        """
        try:
            output = run_command(
                ["which", "opencode"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip()
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found OpenCode at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for OpenCode command: {e}")
        
        return None

