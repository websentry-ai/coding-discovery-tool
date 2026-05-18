"""MCP config extraction for JetBrains IDEs on Linux systems."""

import json
import logging
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import get_linux_user_homes
from ...macos_extraction_helpers import get_file_metadata, read_file_content

logger = logging.getLogger(__name__)


class LinuxJetBrainsMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for JetBrains IDEs MCP config on Linux systems."""

    IDE_PATTERNS = [
        "IntelliJIdea", "IntelliJ", "PyCharm", "WebStorm", "PhpStorm",
        "GoLand", "Rider", "CLion", "RustRover", "RubyMine", "DataGrip",
        "DataSpell", "Fleet",
    ]

    MCP_CONFIG_FILES = [
        "mcp.json",
        ".mcp/config.json",
        ".mcp.json",
        "claude_mcp_config.json",
        ".cursor/mcp.json",
        ".vscode/mcp.json",
    ]

    def extract_mcp_config(self) -> Optional[Dict]:
        all_projects = []
        for user_home in get_linux_user_homes():
            try:
                all_projects.extend(self._extract_jetbrains_projects_for_user(user_home))
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        if not all_projects:
            return None
        return {"projects": all_projects}

    def _extract_jetbrains_projects_for_user(self, user_home: Path) -> List[Dict]:
        all_projects = []
        jetbrains_root = user_home / ".config" / "JetBrains"

        if not jetbrains_root.exists():
            return all_projects

        try:
            for folder in os.listdir(jetbrains_root):
                folder_path = jetbrains_root / folder
                if folder.startswith(".") or not folder_path.is_dir():
                    continue
                if not any(pattern in folder for pattern in self.IDE_PATTERNS):
                    continue
                ide_projects = self._extract_ide_projects(folder_path, folder, user_home)
                all_projects.extend(ide_projects)
        except PermissionError as e:
            logger.error(f"Permission denied reading {jetbrains_root}: {e}")
        except Exception as e:
            logger.warning(f"Error scanning {jetbrains_root}: {e}")

        return all_projects

    def _extract_ide_projects(self, config_path: Path, ide_name: str, user_home: Path) -> List[Dict]:
        projects = []
        ide_mcp_servers = self._extract_ide_mcp_servers(config_path)

        recent_files = [
            config_path / "options" / "recentProjects.xml",
            config_path / "options" / "recentSolutions.xml",
            config_path / "options" / "recentProjectDirectories.xml",
        ]

        project_paths = set()
        for recent_file in recent_files:
            if recent_file.exists():
                project_paths.update(self._extract_project_paths_from_xml(recent_file))

        if not project_paths:
            return projects

        for project_path_str in project_paths:
            project_path_str = self._normalize_path(project_path_str, user_home)
            project_path = Path(project_path_str)
            try:
                if not project_path.exists() or not project_path.is_dir():
                    continue
            except PermissionError:
                continue

            mcp_servers = self._detect_project_mcp(project_path)
            rules = self._detect_project_rules(project_path)
            combined_mcp_servers = ide_mcp_servers + mcp_servers

            if combined_mcp_servers or rules:
                projects.append({
                    "path": str(project_path),
                    "mcpServers": combined_mcp_servers,
                    "rules": rules,
                })

        return projects

    def _extract_project_paths_from_xml(self, xml_path: Path) -> set:
        paths = set()
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            for el in root.iter():
                for attr in ["value", "key", "path", "projectPath"]:
                    val = el.get(attr)
                    if val and self._looks_like_path(val):
                        paths.add(val)
                if el.text and self._looks_like_path(el.text):
                    paths.add(el.text)
        except PermissionError:
            logger.debug(f"Permission denied reading {xml_path}")
        except Exception as e:
            logger.warning(f"Error parsing {xml_path}: {e}")
        return paths

    def _looks_like_path(self, val: str) -> bool:
        indicators = ["$USER_HOME$", "/Users/", "/home/", "~/"]
        return any(ind in val for ind in indicators) or val.startswith("/")

    def _normalize_path(self, path: str, user_home: Path) -> str:
        home_str = str(user_home)
        path = path.replace("$USER_HOME$", home_str)
        path = path.replace("$HOME$", home_str)
        if path.startswith("~"):
            path = home_str + path[1:]
        return path

    def _extract_ide_mcp_servers(self, config_path: Path) -> List[Dict]:
        servers = []
        xml_paths = [
            config_path / "options" / "llm.mcpServers.xml",
            config_path / "options" / "aiAssistant.xml",
            config_path / "options" / "mcp.xml",
        ]
        for xml_path in xml_paths:
            if xml_path.exists():
                try:
                    parsed_servers = self._parse_mcp_xml(xml_path)
                    servers.extend(parsed_servers)
                except PermissionError:
                    logger.debug(f"Permission denied reading {xml_path}")
                except Exception as e:
                    logger.warning(f"Error reading {xml_path.name}: {e}")
        return servers

    def _parse_mcp_xml(self, xml_path: Path) -> List[Dict]:
        servers = []
        try:
            tree = ET.parse(xml_path)
            for node in tree.findall(".//McpServerConfigurationProperties"):
                def get_opt(n, name):
                    if n is None:
                        return None
                    el = n.find(f"option[@name='{name}']")
                    return el.get("value") if el is not None else None

                name = get_opt(node, "name")
                if not name:
                    continue

                local_props = node.find(".//McpLocalServerProperties")
                if local_props is not None:
                    command = get_opt(local_props, "command") or "builtin"
                    args = self._parse_args(get_opt(local_props, "args"))
                else:
                    command = "builtin"
                    args = []

                servers.append({
                    "name": name,
                    "command": command,
                    "args": args,
                    "enabled": get_opt(node, "enabled") != "false",
                })
        except Exception as e:
            logger.warning(f"Error parsing {xml_path}: {e}")
        return servers

    def _parse_args(self, args_str: Optional[str]) -> List[str]:
        if not args_str:
            return []
        args_str = args_str.strip()
        if args_str.startswith("[") and args_str.endswith("]"):
            try:
                return json.loads(args_str.replace("'", '"'))
            except Exception:
                pass
        return [args_str] if args_str else []

    def _detect_project_mcp(self, project_path: Path) -> List[Dict]:
        mcp_servers = []
        for config_file in self.MCP_CONFIG_FILES:
            path = project_path / config_file
            try:
                if path.exists():
                    data = json.loads(path.read_text())
                    mcp_dict = data.get("mcpServers", data.get("servers", {}))
                    for name, config in mcp_dict.items():
                        if "command" in config:
                            mcp_servers.append({
                                "name": name,
                                "command": config["command"],
                                "args": config.get("args", []),
                            })
            except PermissionError:
                logger.debug(f"Permission denied reading {path}")
            except Exception:
                pass
        return mcp_servers

    def _detect_project_rules(self, project_path: Path) -> List[Dict]:
        rules = []
        candidates = [".cursorrules", ".windsurfrules", ".prompts", "GEMINI.md"]
        for c in candidates:
            p = project_path / c
            try:
                if p.is_file():
                    rule = self._read_rule_file(p, scope="project")
                    if rule:
                        rules.append(rule)
            except PermissionError:
                logger.debug(f"Permission denied reading {p}")

        for d in [".cline/rules", ".aiassistant/rules"]:
            dir_path = project_path / d
            try:
                if dir_path.is_dir():
                    for f in dir_path.glob("*.md"):
                        rule = self._read_rule_file(f, scope="project")
                        if rule:
                            rules.append(rule)
            except PermissionError:
                logger.debug(f"Permission denied reading {dir_path}")

        return rules

    def _read_rule_file(self, path: Path, scope: str = "project") -> Optional[Dict]:
        try:
            metadata = get_file_metadata(path)
            content, truncated = read_file_content(path, metadata["size"])
            return {
                "file_path": str(path),
                "file_name": path.name,
                "content": content,
                "size": metadata["size"],
                "last_modified": metadata["last_modified"],
                "truncated": truncated,
                "scope": scope,
            }
        except PermissionError:
            logger.debug(f"Permission denied reading rule file: {path}")
            return None
        except Exception:
            return None
