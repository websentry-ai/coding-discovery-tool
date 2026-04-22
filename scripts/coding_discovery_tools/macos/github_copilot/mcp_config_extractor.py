"""
MCP config extraction for GitHub Copilot on macOS systems.
"""

import json
import logging
import re
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH, SKIP_DIRS
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
)
from ...macos_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)

class MacOSGitHubCopilotMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot MCP config on macOS systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract GitHub Copilot MCP configuration on macOS.
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

    def _extract_workspace_configs(self) -> List[Dict]:
        """
        Extract workspace-level .vscode/mcp.json configs from project directories.

        Since .vscode is in SKIP_DIRS, the general directory walk skips it.
        This method walks for project directories and directly checks for
        .vscode/mcp.json in each, bypassing the SKIP_DIRS filter.
        """
        configs = []
        root_path = Path("/")

        try:
            top_level_dirs = get_top_level_directories(root_path)
            for top_dir in top_level_dirs:
                try:
                    self._walk_for_workspace_mcp(root_path, top_dir, configs, current_depth=1)
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
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directories looking for .vscode/mcp.json files.

        Args:
            root_path: Root search path
            current_dir: Current directory being processed
            configs: List to populate with MCP configs
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        # Skip dirs from SKIP_DIRS but still check .vscode directly
                        if item.name in SKIP_DIRS and item.name != ".vscode":
                            continue
                        if item.name == ".vscode":
                            self._check_vscode_mcp(item, configs)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_workspace_mcp(root_path, item, configs, current_depth + 1)

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

        Args:
            vscode_dir: Path to the .vscode directory
            configs: List to populate with MCP configs
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
