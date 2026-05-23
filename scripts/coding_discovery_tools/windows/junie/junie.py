"""
Junie detection for Windows.

Junie is JetBrains' AI coding agent. On Windows it stores its config in a
user-level ``.junie`` directory (``%USERPROFILE%\\.junie``), the same layout
used on macOS/Linux. When running as administrator we scan every user's
profile under ``C:\\Users``; otherwise just the current user's home.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...windows_extraction_helpers import is_running_as_admin

logger = logging.getLogger(__name__)


class WindowsJunieDetector(BaseToolDetector):
    """Detector for Junie installations on Windows systems."""

    JUNIE_DIR_NAME = ".junie"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Junie"

    def detect(self) -> Optional[Dict]:
        """Detect Junie installation on Windows."""
        if is_running_as_admin():
            users_dir = Path(Path.home().anchor) / "Users"
            if users_dir.exists():
                excluded = {'public', 'default', 'default user', 'all users'}
                for user_dir in users_dir.iterdir():
                    if not user_dir.is_dir() or user_dir.name.startswith('.'):
                        continue
                    if user_dir.name.lower() in excluded:
                        continue
                    try:
                        result = self._detect_junie_for_user(user_dir)
                        if result:
                            return result
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping user directory {user_dir}: {e}")
                        continue
            return None
        else:
            return self._detect_junie_for_user(Path.home())

    def get_version(self) -> Optional[str]:
        """Extract Junie version."""
        result = self.detect()
        if result:
            return result.get('version')
        return None

    def _detect_junie_for_user(self, user_home: Path) -> Optional[Dict]:
        """Detect Junie installation for a specific user."""
        junie_dir = user_home / self.JUNIE_DIR_NAME

        if not junie_dir.exists() or not junie_dir.is_dir():
            return None

        logger.debug(f"Found Junie directory at: {junie_dir}")

        version = self._get_version_from_config(junie_dir)

        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": str(junie_dir)
        }

    def _get_version_from_config(self, junie_dir: Path) -> Optional[str]:
        """Try to extract Junie version from configuration files."""
        config_files = [
            junie_dir / "config.json",
            junie_dir / "settings.json",
        ]

        for config_file in config_files:
            try:
                if config_file.exists():
                    with open(config_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if 'version' in data:
                            return data['version']
            except (json.JSONDecodeError, OSError, PermissionError) as e:
                logger.debug(f"Could not read config file {config_file}: {e}")
                continue

        return None
