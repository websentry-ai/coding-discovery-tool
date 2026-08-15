"""
Junie detection for macOS.
"""

import logging
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from ...macos_extraction_helpers import is_running_as_root
from ...user_tool_detector import find_junie_binary_for_user, junie_version_from_binary

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

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect Junie installations on macOS.
        """
        if is_running_as_root():
            results: List[Dict] = []
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            results.extend(self._detect_junie_for_user(user_dir))
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
            return results or None
        return self._detect_junie_for_user(Path.home()) or None

    def get_version(self) -> Optional[str]:
        """
        Extract Junie version.
        """
        results = self.detect()
        return results[0].get('version') if results else None

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

        Scoping matters: ``MacOSJetBrainsDetector.detect()`` ignores ``user_home``
        and, under root, scans every user under ``/Users``. Calling it here would
        attribute another user's Junie plugin to whichever user is currently
        being scanned (cross-user false positive). Instead we drive the
        detector's per-user config-dir scan for ``user_home`` only, then enrich
        with plugins. The JetBrains detector itself is never modified — only its
        existing per-user methods are reused read-only.
        """
        surfaces: List[Tuple[str, str, Optional[str]]] = []
        try:
            jetbrains_detector = MacOSJetBrainsDetector()
            scoped_ides = jetbrains_detector._scan_jetbrains_config_dir(user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"JetBrains scan for Junie failed under {user_home}: {e}")
            return surfaces

        for ide in scoped_ides:
            config_path = ide.get("config_path")
            if not self._path_under_user_home(config_path, user_home):
                continue
            try:
                plugins = jetbrains_detector._get_plugin_details(config_path)
            except (PermissionError, OSError) as e:
                logger.debug(f"Plugin scan for Junie failed under {config_path}: {e}")
                continue
            for plugin in plugins:
                if "junie" in str(plugin.get("name", "")).lower():
                    surfaces.append((ide["display_name"], config_path, plugin.get("version")))
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
