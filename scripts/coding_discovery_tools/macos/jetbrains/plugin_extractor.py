"""
Plugin extraction for JetBrains IDEs on macOS systems.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


# Known AI-related plugins mapping for user-friendly display
KNOWN_PLUGINS = {
    "ml-llm": "JetBrains AI Assistant",
    "github-copilot-intellij": "GitHub Copilot",
    "copilot": "GitHub Copilot",
    "claude-code": "Claude Code",
    "claude-code-beta": "Claude Code (Beta)",
    "codewhisperer": "Amazon Q",
    "continue": "Continue",
    "tabnine-intellij": "Tabnine",
    "cody-ai": "Sourcegraph Cody",
    "codegpt": "CodeGPT",
}


class MacOSJetBrainsPluginExtractor:
    """Extractor for JetBrains IDE plugins on macOS systems."""

    JETBRAINS_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell"
    ]

    def extract_plugins_for_ide(self, config_path: Path) -> List[Dict]:
        """
        Extract installed plugins for a specific JetBrains IDE.

        Args:
            config_path: Path to the IDE config directory (e.g., ~/Library/Application Support/JetBrains/IntelliJIdea2024.1)

        Returns:
            List of plugin dicts with name and display_name
        """
        plugins = []
        plugins_dir = config_path / "plugins"

        if not plugins_dir.exists():
            logger.debug(f"Plugins directory not found: {plugins_dir}")
            return plugins

        try:
            for item in os.listdir(plugins_dir):
                # Skip hidden files
                if item.startswith('.'):
                    continue

                item_path = plugins_dir / item

                # Plugins can be directories or .jar files
                if item_path.is_dir() or item.endswith('.jar'):
                    plugin_id = item.replace(".jar", "")
                    display_name = self._get_plugin_display_name(plugin_id)

                    plugins.append({
                        "id": plugin_id,
                        "name": display_name
                    })
                    logger.debug(f"Found plugin: {plugin_id} -> {display_name}")

        except Exception as e:
            logger.warning(f"Error scanning plugins directory {plugins_dir}: {e}")

        return plugins

    def _get_plugin_display_name(self, plugin_id: str) -> str:
        """
        Get a user-friendly display name for a plugin.

        Args:
            plugin_id: Raw plugin ID/folder name

        Returns:
            User-friendly display name
        """
        # Check known plugins first
        plugin_id_lower = plugin_id.lower()
        if plugin_id_lower in KNOWN_PLUGINS:
            return KNOWN_PLUGINS[plugin_id_lower]

        # Check for partial matches in known plugins
        for known_id, display_name in KNOWN_PLUGINS.items():
            if known_id in plugin_id_lower:
                return display_name

        # Return the original ID if no match found
        return plugin_id

    def extract_all_plugins(self) -> Dict[str, List[Dict]]:
        """
        Extract plugins from all detected JetBrains IDEs.

        Returns:
            Dictionary mapping IDE folder name to list of plugins
        """
        all_plugins = {}

        if not self.JETBRAINS_CONFIG_DIR.exists():
            logger.debug(f"JetBrains config directory not found: {self.JETBRAINS_CONFIG_DIR}")
            return all_plugins

        try:
            for folder in os.listdir(self.JETBRAINS_CONFIG_DIR):
                folder_path = self.JETBRAINS_CONFIG_DIR / folder

                # Skip hidden files and non-directories
                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                # Check if folder matches any IDE pattern
                if not any(pattern in folder for pattern in self.IDE_PATTERNS):
                    continue

                plugins = self.extract_plugins_for_ide(folder_path)
                if plugins:
                    all_plugins[folder] = plugins
                    logger.info(f"Found {len(plugins)} plugins for {folder}")

        except Exception as e:
            logger.warning(f"Error scanning JetBrains directory: {e}")

        return all_plugins
