"""
Shared helper functions for MCP config extraction across all platforms.

These functions are used by both Cursor and Claude Code MCP extractors
on Windows and macOS to avoid code duplication.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Optional, Callable

from .constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


def transform_mcp_servers_to_array(mcp_servers: Dict) -> List[Dict]:
    """
    Transform mcpServers from object format to array format.
    
    Excludes 'env' and 'headers' fields from server configs as they're not needed in the output.
    
    Args:
        mcp_servers: Dictionary mapping server names to server configs
        
    Returns:
        List of server config objects with 'name' field added (env and headers fields excluded)
    """
    if not isinstance(mcp_servers, dict):
        return []
    
    # Fields to exclude from server configs
    excluded_fields = {"env", "headers"}
    
    servers_array = []
    for server_name, server_config in mcp_servers.items():
        if isinstance(server_config, dict):
            # Create server object excluding 'env' and 'headers' fields
            server_obj = {
                "name": server_name,
                **{field_name: field_value for field_name, field_value in server_config.items() 
                    if field_name not in excluded_fields}
            }
            servers_array.append(server_obj)
    
    return servers_array


def extract_claude_mcp_fields(config_data: Dict) -> List[Dict]:
    """
    Extract MCP-related fields from Claude Code configuration.
    
    Args:
        config_data: Full configuration dictionary
        
    Returns:
        List of project dicts with MCP configuration
    """
    projects = []
    
    # Extract MCP fields from projects
    if "projects" in config_data and isinstance(config_data["projects"], dict):
        for project_path, project_data in config_data["projects"].items():
            if not isinstance(project_data, dict):
                continue
            
            # Transform mcpServers from object to array
            mcp_servers_obj = project_data.get("mcpServers", {})
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
                
            project_mcp = {
                "path": project_path,
                "mcpServers": mcp_servers_array,
                "mcpContextUris": project_data.get("mcpContextUris", []),
                "enabledMcpjsonServers": project_data.get("enabledMcpjsonServers", []),
                "disabledMcpjsonServers": project_data.get("disabledMcpjsonServers", [])
            }
            
            projects.append(project_mcp)
    
    return projects


def extract_cursor_mcp_from_dir(
    cursor_dir: Path,
    projects: List[Dict],
    global_cursor_dir: Path
) -> None:
    """
    Extract MCP config from a .cursor directory if mcp.json exists.
    
    Args:
        cursor_dir: Path to .cursor directory
        projects: List to append project configs to
        global_cursor_dir: Path to global .cursor directory to skip
    """
    mcp_config_file = cursor_dir / "mcp.json"
    if not mcp_config_file.exists():
        return
    
    try:
        project_root = cursor_dir.parent
        
        # Skip if this is the global config directory
        if cursor_dir == global_cursor_dir:
            return
        
        content = mcp_config_file.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only add if there are MCP servers configured
        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in MCP config {mcp_config_file}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading MCP config {mcp_config_file}: {e}")
    except Exception as e:
        logger.warning(f"Error reading MCP config {mcp_config_file}: {e}")


def walk_for_cursor_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_cursor_dir: Path,
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .cursor/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_cursor_dir: Path to global .cursor directory to skip
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return
    
    try:
        for item in current_dir.iterdir():
            try:
                # Check if we should skip this path
                if should_skip_func(item):
                    continue
                
                # Check depth
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    # Path not relative to root (different drive on Windows)
                    continue
                
                if item.is_dir():
                    # Found a .cursor directory!
                    if item.name == ".cursor":
                        extract_cursor_mcp_from_dir(item, projects, global_cursor_dir)
                        # Don't recurse into .cursor directory
                        continue
                    
                    # Recurse into subdirectories
                    walk_for_cursor_mcp_configs(
                        root_path, item, projects, global_cursor_dir,
                        should_skip_func, current_depth + 1
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


def extract_windsurf_mcp_from_dir(
    windsurf_dir: Path,
    projects: List[Dict],
    global_windsurf_dir: Path
) -> None:
    """
    Extract MCP config from a .windsurf directory if mcp_config.json exists.
    
    Args:
        windsurf_dir: Path to .windsurf directory
        projects: List to append project configs to
        global_windsurf_dir: Path to global .windsurf directory to skip
    """
    mcp_config_file = windsurf_dir / "mcp_config.json"
    if not mcp_config_file.exists():
        return
    
    try:
        project_root = windsurf_dir.parent
        
        # Skip if this is the global config directory
        if windsurf_dir == global_windsurf_dir:
            return
        
        content = mcp_config_file.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only add if there are MCP servers configured
        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Windsurf MCP config {mcp_config_file}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading Windsurf MCP config {mcp_config_file}: {e}")
    except Exception as e:
        logger.warning(f"Error reading Windsurf MCP config {mcp_config_file}: {e}")


def walk_for_windsurf_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_windsurf_dir: Path,
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .windsurf/mcp_config.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_windsurf_dir: Path to global .windsurf directory to skip
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return
    
    try:
        for item in current_dir.iterdir():
            try:
                # Check if we should skip this path
                if should_skip_func(item):
                    continue
                
                # Check depth
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    # Path not relative to root (different drive on Windows)
                    continue
                
                if item.is_dir():
                    # Found a .windsurf directory!
                    if item.name == ".windsurf":
                        extract_windsurf_mcp_from_dir(item, projects, global_windsurf_dir)
                        # Don't recurse into .windsurf directory
                        continue
                    
                    # Recurse into subdirectories
                    walk_for_windsurf_mcp_configs(
                        root_path, item, projects, global_windsurf_dir,
                        should_skip_func, current_depth + 1
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


def extract_roo_mcp_from_dir(
    roo_dir: Path,
    projects: List[Dict],
    global_roo_dir: Optional[Path] = None
) -> None:
    """
    Extract MCP config from a .roo directory if mcp.json exists.
    
    Args:
        roo_dir: Path to .roo directory
        projects: List to append project configs to
        global_roo_dir: Path to global .roo directory to skip (optional)
    """
    mcp_config_file = roo_dir / "mcp.json"
    if not mcp_config_file.exists():
        return
    
    try:
        project_root = roo_dir.parent
        
        # Skip if this is the global config directory
        if global_roo_dir and roo_dir == global_roo_dir:
            return
        
        content = mcp_config_file.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only add if there are MCP servers configured
        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Roo MCP config {mcp_config_file}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading Roo MCP config {mcp_config_file}: {e}")
    except Exception as e:
        logger.warning(f"Error reading Roo MCP config {mcp_config_file}: {e}")


def walk_for_roo_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_roo_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .roo/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_roo_dir: Path to global .roo directory to skip (optional)
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return
    
    try:
        for item in current_dir.iterdir():
            try:
                # Check if we should skip this path
                if should_skip_func(item):
                    continue
                
                # Check depth
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    # Path not relative to root (different drive on Windows)
                    continue
                
                if item.is_dir():
                    # Found a .roo directory!
                    if item.name == ".roo":
                        extract_roo_mcp_from_dir(item, projects, global_roo_dir)
                        # Don't recurse into .roo directory
                        continue
                    
                    # Recurse into subdirectories
                    walk_for_roo_mcp_configs(
                        root_path, item, projects, global_roo_dir,
                        should_skip_func, current_depth + 1
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

