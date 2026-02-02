"""
MCP config extraction for JetBrains IDEs on macOS systems.
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor

MAX_PROJECTS = 50

logger = logging.getLogger(__name__)


class MacOSJetBrainsMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for JetBrains IDEs MCP config on macOS systems."""

    JETBRAINS_CONFIG_DIR = Path.home() / "Library" / "Application Support" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell"
    ]

    MCP_CONFIG_FILES = ["mcp.json", "claude_mcp_config.json"]

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration from JetBrains IDEs on macOS.

        Scans all detected JetBrains IDEs, extracts their recent projects,
        and checks each project for MCP configuration files.

        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        all_projects = []

        if not self.JETBRAINS_CONFIG_DIR.exists():
            logger.debug(f"JetBrains config directory not found: {self.JETBRAINS_CONFIG_DIR}")
            return None

        try:
            for folder in os.listdir(self.JETBRAINS_CONFIG_DIR):
                folder_path = self.JETBRAINS_CONFIG_DIR / folder

                # Skip hidden files and non-directories
                if folder.startswith('.') or not folder_path.is_dir():
                    continue

                # Check if folder matches any IDE pattern
                if not any(pattern in folder for pattern in self.IDE_PATTERNS):
                    continue

                # Extract projects from this IDE's configuration
                ide_projects = self._extract_ide_projects(folder_path, folder)
                all_projects.extend(ide_projects)

        except Exception as e:
            logger.warning(f"Error scanning {self.JETBRAINS_CONFIG_DIR}: {e}")

        # Return None if no projects found
        if not all_projects:
            return None

        return {
            "projects": all_projects
        }

    def _extract_ide_projects(self, config_path: Path, ide_name: str) -> List[Dict]:
        """
        Extract recent projects from a specific JetBrains IDE configuration.

        Args:
            config_path: Path to the IDE config directory
            ide_name: Name of the IDE

        Returns:
            List of project dicts with MCP server info
        """
        projects = []

        # Parse recentProjects.xml
        recent_projects_file = config_path / "options" / "recentProjects.xml"

        if not recent_projects_file.exists():
            logger.debug(f"No recentProjects.xml found for {ide_name}")
            return projects

        # Extract project paths from XML
        project_paths = self._parse_recent_projects_xml(recent_projects_file)

        if not project_paths:
            logger.debug(f"No project paths found in {recent_projects_file}")
            return projects

        logger.info(f"Found {len(project_paths)} projects in {ide_name}")

        # Check each project for MCP config
        for project_path_str in project_paths[:MAX_PROJECTS]:
            # Expand $USER_HOME$ placeholder
            project_path_str = project_path_str.replace("$USER_HOME$", str(Path.home()))

            project_path = Path(project_path_str)

            if not project_path.exists():
                logger.debug(f"Project path does not exist: {project_path}")
                continue

            mcp_servers = self._detect_project_mcp(project_path)

            if mcp_servers:
                projects.append({
                    "path": str(project_path),
                    "mcpServers": mcp_servers
                })
                logger.info(f"Found MCP config in {project_path} with {len(mcp_servers)} server(s)")

        return projects

    def _parse_recent_projects_xml(self, xml_file: Path) -> List[str]:
        """
        Parse recentProjects.xml to extract project paths.

        Handles both formats used by modern JetBrains IDEs:
        - Standard: <option value="$USER_HOME$/..." />
        - Newer: <entry key="$USER_HOME$/..." />

        Args:
            xml_file: Path to recentProjects.xml

        Returns:
            List of project path strings
        """
        paths = []

        try:
            tree = ET.parse(xml_file)
            root = tree.getroot()

            # format 1: standard JetBrains IDEs style
            for option in root.findall(".//option"):
                val = option.get("value")
                if val and ("$USER_HOME$" in val or "/" in val):
                    paths.append(val)

            # format 2: newer JetBrains IDEs style
            for entry in root.findall(".//entry"):
                key = entry.get("key")
                if key and ("$USER_HOME$" in key or "/" in key):
                    paths.append(key)

            # Remove duplicates while preserving order
            seen = set()
            unique_paths = []
            for path in paths:
                if path not in seen:
                    seen.add(path)
                    unique_paths.append(path)

            return unique_paths

        except Exception as e:
            logger.warning(f"Error parsing {xml_file}: {e}")
            return []

    def _detect_project_mcp(self, project_path: Path) -> List[str]:
        """
        Scan a project folder for MCP configuration files.

        Args:
            project_path: Path to the project directory

        Returns:
            List of MCP server names found in the project
        """
        mcp_servers = []

        candidates = [
            project_path / "mcp.json",
            project_path / ".mcp" / "config.json",
            project_path / "claude_mcp_config.json"
        ]

        for config_file in candidates:
            if not config_file.exists():
                continue

            try:
                with open(config_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                    servers = data.get("mcpServers", {})

                    for name, details in servers.items():
                        cmd = details.get("command", "unknown")
                        args = details.get("args", [])

                        mcp_servers.append({
                            "name": name,
                            "command": cmd,
                            "args": args
                        })

                logger.info(f"Found MCP config at {config_file} with {len(servers)} server(s)")

            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in {config_file}: {e}")
            except Exception as e:
                logger.warning(f"Error reading {config_file}: {e}")

        return mcp_servers
