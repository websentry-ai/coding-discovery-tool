"""
Junie detection for macOS.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)


class MacOSJunieDetector(BaseToolDetector):
    """
    Detector for Junie installations on macOS systems.  
    """

    JUNIE_DIR_NAME = ".junie"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Junie"

    def detect(self) -> Optional[Dict]:
        """
        Detect Junie installation on macOS.
        """
        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
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
        """
        Extract Junie version.
        """
        result = self.detect()
        if result:
            return result.get('version')
        return None

    def _detect_junie_for_user(self, user_home: Path) -> Optional[Dict]:
        """
        Detect Junie installation for a specific user.
        """
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
        """
        Try to extract Junie version from configuration files.
        """
        config_files = [
            junie_dir / "config.json",
            junie_dir / "settings.json",
        ]

        for config_file in config_files:
            try:
                if config_file.exists():
                    import json
                    with open(config_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        if 'version' in data:
                            return data['version']
            except (json.JSONDecodeError, OSError, PermissionError) as e:
                logger.debug(f"Could not read config file {config_file}: {e}")
                continue

        return None
