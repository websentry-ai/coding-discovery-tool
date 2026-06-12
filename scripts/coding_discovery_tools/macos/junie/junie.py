"""
Junie detection for macOS.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from ...macos_extraction_helpers import is_running_as_root
from ...user_tool_detector import find_junie_binary_for_user

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

        Gates on a real install signal — the Junie CLI binary OR the Junie
        plugin in a JetBrains IDE — not on the ``~/.junie`` directory, which is
        user-authored guidelines residue that survives uninstall. ``~/.junie``
        is still used as the version source.
        """
        junie_bin = find_junie_binary_for_user(user_home)
        install_path: Optional[str] = junie_bin

        if not install_path:
            install_path = self._has_junie_jetbrains_plugin(user_home)

        if not install_path:
            return None

        logger.debug(f"Detected Junie install signal at: {install_path}")

        version = self._get_version_from_config(user_home / self.JUNIE_DIR_NAME)

        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": install_path
        }

    def _has_junie_jetbrains_plugin(self, user_home: Path) -> Optional[str]:
        """Return an install_path if the Junie plugin is present in a JetBrains
        IDE belonging to ``user_home``, else None.

        Scoping matters: ``MacOSJetBrainsDetector.detect()`` ignores ``user_home``
        and, under root, scans every user under ``/Users``. Calling it here would
        attribute another user's Junie plugin to whichever user is currently
        being scanned (cross-user false positive). Instead we drive the
        detector's per-user config-dir scan for ``user_home`` only, then enrich
        with plugins. The JetBrains detector itself is never modified — only its
        existing per-user methods are reused read-only.
        """
        try:
            jetbrains_detector = MacOSJetBrainsDetector()
            scoped_ides = jetbrains_detector._scan_jetbrains_config_dir(user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"JetBrains scan for Junie failed under {user_home}: {e}")
            return None

        for ide in scoped_ides:
            config_path = ide.get("config_path")
            if not self._path_under_user_home(config_path, user_home):
                continue
            try:
                plugins = jetbrains_detector._get_plugins(config_path)
            except (PermissionError, OSError) as e:
                logger.debug(f"Plugin scan for Junie failed under {config_path}: {e}")
                continue
            for plugin_name in plugins:
                if "junie" in plugin_name.lower():
                    return config_path
        return None

    @staticmethod
    def _path_under_user_home(config_path: Optional[str], user_home: Path) -> bool:
        """True if ``config_path`` is inside ``user_home`` (strict scoping guard)."""
        if not config_path:
            return False
        try:
            return Path(config_path).resolve().is_relative_to(user_home.resolve())
        except (OSError, ValueError):
            return False

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
                        if isinstance(data, dict) and isinstance(data.get('version'), str):
                            return data['version']
            except (json.JSONDecodeError, OSError, PermissionError) as e:
                logger.debug(f"Could not read config file {config_file}: {e}")
                continue

        return None
