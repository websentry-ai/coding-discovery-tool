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

        Extracts both user/local scope configs from .claude.json and .claude/mcp.json
        and project-scope configs from .mcp.json files at project roots.

        Returns:
            Dict with MCP config info (projects array) or None if not found
        """
        all_projects = []

        # Scan entire filesystem from root drive
        root_drive = Path.home().anchor # Gets the root drive like "C:\"
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
        """
        projects = []
        system_dirs = self._get_system_directories()

        # Find .claude.json files
        self._find_claude_json_configs(root_path, search_dir, projects, system_dirs)

        # Find .claude/mcp.json files
        self._find_claude_dir_configs(root_path, search_dir, projects, system_dirs)

        # Find .mcp.json files
        self._find_mcp_json_configs(root_path, search_dir, projects, system_dirs)

        return projects

    def _find_claude_json_configs(
        self,
        root_path: Path,
        search_dir: Path,
        projects: List[Dict],
        system_dirs: set
    ) -> None:
        """Find and extract .claude.json config files using rglob."""
        try:
            for config_file in search_dir.rglob(".claude.json"):
                try:
                    if not self._is_valid_path(config_file, root_path, system_dirs):
                        continue

                    config_projects = self._extract_from_config_file(config_file)
                    if config_projects:
                        projects.extend(config_projects)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {config_file}: {e}")

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error scanning for .claude.json in {search_dir}: {e}")

    def _find_claude_dir_configs(
        self,
        root_path: Path,
        search_dir: Path,
        projects: List[Dict],
        system_dirs: set
    ) -> None:
        """Find and extract .claude/mcp.json config files using rglob."""
        try:
            for claude_dir in search_dir.rglob(".claude"):
                try:
                    if not claude_dir.is_dir():
                        continue

                    if not self._is_valid_path(claude_dir, root_path, system_dirs):
                        continue

                    mcp_config = claude_dir / "mcp.json"
                    if mcp_config.exists() and mcp_config.is_file():
                        config_projects = self._extract_from_config_file(mcp_config)
                        if config_projects:
                            projects.extend(config_projects)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {claude_dir}: {e}")

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error scanning for .claude directories in {search_dir}: {e}")

    def _find_mcp_json_configs(
        self,
        root_path: Path,
        search_dir: Path,
        projects: List[Dict],
        system_dirs: set
    ) -> None:
        """Find and extract .mcp.json project-scope config files using rglob."""
        try:
            for mcp_file in search_dir.rglob(".mcp.json"):
                try:
                    if not self._is_valid_path(mcp_file, root_path, system_dirs):
                        continue

                    extract_claude_project_mcp_from_file(mcp_file, projects)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {mcp_file}: {e}")

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error scanning for .mcp.json in {search_dir}: {e}")

    def _is_valid_path(
        self,
        file_path: Path,
        root_path: Path,
        system_dirs: set
    ) -> bool:
        """
        Check if a path is valid within depth limit
        """
        try:
            depth = len(file_path.relative_to(root_path).parts)
            if depth > MAX_SEARCH_DEPTH:
                return False
        except ValueError:
            # Path not relative to root
            return False

        # Check if file or any parent should be skipped using the system directories
        if should_skip_path(file_path, system_dirs):
            return False

        for parent in file_path.parents:
            if parent == root_path:
                break
            if should_skip_path(parent, system_dirs):
                return False

        return True
    
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

