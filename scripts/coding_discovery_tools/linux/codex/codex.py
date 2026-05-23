"""Codex detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command

logger = logging.getLogger(__name__)


class LinuxCodexDetector(BaseToolDetector):
    """Detector for Codex CLI installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Codex"

    def detect(self) -> Optional[Dict]:
        install_path = self._check_codex_command()
        if not install_path:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def get_version(self) -> Optional[str]:
        try:
            output = run_command(["codex", "--version"], VERSION_TIMEOUT)
            if output:
                version = output.strip()
                return version if version else None
        except Exception as e:
            logger.debug(f"Could not extract Codex version: {e}")
        return None

    def _check_codex_command(self) -> Optional[str]:
        try:
            output = run_command(["which", "codex"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    logger.debug(f"Found Codex CLI at: {path}")
                    return path
        except Exception as e:
            logger.debug(f"Could not check for Codex command: {e}")

        common_paths = [
            "/usr/local/bin/codex",
            "/usr/bin/codex",
        ]
        for user_home in self._get_user_homes():
            common_paths.append(str(user_home / ".local" / "bin" / "codex"))

        for path_str in common_paths:
            path = Path(path_str)
            if path.exists() and path.is_file():
                logger.debug(f"Found Codex CLI at: {path}")
                return str(path)

        return None

    def _get_user_homes(self):
        from ...linux_extraction_helpers import get_linux_user_homes
        return get_linux_user_homes()
