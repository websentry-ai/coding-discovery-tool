"""MCP config extraction for Codex on Linux systems."""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_running_as_root,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.codex.mcp_config_extractor import (
    read_codex_toml_mcp_config,
)

logger = logging.getLogger(__name__)

_TOOL_NAME = "Codex"
_PARENT_LEVELS = 1
_PROJECT_PARENT_LEVELS = 2


class LinuxCodexMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Codex MCP config on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = []

        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

        projects.extend(self._extract_project_level_configs())

        return {"projects": projects} if projects else None

    def _extract_global_config(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            config_path = user_home / ".codex" / "config.toml"
            if config_path.exists():
                config = read_codex_toml_mcp_config(config_path, _TOOL_NAME, _PARENT_LEVELS)
                if config:
                    return config
        return None

    def _extract_project_level_configs(self) -> List[Dict]:
        configs = []

        for user_home in get_linux_user_homes():
            try:
                self._walk_for_codex_configs(
                    user_home, user_home, configs,
                    user_home / ".codex",
                    current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return configs

    def _walk_for_codex_configs(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        global_codex_dir: Path,
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
                        if item.name == ".codex":
                            if item == global_codex_dir:
                                continue
                            self._extract_config_from_codex_dir(item, configs)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_codex_configs(
                            root_path, item, configs, global_codex_dir, current_depth + 1
                        )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass

    def _extract_config_from_codex_dir(self, codex_dir: Path, configs: List[Dict]) -> None:
        config_toml = codex_dir / "config.toml"
        if config_toml.exists() and config_toml.is_file():
            config = read_codex_toml_mcp_config(config_toml, _TOOL_NAME, _PROJECT_PARENT_LEVELS)
            if config:
                configs.append(config)
