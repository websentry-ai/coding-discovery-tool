"""Gemini CLI detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class LinuxGeminiCliDetector(BaseToolDetector):
    """Detector for Gemini CLI installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Gemini CLI"

    def detect(self) -> Optional[Dict]:
        install_path = self._check_gemini_command()
        if not install_path:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def get_version(self) -> Optional[str]:
        try:
            output = run_command(["gemini", "--version"], VERSION_TIMEOUT)
            if output:
                version = output.strip()
                return version if version else None
        except Exception as e:
            logger.debug(f"Could not extract Gemini CLI version: {e}")
        return None

    def _check_gemini_command(self) -> Optional[str]:
        try:
            output = run_command(["which", "gemini"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found Gemini CLI at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Gemini CLI command: {e}")
        return None
