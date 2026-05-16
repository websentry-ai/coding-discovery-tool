"""Replit detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


class LinuxReplitDetector(BaseToolDetector):
    """Detector for Replit installations on Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Replit"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            result = self._check_user_data_directory(user_home)
            if result:
                return result

        which_path = self._check_replit_command()
        if which_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": which_path,
            }

        return None

    def get_version(self) -> Optional[str]:
        return None

    def _check_user_data_directory(self, user_home: Path) -> Optional[Dict]:
        candidates = [
            user_home / ".config" / "Replit",
            user_home / ".local" / "share" / "Replit",
        ]
        for path in candidates:
            try:
                if path.exists() and path.is_dir():
                    logger.debug(f"Found Replit user data at: {path}")
                    return {
                        "name": self.tool_name,
                        "version": self.get_version(),
                        "install_path": str(path),
                    }
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check Replit path {path}: {e}")
        return None

    def _check_replit_command(self) -> Optional[str]:
        try:
            output = run_command(["which", "replit"], VERSION_TIMEOUT)
            if output:
                path = output.strip()
                if Path(path).exists():
                    return path
        except Exception as e:
            logger.debug(f"Could not check for replit command: {e}")
        return None
