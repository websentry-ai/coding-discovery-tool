"""
Codex detection for Windows.

Codex is a lightweight coding agent that runs in your terminal.
This module detects Codex CLI installations by checking for the 'codex' command.
"""

import logging
from pathlib import Path
from typing import Optional, Dict
import shutil

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsCodexDetector(BaseToolDetector):
    """
    Detector for Codex CLI installations on Windows systems.
    
    Detection involves:
    - Checking if 'codex' command is available using 'where codex'
    - Verifying installation by running 'codex --version'
    - Checking common installation paths:
      - npm global packages directory
      - User's AppData\Roaming\npm directory
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Codex"

    def detect(self) -> Optional[Dict]:
        """
        Detect Codex CLI installation on Windows.
        
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
            # On Windows, npm-installed commands are .CMD files, so use shell=True
            import subprocess
            result = subprocess.run(
                ["codex", "--version"],
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
                    logger.debug(f"Extracted Codex version: {version}")
                    return version if version else None
            else:
                logger.debug(f"Codex version command failed with return code: {result.returncode}")
        except Exception as e:
            logger.debug(f"Could not extract Codex version: {e}", exc_info=True)
        return None

    def _check_codex_command(self) -> Optional[str]:
        """
        Check if 'codex' command is available using 'where codex' (Windows equivalent of 'which').
        
        Also checks common installation paths as fallback.
        
        Returns:
            Path to codex executable if found, None otherwise
        """
        try:
            # On Windows, use 'where' command instead of 'which'
            output = run_command(
                ["where", "codex"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip().split('\n')[0].strip()  # Get first result
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found Codex CLI at: {path}")
                    return path
            
            # Fallback: check using shutil.which (Python's built-in)
            codex_path = shutil.which("codex")
            if codex_path:
                path = Path(codex_path)
                if path.exists():
                    logger.debug(f"Found Codex CLI via shutil.which at: {path}")
                    return str(path)
        except Exception as e:
            logger.debug(f"Could not check for Codex command: {e}")
        
        # Fallback: Check common installation paths
        user_home = Path.home()
        common_paths = [
            user_home / "AppData" / "Roaming" / "npm" / "codex.cmd",
            user_home / "AppData" / "Roaming" / "npm" / "codex",
        ]
        
        for path in common_paths:
            if path.exists() and path.is_file():
                logger.debug(f"Found Codex CLI at: {path}")
                return str(path)
        
        return None

