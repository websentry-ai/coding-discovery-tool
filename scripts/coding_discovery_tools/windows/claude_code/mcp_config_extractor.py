"""
MCP config extraction for Claude Code on Windows systems.
"""

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...mcp_extraction_helpers import (
    extract_claude_mcp_fields,
    extract_claude_project_mcp_from_file,
    extract_managed_mcp_config,
    extract_claude_plugin_mcp_configs_with_root_support,
)
from ...windows_extraction_helpers import should_skip_path

logger = logging.getLogger(__name__)


class WindowsClaudeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Claude Code MCP config on Windows systems."""

    # Try both possible locations: ~/.claude.json (preferred) and ~/.claude/mcp.json (fallback)
    MCP_CONFIG_PATH_PREFERRED = Path.home() / ".claude.json"
    MCP_CONFIG_PATH_FALLBACK = Path.home() / ".claude" / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Claude Code MCP configuration on Windows.

        Checks multiple sources:
        1. C:\\Program Files\\ClaudeCode\\managed-mcp.json (enterprise managed)
        2. User/local scope configs from .claude.json and .claude/mcp.json
        3. Project-scope configs from .mcp.json files at project roots
        4. Plugin MCP servers from ~/.claude/plugins/*/plugin.json

        Uses parallel processing for filesystem scanning.

        Returns:
            Dict with MCP config info (projects array) or None if not found
        """
        all_projects = []

        # Extract managed MCP config (enterprise deployment - highest precedence)
        extract_managed_mcp_config(all_projects)

        # Scan filesystem for user/local/project scope configs
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [
                item for item in root_path.iterdir()
                if item.is_dir() and not should_skip_path(item, system_dirs)
            ]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(
                        self._scan_directory_for_claude_configs,
                        root_path, dir_path
                    ): dir_path
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        dir_projects = future.result()
                        all_projects.extend(dir_projects)
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")

        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to current user's home directory
            logger.info("Falling back to home directory search")
            home_path = Path.home()
            home_projects = self._scan_directory_for_claude_configs(home_path, home_path)
            all_projects.extend(home_projects)

        # Extract plugin MCP configs from ~/.claude/plugins/*/plugin.json
        extract_claude_plugin_mcp_configs_with_root_support(all_projects)

        # Return None if no configs found
        if not all_projects:
            return None
        
        return {
            "projects": all_projects
        }

    def _scan_directory_for_claude_configs(
        self,
        root_path: Path,
        search_dir: Path
    ) -> List[Dict]:
        """
        Recursively walk directory tree looking for Claude Code MCP config files.

        Uses depth-limited traversal to avoid scanning entire subtrees.
        """
        projects = []
        system_dirs = self._get_system_directories()

        self._walk_for_claude_configs(
            root_path, search_dir, projects, system_dirs, current_depth=1
        )

        return projects

    def _walk_for_claude_configs(
        self,
        root_path: Path,
        current_dir: Path,
        projects: List[Dict],
        system_dirs: set,
        current_depth: int = 0
    ) -> None:
        """
        Walk directory tree with depth limit looking for Claude Code config files.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for entry in current_dir.iterdir():
                try:
                    if should_skip_path(entry, system_dirs):
                        continue

                    if entry.is_dir():
                        # Check for .claude directory with mcp.json
                        if entry.name == ".claude":
                            mcp_config = entry / "mcp.json"
                            if mcp_config.exists() and mcp_config.is_file():
                                config_projects = self._extract_from_config_file(
                                    mcp_config
                                )
                                if config_projects:
                                    projects.extend(config_projects)
                            continue

                        self._walk_for_claude_configs(
                            root_path, entry, projects,
                            system_dirs, current_depth + 1
                        )

                    elif entry.is_file():
                        # Check for .claude.json files
                        if entry.name == ".claude.json":
                            config_projects = self._extract_from_config_file(entry)
                            if config_projects:
                                projects.extend(config_projects)

                        # Check for .mcp.json files (project-scope)
                        elif entry.name == ".mcp.json":
                            extract_claude_project_mcp_from_file(entry, projects)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {entry}: {e}")

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.
        
        Returns:
            Set of system directory names
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
    
    def _extract_from_config_file(self, config_path: Path) -> List[Dict]:
        """
        Extract MCP projects from a single config file.
        
        Args:
            config_path: Path to the config file
            
        Returns:
            List of project dicts or empty list if extraction fails
        """
        try:
            content = config_path.read_text(encoding='utf-8', errors='replace')
            
            # Parse JSON
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {config_path}: {e}")
                return []
            
            # Extract only MCP-related configuration
            projects = extract_claude_mcp_fields(config_data, config_path)
            return projects
        except PermissionError as e:
            logger.warning(f"Permission denied reading MCP config {config_path}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error reading MCP config {config_path}: {e}")
            return []

