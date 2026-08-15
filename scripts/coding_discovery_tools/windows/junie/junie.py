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
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...jetbrains_naming_helpers import plugin_entries
from ...user_tool_detector import find_junie_binary_for_user, junie_version_from_binary
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

    def detect(self) -> Optional[List[Dict]]:
        """Detect Junie installations on Windows.

        Uses the shared scan_windows_user_directories helper for consistent
        admin/non-admin branching and system-account exclusion.
        """
        found: List[Dict] = []

        def check_user(user_home: Path) -> None:
            found.extend(self._detect_junie_for_user(user_home))

        scan_windows_user_directories(check_user)
        return found or None

    def get_version(self) -> Optional[str]:
        """Extract Junie version."""
        result = self.detect()
        if result:
            return result.get('version')
        return None

    def _detect_junie_for_user(self, user_home: Path) -> List[Dict]:
        """
        Detect every Junie surface for a specific user: the CLI and each JetBrains
        IDE carrying the plugin, one row each (mirroring Augment / GitHub Copilot).

        Gates on a real install signal — the CLI binary or the plugin — not on the
        ``~/.junie`` directory, which is user-authored guidelines residue that
        survives uninstall. Each surface reports its own version; ``~/.junie``
        remains the fallback source.
        """
        rows: List[Dict] = []
        config_dir = str(user_home / self.JUNIE_DIR_NAME)
        config_version = self._get_version_from_config(user_home / self.JUNIE_DIR_NAME)

        junie_bin = find_junie_binary_for_user(user_home)
        if junie_bin:
            logger.debug(f"Detected Junie CLI at: {junie_bin}")
            rows.append({
                "name": self.tool_name,
                "version": junie_version_from_binary(junie_bin) or config_version or "Unknown",
                "install_path": junie_bin,
                "_config_path": config_dir,
            })

        for ide_name, ide_config_path, plugin_version in self._junie_jetbrains_surfaces(user_home):
            logger.debug(f"Detected Junie plugin in {ide_name} at: {ide_config_path}")
            rows.append({
                "name": f"{self.tool_name} ({ide_name})",
                "version": plugin_version or config_version or "Unknown",
                "ide": ide_name,
                "install_path": ide_config_path,
                "_config_path": config_dir,
            })

        return rows

    def _junie_jetbrains_surfaces(self, user_home: Path) -> List[Tuple[str, str, Optional[str]]]:
        """Return ``(ide_name, config_path, plugin_version)`` for every JetBrains IDE
        belonging to ``user_home`` that carries the Junie plugin.

        On Windows ``WindowsJetBrainsDetector.detect()`` already honors
        ``self.user_home`` (its ``jetbrains_config_dir`` property derives from it),
        so the scan is scoped by construction. We additionally guard each match by
        confirming the IDE config path is under ``user_home`` so a stray
        cross-user entry can never be attributed to the user being scanned. The
        JetBrains detector itself is never modified.
        """
        surfaces: List[Tuple[str, str, Optional[str]]] = []
        try:
            jetbrains_detector = WindowsJetBrainsDetector()
            jetbrains_detector.user_home = user_home
            all_ides = jetbrains_detector.detect() or []
        except (PermissionError, OSError) as e:
            logger.debug(f"JetBrains scan for Junie failed under {user_home}: {e}")
            return surfaces

        for ide in all_ides:
            config_path = ide.get("_config_path") or ide.get("install_path")
            if not self._path_under_user_home(config_path, user_home):
                continue
            for plugin in plugin_entries(ide):
                if "junie" in str(plugin.get("name", "")).lower():
                    surfaces.append((ide["name"], config_path, plugin.get("version")))
                    break
        return surfaces

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
