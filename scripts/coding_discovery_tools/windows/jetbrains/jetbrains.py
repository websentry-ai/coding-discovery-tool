"""
JetBrains IDE detection for Windows
"""

import os
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)

# Maximum number of lines to read from idea.log for plan detection
MAX_LOG_LINES = 2000


class WindowsJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for Windows systems."""

    @property
    def jetbrains_config_dir(self) -> Path:
        """
        Dynamic Config Directory (Roaming).

        Uses self.user_home if available (for scanning other users),
        otherwise falls back to environment variables or Path.home().
        """
        if hasattr(self, 'user_home') and self.user_home:
            return self.user_home / "AppData" / "Roaming" / "JetBrains"

        # Fallback to environment variable
        appdata = os.path.expandvars(r"%APPDATA%")
        if appdata and appdata != r"%APPDATA%":
            return Path(appdata) / "JetBrains"
        return Path.home() / "AppData" / "Roaming" / "JetBrains"

    @property
    def jetbrains_local_dir(self) -> Path:
        """
        Dynamic Local Directory (Local - for logs).

        Uses self.user_home if available (for scanning other users),
        otherwise falls back to environment variables or Path.home().
        """
        if hasattr(self, 'user_home') and self.user_home:
            return self.user_home / "AppData" / "Local" / "JetBrains"

        # Fallback to environment variable
        local = os.path.expandvars(r"%LOCALAPPDATA%")
        if local and local != r"%LOCALAPPDATA%":
            return Path(local) / "JetBrains"
        return Path.home() / "AppData" / "Local" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip",
        "DataSpell"
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
        "DataSpell": "DataSpell",
    }

    # Folders to skip when scanning JetBrains directory
    SKIP_FOLDERS = {
        "consent", "DeviceId", "JetBrainsClient",
        "consentOptions", "PrivacyPolicy", "Toolbox",
    }

    PLUGIN_NAME_OVERRIDES = {
        "ml-llm": "JetBrains AI Assistant",
        "ej": "JProfiler Support",
    }

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "JetBrains IDEs"

    def detect(self) -> Optional[List[Dict]]:
        """
        Detect JetBrains IDE installations on Windows.

        Scans %APPDATA%\JetBrains directory for installed IDEs.

        Returns:
            List of dicts, each containing info for one IDE, or None if not found
        """
        detected_ides = self._scan_for_ides(
            self.jetbrains_config_dir,
            self.jetbrains_local_dir
        )

        if not detected_ides:
            return None

        tools = []
        for ide in detected_ides:
            tool_info = {
                "name": ide['display_name'],
                "version": ide['version'],
                "plan": ide['plan'],
                "install_path": ide['config_path'],
                "_ide_folder": ide['folder_name'],
                "_config_path": ide['config_path'],
            }

            logger.info(f"Detecting plugins for {ide['display_name']}...")
            plugins = self._get_plugins(ide['config_path'])
            if plugins:
                tool_info["plugins"] = plugins
                logger.info(f"  ✓ Added {len(plugins)} plugin(s) to {ide['display_name']}")
            else:
                logger.info(f"  ℹ No plugins found for {ide['display_name']}")

            tools.append(tool_info)

        return tools

    def get_version(self) -> Optional[str]:
        """
        Extract JetBrains IDEs version information.

        Returns:
            Comma-separated list of detected IDEs with their plans
        """
        detected_ides = self._scan_for_ides(
            self.jetbrains_config_dir,
            self.jetbrains_local_dir
        )

        if not detected_ides:
            return None

        return ", ".join(
            f"{ide['display_name']} {ide['version']} ({ide['plan']})"
            for ide in detected_ides
        )

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
            items = os.listdir(config_dir)
        except Exception as e:
            logger.warning(f"Error listing directory {config_dir}: {e}")
            return []

        for folder in items:
            try:
                folder_path = config_dir / folder

                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                if folder in self.SKIP_FOLDERS:
                    continue

                matches_name = any(pattern in folder for pattern in self.IDE_PATTERNS)
                has_structure = (folder_path / "plugins").exists() or (folder_path / "options").exists()

                if not (matches_name or has_structure):
                    continue

                display_name, version = self._parse_ide_name_and_version(folder)

                try:
                    plan = self._detect_plan(folder, local_dir)
                except Exception:
                    plan = "Licensed"  # Fallback if log is locked

                detected_ides.append({
                    "folder_name": folder,
                    "display_name": display_name,
                    "version": version,
                    "plan": plan,
                    "config_path": str(folder_path)
                })
                logger.info(f"Detected JetBrains IDE: {display_name} {version} ({plan})")

            except Exception as e:
                # Log warning but continue to next folder
                logger.warning(f"Skipping potential IDE folder '{folder}' due to error: {e}")

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

    def _get_disabled_plugins(self, config_path: str) -> Set[str]:
        """
        Load the set of disabled plugin IDs from disabled_plugins.txt.

        Args:
            config_path: Path to the IDE's config directory

        Returns:
            Set of disabled plugin IDs
        """
        disabled_file = Path(config_path) / "disabled_plugins.txt"
        disabled = set()

        if not disabled_file.exists():
            return disabled

        try:
            with open(disabled_file, 'r', encoding='utf-8', errors='ignore') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        disabled.add(line)
            logger.debug(f"Found {len(disabled)} disabled plugins in {disabled_file}")
        except Exception as e:
            logger.warning(f"Error reading disabled_plugins.txt: {e}")

        return disabled

    def _parse_plugin_xml(self, xml_content: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse plugin.xml content to extract plugin ID and name.

        Uses namespace-agnostic parsing to handle XML with or without namespaces.

        Args:
            xml_content: The XML content as a string

        Returns:
            Tuple of (plugin_id, plugin_name), either may be None if not found
        """
        plugin_id = None
        plugin_name = None

        try:
            # Remove XML namespace declarations for simpler parsing
            xml_content_clean = re.sub(r'\sxmlns[^"]*"[^"]*"', '', xml_content)
            root = ET.fromstring(xml_content_clean)

            # Try to find <id> tag, can be at root level or nested
            id_elem = root.find('.//id')
            if id_elem is not None and id_elem.text:
                plugin_id = id_elem.text.strip()

            # Try to find <name> tag
            name_elem = root.find('.//name')
            if name_elem is not None and name_elem.text:
                plugin_name = name_elem.text.strip()

            # If no <id> found, check the root element's id attribute
            if not plugin_id:
                plugin_id = root.get('id')

        except ET.ParseError as e:
            logger.debug(f"Failed to parse plugin.xml: {e}")
        except Exception as e:
            logger.debug(f"Error parsing plugin.xml: {e}")

        return plugin_id, plugin_name

    def _extract_plugin_info_from_dir(self, plugin_dir: Path) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract plugin ID and name from a plugin directory.

        Args:
            plugin_dir: Path to the plugin directory

        Returns:
            Tuple of (plugin_id, plugin_name)
        """
        # Check for META-INF/plugin.xml
        plugin_xml = plugin_dir / "META-INF" / "plugin.xml"

        # Also check in lib/*.jar files if META-INF not found at root
        if not plugin_xml.exists():
            lib_dir = plugin_dir / "lib"
            if lib_dir.exists():
                for jar_file in lib_dir.glob("*.jar"):
                    result = self._extract_plugin_info_from_jar(jar_file)
                    if result[0] or result[1]:
                        return result

            return None, None

        try:
            xml_content = plugin_xml.read_text(encoding='utf-8', errors='ignore')
            return self._parse_plugin_xml(xml_content)
        except Exception as e:
            logger.debug(f"Error reading plugin.xml from {plugin_dir}: {e}")
            return None, None

    def _extract_plugin_info_from_jar(self, jar_path: Path) -> Tuple[Optional[str], Optional[str]]:
        """
        Extract plugin ID and name from a JAR file.

        Args:
            jar_path: Path to the JAR file

        Returns:
            Tuple of (plugin_id, plugin_name)
        """
        try:
            with zipfile.ZipFile(jar_path, 'r') as zf:
                if 'META-INF/plugin.xml' in zf.namelist():
                    xml_content = zf.read('META-INF/plugin.xml').decode('utf-8', errors='ignore')
                    return self._parse_plugin_xml(xml_content)
        except zipfile.BadZipFile:
            logger.debug(f"Invalid JAR file: {jar_path}")
        except Exception as e:
            logger.debug(f"Error reading plugin.xml from JAR {jar_path}: {e}")

        return None, None

    def _transform_plugin_name(self, plugin_id: Optional[str], plugin_name: Optional[str]) -> Optional[str]:
        """
        Apply metadata transformations to plugin name.

        Args:
            plugin_id: The plugin ID
            plugin_name: The original plugin name

        Returns:
            Transformed plugin name, or None if plugin should be skipped
        """
        # Check for plugin ID overrides
        if plugin_id and plugin_id in self.PLUGIN_NAME_OVERRIDES:
            return self.PLUGIN_NAME_OVERRIDES[plugin_id]

        return plugin_name

    def _get_plugins(self, config_path: str) -> List[str]:
        """
        Get list of installed and enabled plugins for a JetBrains IDE.

        Parses META-INF/plugin.xml from each plugin to extract the plugin name.
        Filters out disabled plugins and applies metadata transformations.

        Args:
            config_path: Path to the IDE's config directory

        Returns:
            List of plugin names (cleaned and transformed)
        """
        plugins_dir = Path(config_path) / "plugins"
        plugins = []

        if not plugins_dir.exists():
            logger.debug(f"Plugins directory not found: {plugins_dir}")
            return plugins

        disabled_plugins = self._get_disabled_plugins(config_path)

        try:
            for item in os.listdir(plugins_dir):
                item_path = plugins_dir / item

                if item.startswith('.'):
                    continue

                plugin_id = None
                plugin_name = None

                if item_path.is_dir():
                    plugin_id, plugin_name = self._extract_plugin_info_from_dir(item_path)
                elif item.endswith('.jar'):
                    plugin_id, plugin_name = self._extract_plugin_info_from_jar(item_path)
                else:
                    continue

                if plugin_id and plugin_id in disabled_plugins:
                    logger.debug(f"Skipping disabled plugin: {plugin_id}")
                    continue

                final_name = self._transform_plugin_name(plugin_id, plugin_name)

                if not final_name:
                    final_name = item[:-4] if item.endswith('.jar') else item

                plugins.append(final_name)
                logger.debug(f"Found plugin: {final_name} (id: {plugin_id})")

        except Exception as e:
            logger.warning(f"Error scanning plugins directory {plugins_dir}: {e}")

        return sorted(plugins)
