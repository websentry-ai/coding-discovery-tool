"""
MCP config extraction for Gemini CLI on Windows systems.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_global_mcp_config_with_root_support,
    transform_mcp_servers_to_array,
)
from ...windows_extraction_helpers import should_skip_path
from ...constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


class WindowsGeminiCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Gemini CLI MCP config on Windows systems."""

    GLOBAL_MCP_CONFIG_PATH = Path.home() / ".gemini" / "settings.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Gemini CLI MCP configuration on Windows.

        Extracts both global and project-level MCP configs:
        - Global: ~/.gemini/settings.json
        - Project: <project>/.gemini/settings.json

        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []

        global_config = self._extract_global_config()
        if global_config:
            projects.append(global_config)

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
        
        When running as administrator, collects global configs from ALL users.
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
        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir()
                            if item.is_dir() and not should_skip_path(item, system_dirs)]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_gemini_configs, root_path, dir_path, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        result = future.result()
                        if result:
                            configs.extend(result)
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            home_path = Path.home()
            for gemini_dir in home_path.rglob(".gemini"):
                try:
                    if gemini_dir.parent == home_path:
                        continue
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
        current_depth: int = 0
    ) -> List[Dict]:
        """
        Recursively walk directory tree looking for .gemini directories.
        """
        configs = []

        if current_depth > MAX_SEARCH_DEPTH:
            return configs

        try:
            for item in current_dir.iterdir():
                try:
                    system_dirs = self._get_system_directories()
                    if should_skip_path(item, system_dirs):
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

                        sub_configs = self._walk_for_gemini_configs(root_path, item, current_depth + 1)
                        configs.extend(sub_configs)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

        return configs

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

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
