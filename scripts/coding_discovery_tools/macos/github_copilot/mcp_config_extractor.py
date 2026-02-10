"""
MCP config extraction for GitHub Copilot on macOS systems.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)

class MacOSGitHubCopilotMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot MCP config on macOS systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract GitHub Copilot MCP configuration on macOS.
        """
        projects = []

        # Extract VS Code configs
        vscode_configs = self._extract_vscode_configs()
        projects.extend(vscode_configs)

        # Extract JetBrains configs
        jetbrains_configs = self._extract_jetbrains_configs()
        projects.extend(jetbrains_configs)

        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_vscode_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from VS Code.
        """
        return extract_ide_global_configs_with_root_support(
            self._extract_vscode_configs_for_user,
            tool_name="GitHub Copilot (VS Code)"
        )

    def _extract_vscode_configs_for_user(self, user_home: Path) -> List[Dict]:
        """
        Extract VS Code MCP configs for a specific user.
        """
        configs = []
        code_user_base = user_home / "Library" / "Application Support" / "Code" / "User"

        primary_path = code_user_base / "mcp.json"
        fallback_path = code_user_base / "globalStorage" / "ms-vscode.vscode-github-copilot" / "mcp.json"

        if primary_path.exists():
            config = self._read_mcp_config(primary_path, str(code_user_base))
            if config:
                configs.append(config)
                return configs

        if fallback_path.exists():
            config = self._read_mcp_config(fallback_path, str(fallback_path.parent))
            if config:
                configs.append(config)

        return configs

    def _extract_jetbrains_configs(self) -> List[Dict]:
        """
        Extract global MCP configs from JetBrains IDEs.
        """
        return extract_ide_global_configs_with_root_support(
            self._extract_jetbrains_configs_for_user,
            tool_name="GitHub Copilot (JetBrains)"
        )

    def _extract_jetbrains_configs_for_user(self, user_home: Path) -> List[Dict]:
        """
        Extract JetBrains MCP configs for a specific user.
        """
        configs = []
        jetbrains_config_path = user_home / ".config" / "github-copilot" / "intellij" / "mcp.json"

        if jetbrains_config_path.exists():
            config = self._read_mcp_config(jetbrains_config_path, str(jetbrains_config_path.parent))
            if config:
                configs.append(config)

        return configs

    def _read_mcp_config(self, config_path: Path, tool_path: str) -> Optional[Dict]:
        """
        Read and parse an MCP config file, stripping JSON comments.
        """
        try:
            content = config_path.read_text(encoding='utf-8', errors='replace')

            config_data = json.loads(content)

            mcp_servers_obj = config_data.get("servers") or config_data.get("mcpServers", {})

            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

            if mcp_servers_array:
                return {
                    "path": tool_path,
                    "mcpServers": mcp_servers_array
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in GitHub Copilot MCP config {config_path}: {e}")
        except PermissionError as e:
            logger.debug(f"Permission denied reading GitHub Copilot MCP config {config_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading GitHub Copilot MCP config {config_path}: {e}")

        return None
