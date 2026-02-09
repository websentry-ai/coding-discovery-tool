import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)


def clean_json_comments(text: str) -> str:
    """
    Remove comments from JSONC content while preserving URLs (http://)
    """
    pattern = r'("(?:\\.|[^"\\])*")|(/\*.*?\*/)|(//.*$)'

    def replace(match):
        if match.group(1):
            return match.group(1)
        return ""

    return re.sub(pattern, replace, text, flags=re.DOTALL | re.MULTILINE)


class WindowsGitHubCopilotMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot MCP config on Windows systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract GitHub Copilot MCP configuration on Windows.

        - VS Code: %APPDATA%\\Code\\User\\mcp.json
        - VS Code (fallback): %APPDATA%\\Code\\User\\globalStorage\\ms-vscode.vscode-github-copilot\\mcp.json
        - JetBrains: %LOCALAPPDATA%\\github-copilot\\intellij\\mcp.json
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

        - %APPDATA%\\Code\\User\\mcp.json
        - %APPDATA%\\Code\\User\\globalStorage\\ms-vscode.vscode-github-copilot\\mcp.json
        """
        configs = []

        appdata_roaming = user_home / "AppData" / "Roaming"
        code_user_base = appdata_roaming / "Code" / "User"

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

        Location: %LOCALAPPDATA%\\github-copilot\\intellij\\mcp.json
        """
        configs = []

        localappdata = user_home / "AppData" / "Local"
        jetbrains_config_path = localappdata / "github-copilot" / "intellij" / "mcp.json"

        if jetbrains_config_path.exists():
            config = self._read_mcp_config(jetbrains_config_path, str(jetbrains_config_path.parent))
            if config:
                configs.append(config)

        return configs

    def _read_mcp_config(self, config_path: Path, tool_path: str) -> Optional[Dict]:
        """
        Read and parse an MCP config file, stripping JSON comments.

        Uses robust JSONC parser to handle comments without breaking URLs.

        Args:
            config_path: Path to the mcp.json file
            tool_path: Path to use as the project/tool path in output

        Returns:
            Dict with 'path' and 'mcpServers' keys, or None if parsing fails
        """
        try:
            content = config_path.read_text(encoding='utf-8', errors='replace')
            content = clean_json_comments(content)

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
