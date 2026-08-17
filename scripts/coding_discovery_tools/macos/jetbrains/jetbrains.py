"""
JetBrains IDE detection for macOS
"""

import os
import logging
import zipfile
from pathlib import Path
from typing import Optional, Dict, List, Set, Tuple

from ...coding_tool_base import BaseToolDetector
from ...jetbrains_naming_helpers import (
    JETBRAINS_IDE_NAME_MAPPING,
    JETBRAINS_SKIP_FOLDERS,
    detect_plan,
    looks_like_ide_folder,
    parse_ide_name_and_version,
    parse_plugin_metadata,
    should_skip_folder,
)
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)


class MacOSJetBrainsDetector(BaseToolDetector):
    """JetBrains IDEs detector for macOS systems."""

    IDE_NAME_MAPPING = JETBRAINS_IDE_NAME_MAPPING

    # Folders to skip when scanning JetBrains directory
    SKIP_FOLDERS = JETBRAINS_SKIP_FOLDERS

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
            plugin_details = self._get_plugin_details(ide['config_path'])
            if plugin_details:
                tool_info["plugins"] = [plugin["name"] for plugin in plugin_details]
                tool_info["_plugin_details"] = plugin_details
                logger.info(f"  ✓ Added {len(plugin_details)} plugin(s) to {ide['display_name']}")
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

        When running as root, scans all user directories in /Users.
        """
        all_detected_ides = []
        scanned_homes = set()

        if is_running_as_root():
            users_dir = Path("/Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            user_ides = self._scan_jetbrains_config_dir(user_dir)
                            # Dedup per-user, or one user's newer IDE evicts another's.
                            all_detected_ides.extend(self._filter_old_versions(user_ides))
                            scanned_homes.add(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue

        # Under sudo, HOME is often one of the /Users entries already walked above.
        home = Path.home()
        if home not in scanned_homes:
            try:
                home_ides = self._scan_jetbrains_config_dir(home)
                all_detected_ides.extend(self._filter_old_versions(home_ides))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping home directory {home}: {e}")

        return all_detected_ides

    def _scan_jetbrains_config_dir(self, user_home: Path) -> List[Dict]:
        """
        Scan JetBrains config directory for a specific user.
        """
        detected_ides = []
        jetbrains_config_dir = user_home / "Library" / "Application Support" / "JetBrains"

        if not jetbrains_config_dir.exists():
            logger.debug(f"JetBrains config directory not found: {jetbrains_config_dir}")
            return detected_ides

        try:
            items = os.listdir(jetbrains_config_dir)
        except Exception as e:
            logger.warning(f"Error listing directory {jetbrains_config_dir}: {e}")
            return []

        for folder in items:
            try:
                folder_path = jetbrains_config_dir / folder

                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                # Skip system/internal folders
                if should_skip_folder(folder, self.SKIP_FOLDERS):
                    continue

                is_versioned = looks_like_ide_folder(folder)
                has_structure = (folder_path / "plugins").exists() or (folder_path / "options").exists()

                if not (is_versioned or has_structure):
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

        return detected_ides

    def _parse_ide_name_and_version(self, folder_name: str) -> Tuple[str, str]:
        """
        Parse IDE name and version from folder name.
        """
        return parse_ide_name_and_version(folder_name, self.IDE_NAME_MAPPING)

    @staticmethod
    def _detect_plan(folder_name: str) -> str:
        return detect_plan(folder_name)

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

    def _parse_plugin_xml(self, xml_content: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Parse plugin.xml content to extract plugin ID, name and version.
        """
        return parse_plugin_metadata(xml_content)

    def _extract_plugin_info_from_dir(self, plugin_dir: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract plugin ID, name and version from a plugin directory.
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

            return None, None, None

        try:
            xml_content = plugin_xml.read_text(encoding='utf-8', errors='ignore')
            return self._parse_plugin_xml(xml_content)
        except Exception as e:
            logger.debug(f"Error reading plugin.xml from {plugin_dir}: {e}")
            return None, None, None

    def _extract_plugin_info_from_jar(self, jar_path: Path) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract plugin ID, name and version from a JAR file.
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

        return None, None, None

    def _transform_plugin_name(self, plugin_id: Optional[str], plugin_name: Optional[str]) -> Optional[str]:
        """
        Apply metadata transformations to plugin name.
        """
        if plugin_id and plugin_id in self.PLUGIN_NAME_OVERRIDES:
            return self.PLUGIN_NAME_OVERRIDES[plugin_id]

        return plugin_name

    def _get_plugin_details(self, config_path: str) -> List[Dict]:
        """
        Get installed and enabled plugins for a JetBrains IDE as ``{name, version}`` dicts.
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

                if item_path.is_dir():
                    plugin_id, plugin_name, plugin_version = self._extract_plugin_info_from_dir(item_path)
                elif item.endswith('.jar'):
                    plugin_id, plugin_name, plugin_version = self._extract_plugin_info_from_jar(item_path)
                else:
                    continue

                if plugin_id and plugin_id in disabled_plugins:
                    logger.info(f"    Skipping disabled plugin: {plugin_id}")
                    continue

                final_name = self._transform_plugin_name(plugin_id, plugin_name)

                if not final_name:
                    final_name = item[:-4] if item.endswith('.jar') else item

                plugins.append({"name": final_name, "version": plugin_version})
                logger.info(f"    + {final_name} (id: {plugin_id}, version: {plugin_version})")

        except Exception as e:
            logger.warning(f"Error scanning plugins directory {plugins_dir}: {e}")

        logger.info(f"  Found {len(plugins)} plugin(s) in {plugins_dir}")
        return sorted(plugins, key=lambda plugin: plugin["name"])

    def _get_plugins(self, config_path: str) -> List[str]:
        """
        Get plugin display names for a JetBrains IDE, as reported in the payload.
        """
        return [plugin["name"] for plugin in self._get_plugin_details(config_path)]
