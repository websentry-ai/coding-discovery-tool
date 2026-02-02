"""
JetBrains IDE detection for macOS
"""

import os
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from .plugin_extractor import MacOSJetBrainsPluginExtractor

MAX_LOG_LINES = 1000

logger = logging.getLogger(__name__)


class MacOSJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for macOS systems."""

    JETBRAINS_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "JetBrains"
    JETBRAINS_LOGS_DIR = Path.home() / "Library" / "Logs" / "JetBrains"

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

        plugin_extractor = MacOSJetBrainsPluginExtractor()

        # Output format: "{IDE Name} {Version} ({Plan})" or "{IDE Name} {Version}" if plan is Unknown
        tools = []
        for ide in detected_ides:
            config_path = Path(ide['config_path'])

            # Extract plugins for this IDE
            plugins = plugin_extractor.extract_plugins_for_ide(config_path)
            logger.info(f"Found {len(plugins)} plugins for {ide['display_name']}")

            plan = ide['plan']
            if plan and plan != "Unknown":
                name = f"{ide['display_name']} {ide['version']} ({plan})"
            else:
                name = f"{ide['display_name']} {ide['version']}"

            tools.append({
                "name": name,
                "version": ide['version'],
                "install_path": ide['config_path'],
                "_ide_folder": ide['folder_name'],  # Store for MCP extractor
                "_config_path": ide['config_path'],  # Store for MCP extractor
                "extensions": plugins  # Include plugins/extensions
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

        def format_ide(ide):
            plan = ide['plan']
            if plan and plan != "Unknown":
                return f"{ide['display_name']} {ide['version']} ({plan})"
            return f"{ide['display_name']} {ide['version']}"

        return ", ".join([format_ide(ide) for ide in detected_ides])

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

                # Detect plan type for this IDE (using Logs directory)
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

        return detected_ides

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

    def _detect_plan(self, folder_name: str) -> str:
        """
        Detect the plan type (Paid, Free, Trial) by scanning idea.log.

        The idea.log file is located in ~/Library/Logs/JetBrains/<Folder_Name>/
        rather than in the Application Support config directory.

        Args:
            folder_name: Name of the IDE folder (e.g., "IntelliJIdea2024.1")

        Returns:
            Plan type string (never None, defaults to "Unknown")
        """
        if "IdeaIC" in folder_name or "PyCharmCE" in folder_name:
            return "Free"

        # Build the path to idea.log in the Logs directory
        logs_dir = self.JETBRAINS_LOGS_DIR / folder_name

        if not logs_dir.exists():
            logger.debug(f"Logs directory not found: {logs_dir}")
            return "Unknown"

        # Find the most recent idea.log file
        idea_log = None
        try:
            log_files = list(logs_dir.glob("idea.log*"))
            if log_files:
                # Sort by modification time, get the most recent
                idea_log = max(log_files, key=lambda p: p.stat().st_mtime)
        except Exception as e:
            logger.debug(f"Error finding idea.log: {e}")
            return "Unknown"

        if not idea_log or not idea_log.exists():
            logger.debug(f"No idea.log found at {logs_dir}")
            return "Unknown"

        # Scan the log file line by line for plan indicators
        try:
            with open(idea_log, 'r', encoding='utf-8', errors='ignore') as f:
                for i, line in enumerate(f):
                    if i > MAX_LOG_LINES:
                        break

                    # Check for plan indicators
                    if "Licensed to" in line:
                        logger.debug(f"Found 'Licensed to' in {idea_log}")
                        return "Paid"
                    elif "Evaluation" in line or "evaluation license" in line.lower():
                        logger.debug(f"Found 'Evaluation' in {idea_log}")
                        return "Trial"
                    elif "Community" in line or "CommunityEdition" in line or "IdeaIC" in line:
                        logger.debug(f"Found 'Community/CommunityEdition/IdeaIC' in {idea_log}")
                        return "Free"

                logger.debug(f"No plan indicators found in {idea_log}")
                return "Unknown"

        except Exception as e:
            logger.warning(f"Error reading idea.log at {idea_log}: {e}")
            return "Unknown"
