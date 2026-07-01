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
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...user_tool_detector import find_junie_binary_for_user
from ...windows_extraction_helpers import scan_windows_user_directories
from ..jetbrains.jetbrains import WindowsJetBrainsDetector

logger = logging.getLogger(__name__)


class WindowsJunieDetector(BaseToolDetector):
    """Detector for Junie installations on Windows systems."""

    JUNIE_DIR_NAME = ".junie"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Junie"

    def detect(self) -> Optional[Dict]:
        """Detect Junie installation on Windows.

        Uses the shared scan_windows_user_directories helper for consistent
        admin/non-admin branching and system-account exclusion, returning the
        first user's installation found.
        """
        found: List[Dict] = []

        def check_user(user_home: Path) -> None:
            if found:
                return
            result = self._detect_junie_for_user(user_home)
            if result:
                found.append(result)

        scan_windows_user_directories(check_user)
        return found[0] if found else None

    def get_version(self) -> Optional[str]:
        """Extract Junie version."""
        result = self.detect()
        if result:
            return result.get('version')
        return None

    def _detect_junie_for_user(self, user_home: Path) -> Optional[Dict]:
        """Detect Junie installation for a specific user.

        Gates on a real install signal — the Junie CLI binary OR the Junie
        plugin in a JetBrains IDE — not on the ``%USERPROFILE%\\.junie``
        directory, which is user-authored guidelines residue that survives
        uninstall. ``.junie`` is still used as the version source.
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

        On Windows ``WindowsJetBrainsDetector.detect()`` already honors
        ``self.user_home`` (its ``jetbrains_config_dir`` property derives from it),
        so the scan is scoped by construction. We additionally guard each match by
        confirming the IDE config path is under ``user_home`` so a stray
        cross-user entry can never be attributed to the user being scanned. The
        JetBrains detector itself is never modified.
        """
        try:
            jetbrains_detector = WindowsJetBrainsDetector()
            jetbrains_detector.user_home = user_home
            all_ides = jetbrains_detector.detect() or []
        except (PermissionError, OSError) as e:
            logger.debug(f"JetBrains scan for Junie failed under {user_home}: {e}")
            return None

        for ide in all_ides:
            config_path = ide.get("_config_path") or ide.get("install_path")
            if not self._path_under_user_home(config_path, user_home):
                continue
            for plugin_name in ide.get("plugins", []):
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
                        if isinstance(data, dict) and isinstance(data.get('version'), str):
                            return data['version']
            except (json.JSONDecodeError, OSError, PermissionError) as e:
                logger.debug(f"Could not read config file {config_file}: {e}")
                continue

        return None
