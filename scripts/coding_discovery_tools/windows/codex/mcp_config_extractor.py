"""
MCP config extraction for Codex on Windows systems.

Codex uses TOML format for configuration files located at ~/.codex/config.toml.
This extractor parses the TOML file to extract MCP server configurations.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...toml_mcp_helpers import (
    _TOOL_NAME,
    _PARENT_LEVELS,
    read_codex_toml_mcp_config,
)
from ...windows_extraction_helpers import (
    is_running_as_admin,
    should_skip_path,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)


def _is_admin_user() -> Tuple[bool, Optional[Path]]:
    """
    Check if running as admin user and get users directory.

    Returns:
        Tuple of (is_admin, users_dir) where users_dir is None if not admin
    """
    is_admin = is_running_as_admin()
    users_dir = Path("C:\\Users") if is_admin else None
    return is_admin, users_dir


def _extract_config_from_user_directories(
    global_config_path: Path,
    tool_name: str,
    parent_levels: int
) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
    """
    Extract MCP config from all user directories (when running as administrator).

    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path

    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    is_admin, users_dir = _is_admin_user()

    if not is_admin or not users_dir or not users_dir.exists():
        return None

    for user_dir in users_dir.iterdir():
        if not user_dir.is_dir() or user_dir.name.startswith('.'):
            continue

        try:
            user_config_path = user_dir / global_config_path.relative_to(Path.home())
            if user_config_path.exists():
                config = read_codex_toml_mcp_config(user_config_path, tool_name, parent_levels)
                if config:
                    return config
        except (ValueError, OSError):
            continue

    return None


def extract_codex_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = _TOOL_NAME,
    parent_levels: int = _PARENT_LEVELS
) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
    """
    Extract global Codex MCP config with support for admin user.

    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path

    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    config = _extract_config_from_user_directories(
        global_config_path, tool_name, parent_levels
    )
    if config:
        return config

    if global_config_path.exists():
        return read_codex_toml_mcp_config(global_config_path, tool_name, parent_levels)

    return None


class WindowsCodexMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Codex MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".codex" / "config.toml"

    # Project-level config uses parent_levels=2: <project>/.codex/config.toml -> 2 levels up = <project>
    _PROJECT_PARENT_LEVELS = 2

    def extract_mcp_config(self) -> Optional[Dict[str, List[Dict[str, Any]]]]:
        """
        Extract Codex MCP configuration on Windows.

        Extracts global MCP config from ~/.codex/config.toml and
        project-level configs from **\\.codex\\config.toml.

        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []

        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

        project_configs = self._extract_project_level_configs()
        projects.extend(project_configs)

        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_global_config(self) -> Optional[Dict[str, Union[str, List[Dict[str, Any]]]]]:
        """
        Extract global MCP config from ~/.codex/config.toml.

        When running as administrator, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.

        Returns:
            Dict with 'path' and 'mcpServers' keys, or None if not found
        """
        return extract_codex_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name=_TOOL_NAME,
            parent_levels=_PARENT_LEVELS
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """
        Extract project-level MCP configs from **\\.codex\\config.toml.

        Walks the filesystem looking for .codex directories at project level,
        skipping the global ~/.codex directory to avoid duplicates.

        Returns:
            List of MCP config dicts
        """
        configs = []
        root_drive = Path.home().anchor
        root_path = Path(root_drive)
        system_dirs = get_windows_system_directories()

        # Global .codex directory to skip
        global_codex_dir = Path.home() / ".codex"

        try:
            top_level_dirs = [
                item for item in root_path.iterdir()
                if item.is_dir() and not item.name.startswith('.')
                and not should_skip_path(item, system_dirs)
            ]
            for top_dir in top_level_dirs:
                try:
                    self._walk_for_codex_configs(
                        root_path, top_dir, configs, system_dirs, global_codex_dir, current_depth=1
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing root for Codex project scan: {e}")

        return configs

    def _walk_for_codex_configs(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        system_dirs: set,
        global_codex_dir: Path,
        current_depth: int = 0
    ) -> None:
        """Recursively walk directories looking for .codex\\config.toml files."""
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
                        if item.name == ".codex":
                            if item == global_codex_dir:
                                continue
                            self._extract_config_from_codex_dir(item, configs)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_codex_configs(
                            root_path, item, configs, system_dirs, global_codex_dir, current_depth + 1
                        )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_config_from_codex_dir(self, codex_dir: Path, configs: List[Dict]) -> None:
        """
        Extract MCP config from a .codex directory's config.toml.

        Uses parent_levels=2 to resolve the project root:
        <project>\\.codex\\config.toml -> 2 levels up -> <project>

        Args:
            codex_dir: Path to the .codex directory
            configs: List to populate with MCP configs
        """
        config_toml = codex_dir / "config.toml"
        if config_toml.exists() and config_toml.is_file():
            config = read_codex_toml_mcp_config(
                config_toml,
                tool_name=_TOOL_NAME,
                parent_levels=self._PROJECT_PARENT_LEVELS
            )
            if config:
                configs.append(config)
