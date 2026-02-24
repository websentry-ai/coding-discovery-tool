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


def extract_mcp_from_dir_generic(
    tool_dir: Path,
    projects: List[Dict],
    config_filename: str,
    tool_name: str,
    global_tool_dir: Optional[Path] = None
) -> None:
    """
    Generic function to extract MCP config from a tool directory.
    
    This replaces all tool-specific extract_*_mcp_from_dir functions.
    
    Args:
        tool_dir: Path to the tool directory (e.g., .cursor, .windsurf, .roo, .kilocode)
        projects: List to append project configs to
        config_filename: Name of the MCP config file (e.g., "mcp.json", "mcp_config.json")
        tool_name: Name of the tool (for logging)
        global_tool_dir: Path to global tool directory to skip (optional)
    """
    mcp_config_file = tool_dir / config_filename
    if not mcp_config_file.exists():
        return
    
    try:
        project_root = tool_dir.parent
        
        # Skip if this is the global config directory
        if global_tool_dir and tool_dir == global_tool_dir:
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
        logger.warning(f"Invalid JSON in {tool_name} MCP config {mcp_config_file}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading {tool_name} MCP config {mcp_config_file}: {e}")
    except Exception as e:
        logger.warning(f"Error reading {tool_name} MCP config {mcp_config_file}: {e}")


def walk_for_mcp_configs_generic(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    tool_dir_name: str,
    config_filename: str,
    tool_name: str,
    global_tool_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Generic function to recursively walk directory tree looking for tool MCP config files.
    
    This replaces all tool-specific walk_for_*_mcp_configs functions.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        tool_dir_name: Name of the tool directory to look for (e.g., ".cursor", ".windsurf")
        config_filename: Name of the MCP config file (e.g., "mcp.json", "mcp_config.json")
        tool_name: Name of the tool (for logging)
        global_tool_dir: Path to global tool directory to skip (optional)
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
                    # Found the tool directory!
                    if item.name == tool_dir_name:
                        extract_mcp_from_dir_generic(
                            item, projects, config_filename, tool_name, global_tool_dir
                        )
                        # Don't recurse into tool directory
                        continue
                    
                    # Recurse into subdirectories
                    walk_for_mcp_configs_generic(
                        root_path, item, projects, tool_dir_name, config_filename,
                        tool_name, global_tool_dir, should_skip_func, current_depth + 1
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


def extract_claude_mcp_fields(config_data: Dict, config_path: Path) -> List[Dict]:
    """
    Extract MCP-related fields from Claude Code configuration.
    
    Args:
        config_data: Full configuration dictionary
        
    Returns:
        List of project dicts with MCP configuration
    """
    projects = []

    # Extract user-level (global) mcpServers from root of config
    if "mcpServers" in config_data and isinstance(config_data["mcpServers"], dict):
        user_mcp_servers_obj = config_data["mcpServers"]
        user_mcp_servers_array = transform_mcp_servers_to_array(user_mcp_servers_obj)

        if user_mcp_servers_array:
            projects.append({
                "path": str(config_path),
                "mcpServers": user_mcp_servers_array,
                "scope": "user"
            })

    # Extract project-level mcpServers from projects
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
                "disabledMcpjsonServers": project_data.get("disabledMcpjsonServers", []),
                "scope": "project"
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
    extract_mcp_from_dir_generic(
        cursor_dir, projects, "mcp.json", "Cursor", global_cursor_dir
    )


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
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".cursor", "mcp.json",
        "Cursor", global_cursor_dir, should_skip_func, current_depth
    )


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
    extract_mcp_from_dir_generic(
        windsurf_dir, projects, "mcp_config.json", "Windsurf", global_windsurf_dir
    )


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
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".windsurf", "mcp_config.json",
        "Windsurf", global_windsurf_dir, should_skip_func, current_depth
    )


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
    extract_mcp_from_dir_generic(
        roo_dir, projects, "mcp.json", "Roo Code", global_roo_dir
    )


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
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".roo", "mcp.json",
        "Roo Code", global_roo_dir, should_skip_func, current_depth
    )


def extract_kilocode_mcp_from_dir(
    kilocode_dir: Path,
    projects: List[Dict],
    global_kilocode_dir: Optional[Path] = None
) -> None:
    """
    Extract MCP config from a .kilocode directory if mcp.json exists.
    
    Args:
        kilocode_dir: Path to .kilocode directory
        projects: List to append project configs to
        global_kilocode_dir: Path to global .kilocode directory to skip (optional)
    """
    extract_mcp_from_dir_generic(
        kilocode_dir, projects, "mcp.json", "Kilo Code", global_kilocode_dir
    )


def walk_for_kilocode_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    global_kilocode_dir: Optional[Path],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for .kilocode/mcp.json files.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        projects: List to append project configs to
        global_kilocode_dir: Path to global .kilocode directory to skip (optional)
        should_skip_func: Function to check if a path should be skipped
        current_depth: Current recursion depth
    """
    walk_for_mcp_configs_generic(
        root_path, current_dir, projects, ".kilocode", "mcp.json",
        "Kilo Code", global_kilocode_dir, should_skip_func, current_depth
    )


def read_global_mcp_config(
    config_path: Path,
    tool_name: str = "MCP",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Read and parse a global MCP config file.
    
    This is a shared helper for reading global MCP configs that follow the standard pattern:
    - Read JSON file
    - Extract mcpServers object
    - Transform to array
    - Return dict with path and mcpServers
    
    Args:
        config_path: Path to the MCP config JSON file
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path (default: 2)
                      For ~/.cursor/mcp.json -> 2 levels up = ~
                      For ~/.gemini/antigravity/mcp_config.json -> 3 levels up = ~
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only return if there are MCP servers configured
        if mcp_servers_array:
            # Calculate the global config path by going up parent_levels
            global_config_path = config_path
            for _ in range(parent_levels):
                global_config_path = global_config_path.parent
            return {
                "path": str(global_config_path),
                "mcpServers": mcp_servers_array
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in global {tool_name} MCP config {config_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_global_mcp_config_with_root_support(
    global_config_path: Path,
    tool_name: str = "MCP",
    parent_levels: int = 2
) -> Optional[Dict]:
    """
    Extract global MCP config with support for root/admin user (checks all users).
    
    When running as root/admin, this function checks all user directories
    and returns the first non-empty config found.
    
    Args:
        global_config_path: Path to the global MCP config file (relative to home)
        tool_name: Name of the tool (for logging)
        parent_levels: Number of parent directories to go up for the path
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no config found
    """
    import platform
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, prioritize checking user directories first
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                # Build user-specific config path
                # global_config_path is like ~/.cursor/mcp.json
                # We need to replace ~ with user_dir
                try:
                    user_config_path = user_dir / global_config_path.relative_to(Path.home())
                    if user_config_path.exists():
                        config = read_global_mcp_config(user_config_path, tool_name, parent_levels)
                        if config:
                            return config
                except (ValueError, OSError):
                    # Path might not be relative to home, try direct construction
                    continue
        
        # Fallback to admin's own global config if no user config found
        if global_config_path.exists():
            return read_global_mcp_config(global_config_path, tool_name, parent_levels)
    else:
        # For regular users, check their own home directory
        if global_config_path.exists():
            return read_global_mcp_config(global_config_path, tool_name, parent_levels)
    
    return None


def extract_ide_global_configs_with_root_support(
    extract_configs_for_user_func,
    tool_name: str = "MCP"
) -> List[Dict]:
    """
    Extract global MCP configs from IDE global storage with support for root user.
    
    This helper is for tools like Cline and Roo Code that store configs in IDE global storage
    (multiple configs per user, one per IDE).
    
    When running as root/admin, this function checks all user directories
    and returns all configs found.
    
    Args:
        extract_configs_for_user_func: Function that extracts configs for a specific user
                                      Signature: func(user_home: Path) -> List[Dict]
        tool_name: Name of the tool (for logging)
    
    Returns:
        List of config dicts with 'path' and 'mcpServers' keys
    """
    import platform
    
    all_configs = []
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, check all users
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    user_configs = extract_configs_for_user_func(user_dir)
                    all_configs.extend(user_configs)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir} for {tool_name}: {e}")
                    continue
        
        # Also check root/admin's own configs
        try:
            root_configs = extract_configs_for_user_func(Path.home())
            all_configs.extend(root_configs)
        except Exception as e:
            logger.debug(f"Error extracting root configs for {tool_name}: {e}")
    else:
        # For regular users, check their own home directory
        try:
            user_configs = extract_configs_for_user_func(Path.home())
            all_configs.extend(user_configs)
        except Exception as e:
            logger.debug(f"Error extracting user configs for {tool_name}: {e}")
    
    return all_configs


def read_ide_global_mcp_config(
    config_path: Path,
    tool_name: str = "MCP",
    use_full_path: bool = True
) -> Optional[Dict]:
    """
    Read and parse a global MCP config file from IDE global storage.
    
    Args:
        config_path: Path to the MCP config JSON file
        tool_name: Name of the tool (for logging)
        use_full_path: If True, use the full config_path as the path in result.
                      If False, use parent directory
    
    Returns:
        Dict with 'path' and 'mcpServers' keys, or None if no servers found
    """
    try:
        content = config_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)
        
        mcp_servers_obj = config_data.get("mcpServers", {})
        
        # Transform mcpServers from object to array
        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
        
        # Only return if there are MCP servers configured
        if mcp_servers_array:
            return {
                "path": str(config_path) if use_full_path else str(config_path.parent),
                "mcpServers": mcp_servers_array
            }
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in global {tool_name} MCP config {config_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading global {tool_name} MCP config {config_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading global {tool_name} MCP config {config_path}: {e}")
    
    return None


def extract_project_level_mcp_configs_with_fallback_windows(
    root_path: Path,
    tool_dir_name: str,
    global_tool_dir: Optional[Path],
    extract_from_dir_func,
    walk_for_configs_func: Callable,
    should_skip_func: Callable[[Path], bool]
) -> List[Dict]:
    """
    Windows-specific helper for extracting project-level MCP configs with root path handling.
    
    This function handles the common pattern for Windows:
    1. If searching from root drive (C:\), get top-level directories and walk each
    2. If searching from non-root, use rglob to find tool directories
    3. Fallback to home directory if root access fails
    
    Uses Windows-specific system directory skipping.
    
    Args:
        root_path: Root directory to search from (root drive for MDM)
        tool_dir_name: Name of the tool directory to search for (e.g., ".cursor", ".windsurf", ".roo")
        global_tool_dir: Path to the global tool directory to skip
        extract_from_dir_func: Function to extract MCP from a found tool directory
                              Signature: func(tool_dir: Path, projects: List, global_dir: Path)
        walk_for_configs_func: Function to recursively walk for MCP configs
                             Signature: func(root_path: Path, current_dir: Path, projects: List,
                                            global_dir: Path, should_skip: Callable, depth: int)
        should_skip_func: Function to check if a path should be skipped (Windows-specific)
    
    Returns:
        List of project dicts with MCP configs
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    projects = []
    
    try:
        # Get top-level directories, skipping system ones using Windows-specific logic
        top_level_dirs = [
            item for item in root_path.iterdir()
            if item.is_dir() and not should_skip_func(item)
        ]
        
        # Use parallel processing for top-level directories
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {
                executor.submit(
                    walk_for_configs_func,
                    root_path, dir_path, projects, global_tool_dir,
                    should_skip_func, current_depth=1
                )
                for dir_path in top_level_dirs
            }
            
            for future in as_completed(futures):
                try:
                    future.result()  # Raises exception if any occurred
                except Exception as e:
                    logger.debug(f"Error in parallel processing: {e}")
    except (PermissionError, OSError) as e:
        logger.warning(f"Error accessing root directory: {e}")
        # Fallback to home directory
        logger.info("Falling back to home directory search")
        home_path = Path.home()
        
        for tool_dir in home_path.rglob(tool_dir_name):
            try:
                if should_skip_func(tool_dir):
                    continue
                extract_from_dir_func(tool_dir, projects, global_tool_dir)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {tool_dir}: {e}")
                continue
    
    return projects


def extract_claude_project_mcp_from_file(
    mcp_json_path: Path,
    projects: List[Dict]
) -> None:
    """
    Extract MCP config from a project-scope .mcp.json file.
    """
    if not mcp_json_path.exists() or not mcp_json_path.is_file():
        return

    try:
        project_root = mcp_json_path.parent

        content = mcp_json_path.read_text(encoding='utf-8', errors='replace')
        config_data = json.loads(content)

        mcp_servers_obj = config_data.get("mcpServers", {})

        mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

        if mcp_servers_array:
            projects.append({
                "path": str(project_root),
                "mcpServers": mcp_servers_array,
                "scope": "project"
            })
    except json.JSONDecodeError as e:
        logger.warning(f"Invalid JSON in Claude Code project MCP config {mcp_json_path}: {e}")
    except PermissionError as e:
        logger.warning(f"Permission denied reading Claude Code project MCP config {mcp_json_path}: {e}")
    except Exception as e:
        logger.warning(f"Error reading Claude Code project MCP config {mcp_json_path}: {e}")


def walk_for_claude_project_mcp_configs(
    root_path: Path,
    current_dir: Path,
    projects: List[Dict],
    should_skip_func: Callable[[Path], bool],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for Claude Code project-scope .mcp.json files.
    This looks for .mcp.json files directly at project roots.
    """
    if current_depth > MAX_SEARCH_DEPTH:
        return

    try:
        for item in current_dir.iterdir():
            try:
                if should_skip_func(item):
                    continue

                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue

                if item.is_file() and item.name == ".mcp.json":
                    extract_claude_project_mcp_from_file(item, projects)
                elif item.is_dir():
                    walk_for_claude_project_mcp_configs(
                        root_path, item, projects,
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


def extract_dual_path_configs_with_root_support(
    preferred_path: Path,
    fallback_path: Path,
    extract_from_file_func,
    tool_name: str = "MCP"
) -> List[Dict]:
    """
    Extract configs from dual paths (preferred + fallback) with root user support.
    
    This helper is for tools like Claude Code that have two possible config locations.
    It tries the preferred path first, then falls back to the fallback path.
    
    When running as root/admin, checks all user directories.
    
    Args:
        preferred_path: Preferred config file path (relative to home)
        fallback_path: Fallback config file path (relative to home)
        extract_from_file_func: Function to extract configs from a file
                               Signature: func(config_path: Path) -> List[Dict]
        tool_name: Name of the tool (for logging)
    
    Returns:
        List of config dicts
    """
    import platform
    
    all_projects = []
    
    # Check if running as admin/root
    is_admin = False
    users_dir = None
    
    if platform.system() == "Darwin":
        try:
            from .macos_extraction_helpers import is_running_as_root
            is_admin = is_running_as_root()
            users_dir = Path("/Users")
        except ImportError:
            pass
    elif platform.system() == "Windows":
        try:
            import ctypes
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            users_dir = Path("C:\\Users")
        except Exception:
            try:
                import getpass
                current_user = getpass.getuser().lower()
                is_admin = current_user in ["administrator", "system"]
                users_dir = Path("C:\\Users")
            except Exception:
                pass
    
    # When running as admin/root, check all users
    if is_admin and users_dir and users_dir.exists():
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                # Try preferred location for this user
                try:
                    user_preferred = user_dir / preferred_path.relative_to(Path.home())
                    if user_preferred.exists():
                        user_projects = extract_from_file_func(user_preferred)
                        if user_projects:
                            all_projects.extend(user_projects)
                            continue
                except (ValueError, OSError):
                    pass
                
                # Try fallback location for this user
                try:
                    user_fallback = user_dir / fallback_path.relative_to(Path.home())
                    if user_fallback.exists():
                        user_projects = extract_from_file_func(user_fallback)
                        if user_projects:
                            all_projects.extend(user_projects)
                except (ValueError, OSError):
                    pass
        
        # Also check root/admin's configs
        if preferred_path.exists():
            root_projects = extract_from_file_func(preferred_path)
            if root_projects:
                all_projects.extend(root_projects)
        elif fallback_path.exists():
            root_projects = extract_from_file_func(fallback_path)
            if root_projects:
                all_projects.extend(root_projects)
    else:
        # For regular users, check their own home directory
        if preferred_path.exists():
            user_projects = extract_from_file_func(preferred_path)
            if user_projects:
                all_projects.extend(user_projects)
        elif fallback_path.exists():
            user_projects = extract_from_file_func(fallback_path)
            if user_projects:
                all_projects.extend(user_projects)
    
    return all_projects

