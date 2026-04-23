import json
import logging
import os
import re
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH, SKIP_DIRS
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
)
from ...windows_extraction_helpers import (
    should_skip_path,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)

class WindowsGitHubCopilotMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot MCP config on Windows systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract GitHub Copilot MCP configuration on Windows.

        - VS Code global: %APPDATA%\\Code\\User\\mcp.json
        - VS Code (fallback): %APPDATA%\\Code\\User\\globalStorage\\ms-vscode.vscode-github-copilot\\mcp.json
        - JetBrains global: %LOCALAPPDATA%\\github-copilot\\intellij\\mcp.json
        - Workspace: **\\.vscode\\mcp.json
        """
        projects = []

        # Extract VS Code global configs
        vscode_configs = self._extract_vscode_configs()
        projects.extend(vscode_configs)

        # Extract JetBrains global configs
        jetbrains_configs = self._extract_jetbrains_configs()
        projects.extend(jetbrains_configs)

        # Extract workspace-level .vscode/mcp.json configs
        workspace_configs = self._extract_workspace_configs()
        projects.extend(workspace_configs)

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

    def _extract_workspace_configs(self) -> List[Dict]:
        """
        Extract workspace-level .vscode\\mcp.json configs from project directories.

        Since .vscode is in SKIP_DIRS, the general directory walk skips it.
        This method walks for project directories and directly checks for
        .vscode\\mcp.json in each, bypassing the SKIP_DIRS filter.
        """
        configs = []
        root_drive = Path.home().anchor
        root_path = Path(root_drive)
        system_dirs = get_windows_system_directories()

        try:
            top_level_dirs = [
                item for item in root_path.iterdir()
                if item.is_dir() and not item.name.startswith('.')
                and not should_skip_path(item, system_dirs)
            ]
            for top_dir in top_level_dirs:
                try:
                    self._walk_for_workspace_mcp(root_path, top_dir, configs, system_dirs, current_depth=1)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing root directory for workspace MCP scan: {e}")

        return configs

    def _walk_for_workspace_mcp(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        system_dirs: set,
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directories looking for .vscode\\mcp.json files.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item, system_dirs):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name in SKIP_DIRS and item.name != ".vscode":
                            continue
                        if item.name == ".vscode":
                            self._check_vscode_mcp(item, configs)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_workspace_mcp(root_path, item, configs, system_dirs, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _check_vscode_mcp(self, vscode_dir: Path, configs: List[Dict]) -> None:
        """
        Check a .vscode directory for mcp.json and extract its config.
        """
        mcp_json = vscode_dir / "mcp.json"
        if mcp_json.exists() and mcp_json.is_file():
            project_root = str(vscode_dir.parent)
            config = self._read_mcp_config(mcp_json, project_root)
            if config:
                configs.append(config)

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
