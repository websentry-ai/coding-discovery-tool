"""Antigravity detection for Linux."""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...linux_extraction_helpers import get_linux_user_homes

logger = logging.getLogger(__name__)


class LinuxAntigravityDetector(BaseToolDetector):
    """Antigravity detector for Linux systems."""

    @property
    def tool_name(self) -> str:
        return "Antigravity"

    def detect(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            antigravity_dir = user_home / ".antigravity"
            if antigravity_dir.exists() and antigravity_dir.is_dir():
                return {
                    "name": self.tool_name,
                    "version": None,
                    "install_path": str(antigravity_dir),
                }
        return None

    def get_version(self) -> Optional[str]:
        return None
