"""
OpenCode detection for Windows.

OpenCode is an AI-powered coding assistant.
This module detects OpenCode installations by checking for the 'opencode' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict
import shutil

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsOpenCodeDetector(BaseToolDetector):
    """
    Detector for OpenCode installations on Windows systems.
    
    Detection involves:
    - Checking if 'opencode' command is available using 'where opencode'
    - Verifying installation by running 'opencode --version'
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "OpenCode"

    def detect(self) -> Optional[Dict]:
        """
        Detect OpenCode installation on Windows.
        
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
            # On Windows, npm-installed commands are .CMD files, so use shell=True
            import subprocess
            result = subprocess.run(
                ["opencode", "--version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
                shell=True  # Required for npm .CMD files on Windows
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                if output:
                    version = output.strip()
                    logger.debug(f"Extracted OpenCode version: {version}")
                    return version if version else None
            else:
                logger.debug(f"OpenCode version command failed with return code: {result.returncode}")
        except Exception as e:
            logger.debug(f"Could not extract OpenCode version: {e}", exc_info=True)
        return None

    def _check_opencode_command(self) -> Optional[str]:
        """
        Check if 'opencode' command is available using 'where opencode' (Windows equivalent of 'which').
        
        Returns:
            Path to opencode executable if found, None otherwise
        """
        try:
            # On Windows, use 'where' command instead of 'which'
            output = run_command(
                ["where", "opencode"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip().split('\n')[0].strip()  # Get first result
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found OpenCode at: {path}")
                    return path
            
            # Fallback: check using shutil.which (Python's built-in)
            opencode_path = shutil.which("opencode")
            if opencode_path:
                path = Path(opencode_path)
                if path.exists():
                    logger.debug(f"Found OpenCode via shutil.which at: {path}")
                    return str(path)
        except Exception as e:
            logger.debug(f"Could not check for OpenCode command: {e}")
        
        return None

