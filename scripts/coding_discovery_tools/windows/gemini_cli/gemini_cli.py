"""
Gemini CLI detection for Windows.

Gemini CLI is a command-line tool for interacting with Google's Gemini AI.
This module detects Gemini CLI installations by checking for the 'gemini' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict
import shutil

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsGeminiCliDetector(BaseToolDetector):
    """
    Detector for Gemini CLI installations on Windows systems.
    
    Detection involves:
    - Checking if 'gemini' command is available using 'where gemini'
    - Verifying installation by running 'gemini --version'
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Gemini CLI"

    def detect(self) -> Optional[Dict]:
        """
        Detect Gemini CLI installation on Windows.
        
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
            # On Windows, npm-installed commands are .CMD files, so use shell=True
            import subprocess
            result = subprocess.run(
                ["gemini", "--version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
                shell=True  # Required for npm .CMD files on Windows
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                if output:
                    # Version output might be just a number or include text
                    # Clean up the output to extract version
                    version = output.strip()
                    logger.debug(f"Extracted Gemini CLI version: {version}")
                    return version if version else None
            else:
                logger.debug(f"Gemini CLI version command failed with return code: {result.returncode}")
        except Exception as e:
            logger.debug(f"Could not extract Gemini CLI version: {e}", exc_info=True)
        return None

    def _check_gemini_command(self) -> Optional[str]:
        """
        Check if 'gemini' command is available using 'where gemini' (Windows equivalent of 'which').
        
        Returns:
            Path to gemini executable if found, None otherwise
        """
        try:
            # On Windows, use 'where' command instead of 'which'
            output = run_command(
                ["where", "gemini"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip().split('\n')[0].strip()  # Get first result
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found Gemini CLI at: {path}")
                    return path
            
            # Fallback: check using shutil.which (Python's built-in)
            gemini_path = shutil.which("gemini")
            if gemini_path:
                path = Path(gemini_path)
                if path.exists():
                    logger.debug(f"Found Gemini CLI via shutil.which at: {path}")
                    return str(path)
        except Exception as e:
            logger.debug(f"Could not check for Gemini CLI command: {e}")
        return None

