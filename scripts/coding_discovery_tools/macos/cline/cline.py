"""
Cline detection for macOS
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSClineDetector(BaseToolDetector):
    """Cline detector for macOS systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cline"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cline CLI installation on macOS.
        
        Cline CLI is installed via: npm install -g cline
        
        Returns:
            Dict with tool info or None if not found
        """
        # Check for CLI only
        return self._check_cli_in_path()
    
    def _check_cli_in_path(self) -> Optional[Dict]:
        """
        Check if Cline CLI is installed and in PATH.
        
        Returns:
            Dict with CLI info or None if not found
        """
        output = run_command(["which", "cline"], VERSION_TIMEOUT)
        if output:
            logger.debug(f"Found Cline CLI at: {output}")
            version = self.get_version()
            logger.debug(f"Cline version from CLI check: {version}")
            return {
                "name": self.tool_name,
                "version": version,
                "install_path": output
            }
        logger.debug("Cline CLI not found in PATH")
        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Cline CLI version.
        
        Uses CLI command: cline version (outputs "Cline CLI Version: 1.0.6")
        
        Returns:
            Version string or None
        """
        try:
            import subprocess
            result = subprocess.run(
                ["cline", "version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT
            )
            if result.returncode == 0:
                output = result.stdout.strip() if result.stdout else ""
                if not output and result.stderr:
                    output = result.stderr.strip()
                
                if output:
                    logger.debug(f"Cline CLI version output: {output}")
                    # Extract version from "Cline CLI Version: 1.0.6"
                    from ...utils import extract_version_number
                    version = extract_version_number(output)
                    if version:
                        logger.debug(f"Extracted Cline CLI version: {version}")
                        return version
        except FileNotFoundError:
            logger.debug("Cline CLI not found")
        except subprocess.TimeoutExpired:
            logger.debug("Cline version command timed out")
        except Exception as e:
            logger.debug(f"Could not get version from CLI: {e}")
        
        logger.debug("Could not determine Cline CLI version")
        return None

