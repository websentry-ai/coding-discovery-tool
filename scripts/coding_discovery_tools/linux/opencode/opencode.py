"""OpenCode detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class LinuxOpenCodeDetector(BaseToolDetector):
    """Detector for OpenCode installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "OpenCode"

    def detect(self) -> Optional[Dict]:
        install_path = self._check_opencode_command()
        if not install_path:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def get_version(self) -> Optional[str]:
        try:
            output = run_command(["opencode", "--version"], VERSION_TIMEOUT)
            if output:
                version = output.strip()
                return version or None
        except Exception as e:
            logger.debug(f"Could not extract OpenCode version: {e}")
        return None

    def _check_opencode_command(self) -> Optional[str]:
        try:
            output = run_command(["which", "opencode"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found OpenCode at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for OpenCode command: {e}")
        return None
