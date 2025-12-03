"""
Codex detection for macOS.

Codex is a lightweight coding agent that runs in your terminal.
This module detects Codex CLI installations by checking for the 'codex' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSCodexDetector(BaseToolDetector):
    """
    Detector for Codex CLI installations on macOS systems.
    
    Detection involves:
    - Checking if 'codex' command is available using 'which codex'
    - Verifying installation by running 'codex --version'
    - Checking common installation paths:
      - /usr/local/bin/codex (Homebrew on Intel Macs)
      - /opt/homebrew/bin/codex (Homebrew on Apple Silicon Macs)
      - npm global packages directory
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Codex"

    def detect(self) -> Optional[Dict]:
        """
        Detect Codex CLI installation on macOS.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Check if codex command exists
        install_path = self._check_codex_command()
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
        Extract Codex CLI version using 'codex --version'.
        
        Returns:
            Version string or None if version cannot be determined
        """
        try:
            output = run_command(
                ["codex", "--version"],
                VERSION_TIMEOUT
            )
            if output:
                # Version output might be just a number or include text
                # Clean up the output to extract version
                version = output.strip()
                return version if version else None
        except Exception as e:
            logger.debug(f"Could not extract Codex version: {e}")
        return None

    def _check_codex_command(self) -> Optional[str]:
        """
        Check if 'codex' command is available using 'which codex'.
        
        Also checks common installation paths as fallback.
        
        Returns:
            Path to codex executable if found, None otherwise
        """
        # First, try 'which codex'
        try:
            output = run_command(
                ["which", "codex"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip()
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found Codex CLI at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Codex command: {e}")
        
        # Fallback: Check common installation paths
        common_paths = [
            "/usr/local/bin/codex",  # Homebrew on Intel Macs
            "/opt/homebrew/bin/codex",  # Homebrew on Apple Silicon Macs
        ]
        
        for path_str in common_paths:
            path = Path(path_str)
            if path.exists() and path.is_file():
                logger.debug(f"Found Codex CLI at: {path}")
                return str(path)
        
        return None

