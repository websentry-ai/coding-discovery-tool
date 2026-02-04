"""
MCP config extraction for JetBrains IDEs on Windows systems.
"""

import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...windows_extraction_helpers import get_file_metadata, read_file_content

logger = logging.getLogger(__name__)


class WindowsJetBrainsMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for JetBrains IDEs MCP config on Windows systems."""

    # Windows uses AppData\Roaming for JetBrains config
    @classmethod
    def _get_config_dir(cls):
        """Get JetBrains config directory using %APPDATA% environment variable."""
        appdata_roaming = os.path.expandvars(r"%APPDATA%")
        if appdata_roaming and appdata_roaming != r"%APPDATA%":
            return Path(appdata_roaming) / "JetBrains"
        return Path.home() / "AppData" / "Roaming" / "JetBrains"

    @property
    def JETBRAINS_CONFIG_DIR(self):
        return type(self)._get_config_dir()

    IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip",
        "DataSpell", "Android"
    ]

    MCP_CONFIG_FILES = ["mcp.json", "claude_mcp_config.json"]

    SKIP_FOLDERS = {"consent", "DeviceId", "JetBrainsClient"}

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration from JetBrains IDEs on Windows.

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

                # Skip system folders
                if any(skip in folder for skip in self.SKIP_FOLDERS):
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

        # Extract MCP servers from IDE-level llm.mcpServers.xml
        ide_mcp_servers = self._extract_ide_mcp_servers(config_path)

        # Check each project for MCP config and rules
        for project_path_str in project_paths:
            # Expand $USER_HOME$ placeholder for Windows paths
            project_path_str = project_path_str.replace(
                "$USER_HOME$",
                str(Path.home())
            )
            # Convert forward slashes to backslashes for Windows
            project_path_str = project_path_str.replace("/", "\\")

            project_path = Path(project_path_str)

            if not project_path.exists():
                logger.debug(f"Project path does not exist: {project_path}")
                continue

            mcp_servers = self._detect_project_mcp(project_path)
            rules = self._detect_project_rules(project_path)

            # Combine IDE-level MCP servers with project-level servers
            combined_mcp_servers = ide_mcp_servers + mcp_servers

            # Include project if it has either MCP servers or rules
            if combined_mcp_servers or rules:
                projects.append({
                    "path": str(project_path),
                    "mcpServers": combined_mcp_servers,
                    "rules": rules
                })
                logger.info(
                    f"Found data in {project_path}: "
                    f"{len(combined_mcp_servers)} MCP server(s), {len(rules)} rule(s)"
                )

        return projects

    def _extract_ide_mcp_servers(self, config_path: Path) -> List[Dict]:
        """
        Extract MCP server configurations from llm.mcpServers.xml.

        Parses the IDE-level MCP configuration file. Only includes servers
        that have both a name and command specified.

        Args:
            config_path: Path to the IDE config directory

        Returns:
            List of MCP server dicts with name, command, and args
        """
        mcp_servers = []
        xml_path = config_path / "options" / "llm.mcpServers.xml"

        if not xml_path.exists():
            logger.debug(f"No llm.mcpServers.xml found in {config_path}")
            return mcp_servers

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Find all McpServerConfigurationProperties elements
            for props in root.findall(".//McpServerConfigurationProperties"):
                name_opt = props.find("option[@name='name']")
                if name_opt is None:
                    continue

                name = name_opt.get("value")
                if not name:
                    continue

                # Get command and args - skip if command is missing
                cmd_opt = props.find("option[@name='command']")
                if cmd_opt is None:
                    logger.debug(f"Skipping MCP server '{name}' in llm.mcpServers.xml: missing command")
                    continue

                cmd = cmd_opt.get("value", "")
                if not cmd:
                    logger.debug(f"Skipping MCP server '{name}' in llm.mcpServers.xml: empty command")
                    continue

                args_opt = props.find("option[@name='args']")
                if args_opt is not None:
                    args_value = args_opt.get("value", "")
                    # Parse args - could be space-separated or JSON array
                    if args_value.startswith("["):
                        try:
                            args = json.loads(args_value)
                        except json.JSONDecodeError:
                            args = args_value.split() if args_value else []
                    else:
                        args = args_value.split() if args_value else []
                else:
                    args = []

                mcp_servers.append({
                    "name": name,
                    "command": cmd,
                    "args": args
                })

                logger.debug(f"Found MCP server in llm.mcpServers.xml: {name}")

        except ET.ParseError as e:
            logger.warning(f"Error parsing {xml_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading {xml_path}: {e}")

        return mcp_servers

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

            # Format 1: standard JetBrains IDEs style
            for option in root.findall(".//option"):
                val = option.get("value")
                if val and ("$USER_HOME$" in val or "/" in val or "\\" in val):
                    paths.append(val)

            # Format 2: newer JetBrains IDEs style
            for entry in root.findall(".//entry"):
                key = entry.get("key")
                if key and ("$USER_HOME$" in key or "/" in key or "\\" in key):
                    paths.append(key)

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

    def _detect_project_mcp(self, project_path: Path) -> List[Dict]:
        """
        Scan a project folder for MCP configuration files.

        Args:
            project_path: Path to the project directory

        Returns:
            List of MCP server dicts found in the project
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

    def _read_rule_file(self, path: Path) -> Optional[Dict]:
        """
        Args:
            path: Path to the rule file

        Returns:
            Dict with file_path, file_name, content, size, last_modified, truncated
            or None if reading fails
        """
        try:
            if not path.exists() or not path.is_file():
                return None

            metadata = get_file_metadata(path)
            content, truncated = read_file_content(path, metadata['size'])

            return {
                "file_path": str(path),
                "file_name": path.name,
                "content": content,
                "size": metadata['size'],
                "last_modified": metadata['last_modified'],
                "truncated": truncated
            }
        except Exception as e:
            logger.warning(f"Error reading rule file {path}: {e}")
            return None

    def _detect_project_rules(self, project_path: Path) -> List[Dict]:
        """
        Scans for:
            - Exact file matches: .cursorrules, .windsurfrules, .prompts, GEMINI.md
            - Directory scans: *.md files inside .cline/rules/ and .aiassistant/rules/
            - Wildcard: all *.mdc files in the project root

        Args:
            project_path: Path to the project directory

        Returns:
            List of rule dicts matching the backend schema
        """
        rules = []

        # Exact file candidates
        exact_files = [
            ".cursorrules",
            ".windsurfrules",
            ".prompts",
            "GEMINI.md",
        ]

        for candidate in exact_files:
            rule_file = project_path / candidate
            if rule_file.is_file():
                rule_obj = self._read_rule_file(rule_file)
                if rule_obj:
                    rules.append(rule_obj)

        # Directory candidates - scan for *.md files inside each
        rule_dirs = [
            ".cline/rules",
            ".aiassistant/rules",
        ]

        for dir_candidate in rule_dirs:
            rule_dir = project_path / dir_candidate
            if rule_dir.is_dir():
                try:
                    for md_file in rule_dir.glob("*.md"):
                        rule_obj = self._read_rule_file(md_file)
                        if rule_obj:
                            rules.append(rule_obj)
                except PermissionError:
                    logger.debug(f"Permission denied scanning {rule_dir}")

        # Wildcard: all *.mdc files in the project root
        try:
            for mdc_file in project_path.glob("*.mdc"):
                rule_obj = self._read_rule_file(mdc_file)
                if rule_obj:
                    rules.append(rule_obj)
        except PermissionError:
            logger.debug(f"Permission denied scanning {project_path} for .mdc files")

        return rules
