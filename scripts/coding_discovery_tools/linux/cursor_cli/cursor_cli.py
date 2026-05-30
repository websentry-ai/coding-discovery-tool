"""Cursor CLI detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number

logger = logging.getLogger(__name__)


class LinuxCursorCliDetector(BaseToolDetector):
    """Detector for Cursor CLI installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Cursor CLI"

    def detect(self) -> Optional[Dict]:
        install_path = self._check_cursor_command()
        if not install_path:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def get_version(self) -> Optional[str]:
        try:
            output = run_command(["cursor", "--version"], VERSION_TIMEOUT)
            if output:
                return extract_version_number(output.strip())
        except Exception as e:
            logger.debug(f"Could not extract Cursor CLI version: {e}")
        return None

    def _check_cursor_command(self) -> Optional[str]:
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
