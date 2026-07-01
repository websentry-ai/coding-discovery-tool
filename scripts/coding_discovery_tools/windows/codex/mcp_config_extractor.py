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
    TOOL_NAME,
    PARENT_LEVELS,
    read_codex_toml_mcp_config,
    extract_codex_global_mcp_config_with_admin_support,
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

        projects.extend(self._extract_global_config())

        project_configs = self._extract_project_level_configs()
        projects.extend(project_configs)

        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_global_config(self) -> List[Dict[str, Union[str, List[Dict[str, Any]]]]]:
        """
        Extract global MCP config from ~/.codex/config.toml.

        When running as administrator, accumulates global configs from ALL users
        (de-duplicated by path). Single-user / non-admin yields a 0-or-1 element
        list, identical in content to the single dict (or None) returned before.

        Returns:
            List of config dicts with 'path' and 'mcpServers' keys (empty if none found)
        """
        return extract_codex_global_mcp_config_with_admin_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            _is_admin_user,
            tool_name=TOOL_NAME,
            parent_levels=PARENT_LEVELS
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
                tool_name=TOOL_NAME,
                parent_levels=self._PROJECT_PARENT_LEVELS
            )
            if config:
                configs.append(config)
