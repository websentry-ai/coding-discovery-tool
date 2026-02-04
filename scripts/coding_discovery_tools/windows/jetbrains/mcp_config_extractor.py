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

    JETBRAINS_CONFIG_DIR = Path.home() / "AppData" / "Roaming" / "JetBrains"

    IDE_PATTERNS = [
        "IntelliJIdea", "IntelliJ", "PyCharm", "WebStorm", "PhpStorm",
        "GoLand", "Rider", "CLion", "RustRover", "RubyMine", "DataGrip",
        "DataSpell", "Fleet"
    ]

    # MCP config file candidates (project-level)
    MCP_CONFIG_FILES = [
        "mcp.json",
        ".mcp/config.json",
        ".mcp.json",
        "claude_mcp_config.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
    ]

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

        # Extract global MCP servers from IDE-level configuration
        ide_mcp_servers = self._extract_ide_mcp_servers(config_path)

        # Find recent projects XML files
        recent_files = [
            config_path / "options" / "recentProjects.xml",
            config_path / "options" / "recentSolutions.xml",  # Rider
            config_path / "options" / "recentProjectDirectories.xml",
        ]

        project_paths = set()

        for recent_file in recent_files:
            if recent_file.exists():
                project_paths.update(self._parse_recent_projects_xml(recent_file))

        # Also check workspace.xml for open projects
        workspace = config_path / "workspace.xml"
        if workspace.exists():
            project_paths.update(self._extract_project_paths_from_xml(workspace))

        if not project_paths:
            logger.debug(f"No project paths found for {ide_name}")
            return projects

        logger.info(f"Found {len(project_paths)} projects in {ide_name}")

        # Check each project for MCP config and rules
        for project_path_str in project_paths:
            # Normalize path for Windows
            project_path_str = self._normalize_path(project_path_str)
            project_path = Path(project_path_str)

            if not project_path.exists() or not project_path.is_dir():
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
        Extract MCP server configurations from IDE-level XML files.

        Parses global MCP configuration files (llm.mcpServers.xml, aiAssistant.xml, etc.)
        Handles multiple JetBrains XML formats (2024.x and 2025.x).

        Args:
            config_path: Path to the IDE config directory

        Returns:
            List of MCP server dicts with name, command, and args
        """
        servers = []

        # Primary locations for MCP config
        xml_paths = [
            config_path / "options" / "llm.mcpServers.xml",
            config_path / "options" / "aiAssistant.xml",
            config_path / "options" / "mcp.xml",
        ]

        for xml_path in xml_paths:
            if not xml_path.exists():
                continue

            try:
                parsed_servers = self._parse_mcp_xml(xml_path)
                servers.extend(parsed_servers)
            except ET.ParseError as e:
                logger.warning(f"XML parse error in {xml_path.name}: {e}")
            except Exception as e:
                logger.warning(f"Error reading {xml_path.name}: {e}")

        return servers

    def _parse_mcp_xml(self, xml_path: Path) -> List[Dict]:
        """Parse JetBrains MCP XML configuration file."""
        servers = []

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            for server_node in root.findall(".//McpServerConfigurationProperties"):
                server = self._parse_mcp_server_node(server_node)
                if server:
                    servers.append(server)

            for item in root.findall(".//item"):
                if item.find(".//option[@name='command']") is not None or \
                   item.find(".//option[@name='url']") is not None:
                    server = self._parse_mcp_server_node(item)
                    if server:
                        servers.append(server)

            for entry in root.findall(".//entry"):
                key = entry.get("key")
                value_node = entry.find("value")
                if key and value_node is not None:
                    server = self._parse_mcp_server_node(value_node)
                    if server:
                        if server.get("name") == "Unknown":
                            server["name"] = key
                        servers.append(server)

        except Exception as e:
            logger.warning(f"Error parsing {xml_path}: {e}")

        return servers

    def _parse_mcp_server_node(self, node: ET.Element) -> Optional[Dict]:
        """Parse a single MCP server configuration node."""
        # Helper to get option value
        def get_opt(name: str, default: str = "") -> str:
            el = node.find(f".//option[@name='{name}']")
            return el.get("value", default) if el is not None else default

        # Get name
        name = get_opt("name", "Unknown")

        # Check for nested transport properties (2025.3+ format)
        local_props = node.find(".//McpLocalServerProperties")

        if local_props is not None:
            command = self._get_nested_opt(local_props, "command")
            args = self._parse_args(self._get_nested_opt(local_props, "args"))
        else:
            # Fallback: top-level attributes (older versions)
            command = get_opt("command")
            args = self._parse_args(get_opt("args"))

        # Skip empty/invalid entries (must have name and command)
        if name == "Unknown" or not command:
            return None

        return {
            "name": name,
            "command": command,
            "args": args
        }

    def _get_nested_opt(self, node: ET.Element, name: str) -> Optional[str]:
        """Get option value from nested element."""
        el = node.find(f"option[@name='{name}']")
        return el.get("value") if el is not None else None

    def _parse_args(self, args_str: Optional[str]) -> List[str]:
        """Parse JetBrains stringified argument list."""
        if not args_str:
            return []

        args_str = args_str.strip()

        if args_str.startswith("[") and args_str.endswith("]"):
            try:
                return json.loads(args_str)
            except json.JSONDecodeError:
                try:
                    cleaned = args_str.replace("'", '"')
                    return json.loads(cleaned)
                except json.JSONDecodeError:
                    pass

        # Handle comma-separated
        if "," in args_str and not args_str.startswith("-"):
            return [a.strip().strip("'\"") for a in args_str.split(",")]

        if " " in args_str and not any(c in args_str for c in ["/", "\\", ":"]):
            return args_str.split()

        return [args_str] if args_str else []

    def _parse_recent_projects_xml(self, xml_file: Path) -> set:
        """
        Parse recentProjects.xml to extract project paths.

        Handles both formats used by modern JetBrains IDEs:
        - Standard: <option value="$USER_HOME$/..." />
        - Newer: <entry key="$USER_HOME$/..." />

        Args:
            xml_file: Path to recentProjects.xml

        Returns:
            Set of project path strings
        """
        return self._extract_project_paths_from_xml(xml_file)

    def _extract_project_paths_from_xml(self, xml_path: Path) -> set:
        """
        Extract project paths from JetBrains XML file.

        Args:
            xml_path: Path to the XML file

        Returns:
            Set of project path strings
        """
        paths = set()

        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # Various path formats used by JetBrains
            for el in root.iter():
                for attr in ["value", "key", "path", "projectPath"]:
                    val = el.get(attr)
                    if val and self._looks_like_path(val):
                        paths.add(val)

                # Check text content
                if el.text and self._looks_like_path(el.text):
                    paths.add(el.text)

        except Exception as e:
            logger.warning(f"Error parsing {xml_path}: {e}")

        return paths

    def _looks_like_path(self, val: str) -> bool:
        """Check if string looks like a file path."""
        if not val or len(val) < 3:
            return False
        indicators = ["$USER_HOME$", "C:\\", "D:\\", "E:\\", "F:\\", "/Users/", "/home/", "~/"]
        return any(ind in val for ind in indicators) or val.startswith("/") or (len(val) > 1 and val[1] == ":")

    def _normalize_path(self, path: str) -> str:
        """Normalize JetBrains path variables to actual paths for Windows."""
        home = str(Path.home())
        path = path.replace("$USER_HOME$", home)
        path = path.replace("$HOME$", home)
        path = path.replace("~", home)
        # Convert forward slashes to backslashes for Windows
        path = path.replace("/", "\\")
        return path

    def _detect_project_mcp(self, project_path: Path) -> List[Dict]:
        """
        Scan a project folder for MCP configuration files.

        Args:
            project_path: Path to the project directory

        Returns:
            List of MCP server dicts found in the project
        """
        mcp_servers = []

        for config_file in self.MCP_CONFIG_FILES:
            config_path = project_path / config_file
            if not config_path.exists():
                continue

            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Standard mcpServers format
                mcp_servers_dict = data.get("mcpServers", data.get("servers", {}))

                for name, config in mcp_servers_dict.items():
                    # Only extract stdio servers (with command)
                    if "command" in config:
                        mcp_servers.append({
                            "name": name,
                            "command": config["command"],
                            "args": config.get("args", [])
                        })

                logger.info(f"Found MCP config at {config_path} with {len(mcp_servers_dict)} server(s)")

            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in {config_path}: {e}")
            except Exception as e:
                logger.warning(f"Error reading {config_path}: {e}")

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
