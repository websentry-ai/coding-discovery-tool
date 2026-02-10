"""
JetBrains IDE detection for macOS
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


class MacOSJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for macOS systems."""

    @property
    def jetbrains_config_dir(self) -> Path:
        """
        Return the JetBrains config directory.
        """
        if hasattr(self, 'user_home') and self.user_home:
            return self.user_home / "Library" / "Application Support" / "JetBrains"
        return Path.home() / "Library" / "Application Support" / "JetBrains"

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

    # Folders to skip when scanning JetBrains directory
    SKIP_FOLDERS = {
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
        Detect JetBrains IDE installations on macOS.
        """
        detected_ides = self._scan_for_ides()

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
        """
        detected_ides = []

        if not self.jetbrains_config_dir.exists():
            logger.debug(f"JetBrains config directory not found: {self.jetbrains_config_dir}")
            return detected_ides

        try:
            items = os.listdir(self.jetbrains_config_dir)
        except Exception as e:
            logger.warning(f"Error listing directory {self.jetbrains_config_dir}: {e}")
            return []

        for folder in items:
            try:
                folder_path = self.jetbrains_config_dir / folder

                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                # Skip system/internal folders
                if folder in self.SKIP_FOLDERS:
                    continue

                matches_name = any(pattern in folder for pattern in self.IDE_PATTERNS)
                has_structure = (folder_path / "plugins").exists() or (folder_path / "options").exists()

                if not (matches_name or has_structure):
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
                # Log warning but continue to next folder
                logger.warning(f"Skipping potential IDE folder '{folder}' due to error: {e}")

        return self._filter_old_versions(detected_ides)

    def _parse_ide_name_and_version(self, folder_name: str) -> tuple:
        """
        Parse IDE name and version from folder name.
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

    def _get_disabled_plugins(self, config_path: str) -> Set[str]:
        """
        Load the set of disabled plugin IDs from disabled_plugins.txt.
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
        """
        # Check for META-INF/plugin.xml
        plugin_xml = plugin_dir / "META-INF" / "plugin.xml"

        # Also check in lib/*.jar files if META-INF not found at root
        if not plugin_xml.exists():
            lib_dir = plugin_dir / "lib"
            if lib_dir.exists():
                jar_files = list(lib_dir.glob("*.jar"))
                logger.debug(f"    Checking {len(jar_files)} JAR files in {lib_dir}")
                for jar_file in jar_files:
                    result = self._extract_plugin_info_from_jar(jar_file)
                    if result[0] or result[1]:
                        logger.debug(f"    Found plugin info in {jar_file.name}: id={result[0]}, name={result[1]}")
                        return result
            else:
                logger.debug(f"    No lib directory found at {lib_dir}")

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
        """
        if plugin_id and plugin_id in self.PLUGIN_NAME_OVERRIDES:
            return self.PLUGIN_NAME_OVERRIDES[plugin_id]

        return plugin_name

    def _get_plugins(self, config_path: str) -> List[str]:
        """
        Get list of installed and enabled plugins for a JetBrains IDE.
        """
        plugins_dir = Path(config_path) / "plugins"
        plugins = []

        logger.info(f"  Looking for plugins in: {plugins_dir}")

        if not plugins_dir.exists():
            logger.info(f"  Plugins directory not found: {plugins_dir}")
            return plugins

        disabled_plugins = self._get_disabled_plugins(config_path)

        try:
            items = [i for i in os.listdir(plugins_dir) if not i.startswith('.')]
            logger.info(f"  Scanning {len(items)} items in plugins directory")

            for item in items:
                item_path = plugins_dir / item

                plugin_id = None
                plugin_name = None

                if item_path.is_dir():
                    plugin_id, plugin_name = self._extract_plugin_info_from_dir(item_path)
                elif item.endswith('.jar'):
                    plugin_id, plugin_name = self._extract_plugin_info_from_jar(item_path)
                else:
                    continue

                if plugin_id and plugin_id in disabled_plugins:
                    logger.info(f"    Skipping disabled plugin: {plugin_id}")
                    continue

                final_name = self._transform_plugin_name(plugin_id, plugin_name)

                if not final_name:
                    final_name = item[:-4] if item.endswith('.jar') else item

                plugins.append(final_name)
                logger.info(f"    + {final_name} (id: {plugin_id})")

        except Exception as e:
            logger.warning(f"Error scanning plugins directory {plugins_dir}: {e}")

        logger.info(f"  Found {len(plugins)} plugin(s) in {plugins_dir}")
        return sorted(plugins)
