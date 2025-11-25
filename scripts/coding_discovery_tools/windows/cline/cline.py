"""
Cline detection for Windows
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class WindowsClineDetector(BaseToolDetector):
    """Cline detector for Windows systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cline"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cline CLI installation on Windows.
        
        Cline CLI is installed via: npm install -g cline
        
        Returns:
            Dict with tool info or None if not found
        """
        # Check for CLI only
        return self._check_cli_in_path()
    
    def _check_cli_in_path(self) -> Optional[Dict]:
        """
        Check if Cline CLI is installed and in PATH.
        
        On Windows, 'where' can return multiple paths. We check each one
        and use the first valid path that exists.
        
        Returns:
            Dict with CLI info or None if not found
        """
        # Check for cline with different extensions (Windows npm may create .cmd files)
        for ext in ["", ".exe", ".cmd", ".bat"]:
            output = run_command(["where", f"cline{ext}"], VERSION_TIMEOUT)
            if output:
                # Handle multiple paths (where can return multiple results)
                cline_paths = output.split('\n')
                for cline_path in cline_paths:
                    cline_path = cline_path.strip()
                    if cline_path and Path(cline_path).exists():
                        logger.debug(f"Found Cline CLI at: {cline_path}")
                        version = self.get_version()
                        logger.debug(f"Cline version from CLI check: {version}")
                        return {
                            "name": self.tool_name,
                            "version": version,
                            "install_path": cline_path
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

