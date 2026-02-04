"""
JetBrains IDE detection for Windows
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)

# Maximum number of lines to read from idea.log for plan detection
MAX_LOG_LINES = 2000


class WindowsJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for Windows systems."""

    # Windows uses AppData\Roaming for JetBrains config
    JETBRAINS_CONFIG_DIR = Path.home() / "AppData" / "Roaming" / "JetBrains"
    JETBRAINS_LOCAL_DIR = Path.home() / "AppData" / "Local" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip",
        "DataSpell", "Android"
    ]

    # Maps folder prefixes to display names
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
        "DataSpell": "DataSpell",
        "AndroidStudio": "Android Studio",
    }

    # Folders to skip when scanning JetBrains directory
    SKIP_FOLDERS = {"consent", "DeviceId", "JetBrainsClient"}

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "JetBrains IDEs"

    def detect(self, user_home: Optional[Path] = None) -> Optional[List[Dict]]:
        """
        Detect JetBrains IDE installations on Windows.

        Scans %APPDATA%\\JetBrains directory for installed IDEs.

        Args:
            user_home: Optional user home path for multi-user support

        Returns:
            List of dicts, each containing info for one IDE, or None if not found
        """
        config_dir = self._get_config_dir(user_home)
        local_dir = self._get_local_dir(user_home)

        detected_ides = self._scan_for_ides(config_dir, local_dir)

        if not detected_ides:
            return None

        tools = []
        for ide in detected_ides:
            tools.append({
                "name": ide['display_name'],
                "version": ide['version'],
                "plan": ide['plan'],
                "install_path": ide['config_path'],
                "_ide_folder": ide['folder_name'],
                "_config_path": ide['config_path'],
            })

        return tools

    def get_version(self) -> Optional[str]:
        """
        Extract JetBrains IDEs version information.

        Returns:
            Comma-separated list of detected IDEs with their plans
        """
        detected_ides = self._scan_for_ides(
            self.JETBRAINS_CONFIG_DIR,
            self.JETBRAINS_LOCAL_DIR
        )

        if not detected_ides:
            return None

        return ", ".join(
            f"{ide['display_name']} {ide['version']} ({ide['plan']})"
            for ide in detected_ides
        )

    def _get_config_dir(self, user_home: Optional[Path] = None) -> Path:
        """Get JetBrains config directory for a user."""
        if user_home:
            return user_home / "AppData" / "Roaming" / "JetBrains"
        return self.JETBRAINS_CONFIG_DIR

    def _get_local_dir(self, user_home: Optional[Path] = None) -> Path:
        """Get JetBrains local directory for a user (contains logs)."""
        if user_home:
            return user_home / "AppData" / "Local" / "JetBrains"
        return self.JETBRAINS_LOCAL_DIR

    def _scan_for_ides(
        self,
        config_dir: Path,
        local_dir: Path
    ) -> List[Dict]:
        """
        Scan JetBrains config directory for IDE installations.

        Args:
            config_dir: Path to JetBrains roaming config directory
            local_dir: Path to JetBrains local directory (for logs)

        Returns:
            List of dicts containing IDE info
        """
        detected_ides = []

        if not config_dir.exists():
            logger.debug(f"JetBrains config directory not found: {config_dir}")
            return detected_ides

        try:
            for folder in os.listdir(config_dir):
                folder_path = config_dir / folder

                # Skip hidden files and non-directories
                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                # Skip system folders
                if any(skip in folder for skip in self.SKIP_FOLDERS):
                    continue

                if not any(pattern in folder for pattern in self.IDE_PATTERNS):
                    continue

                display_name, version = self._parse_ide_name_and_version(folder)
                plan = self._detect_plan(folder, local_dir)

                detected_ides.append({
                    "folder_name": folder,
                    "display_name": display_name,
                    "version": version,
                    "plan": plan,
                    "config_path": str(folder_path)
                })
                logger.info(f"Detected JetBrains IDE: {display_name} {version} ({plan})")

        except Exception as e:
            logger.warning(f"Error scanning {config_dir}: {e}")

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

    def _detect_plan(self, folder_name: str, local_dir: Path) -> str:
        """
        Detect plan type by checking folder name and idea.log.

        Checks for "Licensed to" string in idea.log to detect paid plans.
        Falls back to folder name patterns for Community editions.

        Args:
            folder_name: IDE folder name
            local_dir: Path to JetBrains local directory

        Returns:
            Plan type: "Professional", "Community", or "Licensed"
        """
        # Check folder name for community edition markers
        if "IdeaIC" in folder_name or "PyCharmCE" in folder_name:
            return "Community"

        log_file = local_dir / folder_name / "log" / "idea.log"
        if log_file.exists():
            try:
                with open(log_file, 'r', errors='ignore') as f:
                    lines = []
                    for i, line in enumerate(f):
                        if i >= MAX_LOG_LINES:
                            break
                        lines.append(line)

                    log_content = "".join(lines)
                    if "Licensed to" in log_content:
                        return "Professional"
            except Exception as e:
                logger.debug(f"Error reading idea.log for plan detection: {e}")

        # Default to Licensed for non-community editions
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
