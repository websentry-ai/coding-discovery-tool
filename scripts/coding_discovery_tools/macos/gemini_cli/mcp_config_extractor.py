"""
MCP config extraction for Gemini CLI on macOS systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_global_mcp_config_with_root_support,
    transform_mcp_servers_to_array,
)
from ...macos_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


class MacOSGeminiCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Gemini CLI MCP config on macOS systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".gemini" / "settings.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Gemini CLI MCP configuration on macOS.

        Extracts both global and project-level MCP configs:
        - Global: ~/.gemini/settings.json
        - Project: <project>/.gemini/settings.json

        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        
        # Extract global config
        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

        # Extract project-level configs
        project_configs = self._extract_project_level_configs()
        projects.extend(project_configs)

        # Return None if no configs found
        if not projects:
            return None

        return {
            "projects": projects
        }

    def _extract_global_config(self) -> Optional[Dict]:
        """
        Extract global MCP config from ~/.gemini/settings.json

        When running as root, collects global configs from ALL users.
        Returns the first non-empty config found, or None if none found.
        """
        return extract_global_mcp_config_with_root_support(
            self.GLOBAL_MCP_CONFIG_PATH,
            tool_name="Gemini CLI",
            parent_levels=2  # ~/.gemini/settings.json -> 2 levels up = ~
        )

    def _extract_project_level_configs(self) -> List[Dict]:
        """
        Extract project-level MCP configs from .gemini/settings.json files.

        Searches for .gemini directories across the filesystem.
        """
        configs = []
        root_path = Path("/")

        try:
            top_level_dirs = get_top_level_directories(root_path)

            for top_dir in top_level_dirs:
                try:
                    self._walk_for_gemini_configs(root_path, top_dir, configs, current_depth=1)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            home_path = Path.home()
            for gemini_dir in home_path.rglob(".gemini"):
                try:
                    config = self._extract_config_from_gemini_dir(gemini_dir)
                    if config:
                        configs.append(config)
                except (PermissionError, OSError):
                    continue

        return configs

    def _walk_for_gemini_configs(
        self,
        root_path: Path,
        current_dir: Path,
        configs: List[Dict],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .gemini directories.
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
                        if item.name == ".gemini":
                            if item.parent == Path.home():
                                continue
                            config = self._extract_config_from_gemini_dir(item)
                            if config:
                                configs.append(config)
                            continue

                        self._walk_for_gemini_configs(root_path, item, configs, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_config_from_gemini_dir(self, gemini_dir: Path) -> Optional[Dict]:
        """
        Extract MCP config from a .gemini directory's settings.json.
        """
        settings_file = gemini_dir / "settings.json"

        if not settings_file.exists():
            return None

        try:
            content = settings_file.read_text(encoding='utf-8', errors='replace')
            config_data = json.loads(content)

            mcp_servers_obj = config_data.get("mcpServers", {})

            if not mcp_servers_obj:
                return None

            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

            if mcp_servers_array:
                return {
                    "path": str(gemini_dir.parent),
                    "mcpServers": mcp_servers_array
                }
        except json.JSONDecodeError as e:
            logger.debug(f"Invalid JSON in {settings_file}: {e}")
        except PermissionError as e:
            logger.debug(f"Permission denied reading {settings_file}: {e}")
        except Exception as e:
            logger.debug(f"Error reading {settings_file}: {e}")

        return None
