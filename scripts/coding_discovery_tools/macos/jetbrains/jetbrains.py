"""
JetBrains IDE detection for macOS
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


class MacOSJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for macOS systems."""

    JETBRAINS_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell"
    ]

    IDE_NAME_MAPPING = {
        "IntelliJIdea": "IntelliJ IDEA",
        "IdeaIC": "IntelliJ IDEA Community",
        "PyCharm": "PyCharm",
        "PyCharmCE": "PyCharm Community",
        "WebStorm": "WebStorm",
        "PhpStorm": "PhpStorm",
        "GoLand": "GoLand",
        "Rider": "Rider",
        "CLion": "CLion",
        "RustRover": "RustRover",
        "RubyMine": "RubyMine",
        "DataGrip": "DataGrip",
        "DataSpell": "DataSpell"
    }

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "JetBrains IDEs"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect JetBrains IDE installations on macOS.

        Scans ~/Library/Application Support/JetBrains/ directory for installed IDEs.

        Returns:
            List of dicts, each containing info for one IDE, or None if not found
        """
        detected_ides = self._scan_for_ides()

        if not detected_ides:
            return None

        tools = []
        for ide in detected_ides:
            name = f"{ide['display_name']} {ide['version']} ({ide['plan']})"

            tools.append({
                "name": name,
                "version": ide['version'],
                "install_path": ide['config_path'],
                "_ide_folder": ide['folder_name'],  # Store for MCP extractor
                "_config_path": ide['config_path'],  # Store for MCP extractor
            })

        return tools

    def get_version(self) -> Optional[str]:
        """
        Extract JetBrains IDEs version information.

        Returns:
            Comma-separated list of detected IDEs with their plans (if known)
        """
        detected_ides = self._scan_for_ides()

        if not detected_ides:
            return None

        return ", ".join(
            f"{ide['display_name']} {ide['version']} ({ide['plan']})"
            for ide in detected_ides
        )

    def _scan_for_ides(self) -> List[Dict]:
        """
        Scan JetBrains config directory for IDE installations.

        Returns:
            List of dicts containing IDE info (folder_name, display_name, version, plan, config_path)
        """
        detected_ides = []

        if not self.JETBRAINS_CONFIG_DIR.exists():
            logger.debug(f"JetBrains config directory not found: {self.JETBRAINS_CONFIG_DIR}")
            return detected_ides

        try:
            for folder in os.listdir(self.JETBRAINS_CONFIG_DIR):
                folder_path = self.JETBRAINS_CONFIG_DIR / folder

                # Skip hidden files and non-directories
                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                if not any(pattern in folder for pattern in self.IDE_PATTERNS):
                    continue

                display_name, version = self._parse_ide_name_and_version(folder)
                plan = self._detect_plan(folder)

                detected_ides.append({
                    "folder_name": folder,
                    "display_name": display_name,
                    "version": version,
                    "plan": plan,
                    "config_path": str(folder_path)
                })
                logger.info(f"Detected JetBrains IDE: {display_name} {version} ({plan})")

        except Exception as e:
            logger.warning(f"Error scanning {self.JETBRAINS_CONFIG_DIR}: {e}")

        return self._filter_old_versions(detected_ides)

    def _parse_ide_name_and_version(self, folder_name: str) -> tuple:
        """
        Parse IDE name and version from folder name.

        Args:
            folder_name: Folder name like "IntelliJIdea2024.1" or "PyCharm2024.1"

        Returns:
            Tuple of (display_name, version)
        """
        sorted_prefixes = sorted(self.IDE_NAME_MAPPING.keys(), key=len, reverse=True)
        for prefix in sorted_prefixes:
            if folder_name.startswith(prefix):
                version = folder_name[len(prefix):]
                display_name = self.IDE_NAME_MAPPING[prefix]
                return display_name, version if version else "Unknown"

        return folder_name, "Unknown"

    @staticmethod
    def _detect_plan(folder_name: str) -> str:
        """Return 'Free' for Community editions, 'Licensed' otherwise."""
        if "IdeaIC" in folder_name or "PyCharmCE" in folder_name:
            return "Free"
        return "Licensed"

    @staticmethod
    def _filter_old_versions(ide_list: List[Dict]) -> List[Dict]:
        """Group IDEs by display_name and keep only the newest version of each."""
        latest = {}
        for ide in ide_list:
            name = ide['display_name']
            parts = [int(x) for x in ide['version'].split('.') if x.isdigit()]
            ver = tuple(parts) if parts else (0,)
            if name not in latest or ver > latest[name][1]:
                latest[name] = (ide, ver)
        return [entry[0] for entry in latest.values()]
