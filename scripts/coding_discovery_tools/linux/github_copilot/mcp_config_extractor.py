"""MCP config extraction for GitHub Copilot on Linux systems."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH, SKIP_DIRS
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    transform_mcp_servers_to_array,
    _strip_jsonc_comments,
    _strip_trailing_commas,
)

logger = logging.getLogger(__name__)


class LinuxGitHubCopilotMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot MCP config on Linux systems."""

    def extract_mcp_config(self, tool_name: Optional[str] = None) -> Optional[Dict]:
        # Scope MCP sources to the surface: a VS Code row gets VS Code global +
        # workspace .vscode/mcp.json; a JetBrains row gets JetBrains global only.
        # tool_name=None keeps the legacy union (back-compat / direct callers).
        name = (tool_name or "").lower()
        is_vscode = ("vs code" in name) or ("vscode" in name)
        want_vscode = (not tool_name) or is_vscode
        want_jetbrains = (not tool_name) or (not is_vscode)

        projects = []
        if want_vscode:
            projects.extend(self._extract_vscode_configs())
            projects.extend(self._extract_workspace_configs())
        if want_jetbrains:
            projects.extend(self._extract_jetbrains_configs())

        return {"projects": projects} if projects else None

    def _extract_vscode_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            configs.extend(self._extract_vscode_configs_for_user(user_home))
        return configs

    def _extract_vscode_configs_for_user(self, user_home: Path) -> List[Dict]:
        configs = []
        code_user_base = user_home / ".config" / "Code" / "User"

        primary_path = code_user_base / "mcp.json"

        if primary_path.exists():
            config = self._read_mcp_config(primary_path, str(code_user_base))
            if config:
                configs.append(config)

        return configs

    def _extract_jetbrains_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            configs.extend(self._extract_jetbrains_configs_for_user(user_home))
        return configs

    def _extract_jetbrains_configs_for_user(self, user_home: Path) -> List[Dict]:
        configs = []
        jetbrains_config_path = (
            user_home / ".config" / "github-copilot" / "intellij" / "mcp.json"
        )
        if jetbrains_config_path.exists():
            config = self._read_mcp_config(jetbrains_config_path, str(jetbrains_config_path.parent))
            if config:
                configs.append(config)
        return configs

    def _extract_workspace_configs(self) -> List[Dict]:
        configs = []
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_workspace_mcp(user_home, user_home, configs, current_depth=0)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")
        return configs

    def _walk_for_workspace_mcp(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        current_depth: int = 0,
    ) -> None:
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
        except (PermissionError, OSError):
            pass

    def _check_vscode_mcp(self, vscode_dir: Path, configs: List[Dict]) -> None:
        mcp_json = vscode_dir / "mcp.json"
        if mcp_json.exists() and mcp_json.is_file():
            project_root = str(vscode_dir.parent)
            config = self._read_mcp_config(mcp_json, project_root)
            if config:
                configs.append(config)

    def _read_mcp_config(self, config_path: Path, tool_path: str) -> Optional[Dict]:
        try:
            content = config_path.read_text(encoding="utf-8", errors="replace")
            content = _strip_jsonc_comments(content)
            content = _strip_trailing_commas(content)
            config_data = json.loads(content)
            mcp_servers_obj = config_data.get("servers") or config_data.get("mcpServers", {})
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
            if mcp_servers_array:
                return {
                    "path": tool_path,
                    "mcpServers": mcp_servers_array,
                }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in GitHub Copilot MCP config {config_path}: {e}")
        except PermissionError as e:
            logger.debug(f"Permission denied reading {config_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading GitHub Copilot MCP config {config_path}: {e}")
        return None
