"""
Cursor CLI detection for macOS.

Cursor CLI is the standalone agentic terminal tool ``cursor-agent`` — distinct
from the Cursor IDE's ``cursor`` launcher. This module gates on ``cursor-agent``;
gating on ``cursor`` mislabelled the IDE launcher as the CLI.
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
    - Checking if the ``cursor-agent`` command is available via ``which cursor-agent``
    - Reading the version via ``cursor-agent --version``
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

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """
        Extract Cursor CLI version using ``cursor-agent --version``. Best-effort.

        Args:
            binary: When provided, probe THIS exact ``cursor-agent`` path (the one
                detection already resolved for the user). Under a root/MDM
                all-users scan the user's ``~/.local/bin/cursor-agent`` is NOT on
                root's PATH, so the bare ``cursor-agent`` probe reads nothing and
                the version is "Unknown" — probing the resolved binary directly is
                what populates it. When ``None`` (any no-arg caller), keep the
                legacy bare-PATH probe so behaviour is unchanged.

        Returns:
            Version string or None if version cannot be determined
        """
        try:
            command = [str(binary), "--version"] if binary else ["cursor-agent", "--version"]
            output = run_command(command, VERSION_TIMEOUT)
            if output:
                return extract_version_number(output.strip())
        except Exception as e:
            logger.debug(f"Could not extract Cursor CLI version: {e}")
        return None

    def _check_cursor_command(self) -> Optional[str]:
        """
        Check if the ``cursor-agent`` command is available via ``which cursor-agent``.

        Probing ``cursor-agent`` (NOT ``cursor``) is what avoids mislabelling the
        Cursor IDE launcher as the CLI.

        Returns:
            Path to the ``cursor-agent`` executable if found, None otherwise
        """
        try:
            output = run_command(["which", "cursor-agent"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found Cursor CLI (cursor-agent) at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Cursor CLI command: {e}")
        return None
