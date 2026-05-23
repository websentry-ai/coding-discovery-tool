"""Kilo Code detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


class LinuxKiloCodeDetector(BaseToolDetector):
    """Detector for Kilo Code installations on Linux systems."""

    SUPPORTED_IDES = ["Code", "Cursor"]
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"

    @property
    def tool_name(self) -> str:
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            result = self._check_user_for_kilocode(user_home)
            if result:
                return result
        return None

    def get_version(self) -> Optional[str]:
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        extension_path = None
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_kilocode_extension(user_home, ide_name)
            if extension_path:
                break

        if not extension_path:
            return None

        return {
            "name": self.tool_name,
            "version": "Unknown",
            "install_path": str(extension_path),
        }

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        extension_dir = (
            user_home / ".config" / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID
        )
        try:
            if extension_dir.exists() and extension_dir.is_dir():
                logger.debug(f"Found Kilo Code extension directory for {ide_name} at: {extension_dir}")
                return extension_dir
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Kilo Code extension path for {ide_name}: {e}")
        return None
