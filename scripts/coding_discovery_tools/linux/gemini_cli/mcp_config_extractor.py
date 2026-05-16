"""MCP config extraction for Gemini CLI on Linux systems."""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import transform_mcp_servers_to_array

logger = logging.getLogger(__name__)


class LinuxGeminiCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Gemini CLI MCP config on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        projects = []

        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

        projects.extend(self._extract_project_level_configs())

        return {"projects": projects} if projects else None

    def _extract_global_config(self) -> Optional[Dict]:
        for user_home in get_linux_user_homes():
            config_path = user_home / ".gemini" / "settings.json"
            if config_path.exists():
                config = self._extract_config_from_gemini_dir(config_path.parent)
                if config:
                    return config
        return None

    def _extract_project_level_configs(self) -> List[Dict]:
        configs = []

        for user_home in get_linux_user_homes():
            global_gemini_dir = user_home / ".gemini"
            try:
                self._walk_for_gemini_configs(
                    user_home, user_home, configs, global_gemini_dir, current_depth=0
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return configs

    def _walk_for_gemini_configs(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        global_gemini_dir: Path,
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
                        if item.name == ".gemini":
                            if item == global_gemini_dir:
                                continue
                            config = self._extract_config_from_gemini_dir(item)
                            if config:
                                configs.append(config)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_gemini_configs(
                            root_path, item, configs, global_gemini_dir, current_depth + 1
                        )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass

    def _extract_config_from_gemini_dir(self, gemini_dir: Path) -> Optional[Dict]:
        settings_file = gemini_dir / "settings.json"
        if not settings_file.exists():
            return None

        try:
            content = settings_file.read_text(encoding="utf-8", errors="replace")
            config_data = json.loads(content)
            mcp_servers_obj = config_data.get("mcpServers", {})
            if not mcp_servers_obj:
                return None
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
            if mcp_servers_array:
                return {
                    "path": str(gemini_dir.parent),
                    "mcpServers": mcp_servers_array,
                }
        except json.JSONDecodeError as e:
            logger.debug(f"Invalid JSON in {settings_file}: {e}")
        except PermissionError as e:
            logger.debug(f"Permission denied reading {settings_file}: {e}")
        except Exception as e:
            logger.debug(f"Error reading {settings_file}: {e}")

        return None
