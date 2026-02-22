"""
MCP config extraction for Claude Code on Windows systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import extract_claude_mcp_fields
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
        
        Scans entire filesystem from root drive to find all .claude.json and .claude/mcp.json files.
        
        Extracts only MCP-related fields (mcpServers, mcpContextUris, 
        enabledMcpjsonServers, disabledMcpjsonServers) from the config file.
        
        Returns:
            Dict with MCP config info (projects array) or None if not found
        """
        all_projects = []
        
        # Scan entire filesystem from root drive
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Scan each top-level directory
            for top_dir in top_level_dirs:
                try:
                    self._walk_for_claude_mcp_configs(root_path, top_dir, all_projects, current_depth=1)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to current user's home directory
            logger.info("Falling back to home directory search")
            home_path = Path.home()
            self._walk_for_claude_mcp_configs(home_path, home_path, all_projects, current_depth=0)
        
        # Return None if no configs found
        if not all_projects:
            return None
        
        return {
            "projects": all_projects
        }
    
    def _walk_for_claude_mcp_configs(
        self,
        root_path: Path,
        current_dir: Path,
        projects: List[Dict],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for Claude Code MCP config files.
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects: List to append found configs to
            current_depth: Current recursion depth
        """
        from ...constants import MAX_SEARCH_DEPTH
        
        # Check depth limit
        if current_depth > MAX_SEARCH_DEPTH:
            return
        
        try:
            for item in current_dir.iterdir():
                try:
                    # Check if we should skip this path
                    if should_skip_path(item, self._get_system_directories()):
                        continue
                    
                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue
                    
                    if item.is_dir():
                        # Found a .claude directory - check for mcp.json
                        if item.name == ".claude":
                            mcp_config = item / "mcp.json"
                            if mcp_config.exists() and mcp_config.is_file():
                                config_projects = self._extract_from_config_file(mcp_config)
                                if config_projects:
                                    projects.extend(config_projects)
                            # Don't recurse into .claude directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_claude_mcp_configs(root_path, item, projects, current_depth + 1)
                    elif item.is_file():
                        # Check for .claude.json files
                        if item.name == ".claude.json":
                            config_projects = self._extract_from_config_file(item)
                            if config_projects:
                                projects.extend(config_projects)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
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
            stat = config_path.stat()
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

