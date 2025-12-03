"""
Shared helper functions for macOS rules extraction.

These functions are used by both Cursor and Claude Code rules extractors
on macOS to avoid code duplication.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE, MAX_SEARCH_DEPTH, SKIP_DIRS, SKIP_SYSTEM_DIRS

logger = logging.getLogger(__name__)


def is_running_as_root() -> bool:
    """
    Check if the current process is running as root user.
    
    Uses os.getuid() to check if UID is 0, which is portable across
    Unix-like systems (macOS, Linux, etc.).
    
    Returns:
        True if running as root (UID 0), False otherwise
    """
    try:
        return os.getuid() == 0
    except AttributeError:
        # Windows doesn't have os.getuid(), fallback to checking home directory
        # On Windows, this would be a different check anyway
        home = Path.home()
        return str(home) in ["/root", "/var/root"]


def add_rule_to_project(
    rule_info: Dict,
    project_root: str,
    projects_by_root: Dict[str, List[Dict]]
) -> None:
    """
    Add a rule to the appropriate project in the dictionary.
    
    Args:
        rule_info: Rule file information dict
        project_root: Project root path as string
        projects_by_root: Dictionary to update
    """
    if project_root not in projects_by_root:
        projects_by_root[project_root] = []

    # Remove project_root from rule since it's now at project level
    rule_without_root = {k: v for k, v in rule_info.items() if k != 'project_root'}
    projects_by_root[project_root].append(rule_without_root)


def build_project_list(projects_by_root: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Convert projects dictionary to list format.
    
    Args:
        projects_by_root: Dictionary mapping project_root to list of rules
        
    Returns:
        List of project dicts with project_root and rules
    """
    return [
        {
            "project_root": project_root,
            "rules": rules
        }
        for project_root, rules in projects_by_root.items()
    ]


def should_skip_path(path: Path) -> bool:
    """
    Check if path should be skipped during search.
    
    Skips paths containing directories like node_modules, .git, venv, etc.
    
    Args:
        path: Path to check
        
    Returns:
        True if path should be skipped, False otherwise
    """
    return any(part in SKIP_DIRS for part in path.parts)


def should_skip_system_path(path: Path) -> bool:
    """
    Check if path is in a system directory that should be skipped.
    
    Args:
        path: Path to check
        
    Returns:
        True if path should be skipped, False otherwise
    """
    path_str = str(path)
    return any(path_str.startswith(skip_dir) for skip_dir in SKIP_SYSTEM_DIRS)


def extract_single_rule_file(rule_file: Path, find_project_root_func) -> Optional[Dict]:
    """
    Extract a single rule file with metadata.
    
    Args:
        rule_file: Path to the rule file
        find_project_root_func: Function to find project root (tool-specific)
        
    Returns:
        Dict with file info (file_path, file_name, project_root, content,
        size, last_modified, truncated) or None if extraction fails
    """
    try:
        if not rule_file.exists() or not rule_file.is_file():
            return None

        file_metadata = get_file_metadata(rule_file)
        project_root = find_project_root_func(rule_file)
        content, truncated = read_file_content(rule_file, file_metadata['size'])

        return {
            "file_path": str(rule_file),
            "file_name": rule_file.name,
            "project_root": str(project_root) if project_root else None,
            "content": content,
            "size": file_metadata['size'],
            "last_modified": file_metadata['last_modified'],
            "truncated": truncated
        }

    except PermissionError as e:
        logger.warning(f"Permission denied reading {rule_file}: {e}")
        return None
    except UnicodeDecodeError as e:
        logger.warning(f"Unable to decode {rule_file} as text: {e}")
        return None
    except Exception as e:
        logger.warning(f"Error reading rule file {rule_file}: {e}")
        return None


def find_cursor_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Cursor rule file.
    
    Determines project root based on file location:
    - .cursor/*.mdc -> parent of .cursor
    - .cursor/rules/*.mdc -> parent of .cursor (2 levels up from rules)
    - .cursorrules -> directory containing the file
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is directly in .cursor directory
    if parent.name == ".cursor":
        return parent.parent
    
    # Case 2: File is in .cursor/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".cursor":
        return parent.parent.parent
    
    # Case 3: Legacy .cursorrules file (in project root)
    if rule_file.name == ".cursorrules":
        return parent
    
    # Fallback: use the directory containing the file
    return parent


def find_claude_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Claude Code rule file.
    
    Determines project root based on file location:
    - .clauderules in root -> directory containing the file
    - .claude/.clauderules -> parent of .claude (2 levels up)
    - claude.md in root -> directory containing the file
    - .claude/claude.md -> parent of .claude (2 levels up)
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .claude directory
    if parent.name == ".claude":
        return parent.parent
    
    # Case 2: File is directly in project root (.clauderules or claude.md)
    return parent


def find_windsurf_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Windsurf rule file.
    
    Determines project root based on file location:
    - .windsurf/rules/* -> parent of .windsurf (2 levels up from rules)
    - ~/.windsurf/global_rules.md -> home directory (special case for global rules)
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .windsurf/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".windsurf":
        return parent.parent.parent
    
    # Case 2: Global rules file in ~/.windsurf/global_rules.md
    # Use the .windsurf directory's parent (which would be home directory)
    if parent.name == ".windsurf" and rule_file.name == "global_rules.md":
        return parent.parent
    
    # Case 3: File is directly in .windsurf directory (shouldn't happen per docs, but handle it)
    if parent.name == ".windsurf":
        return parent.parent
    
    # Fallback: use the directory containing the file
    return parent


def find_gemini_cli_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Gemini CLI rule file.
    
    For Gemini CLI rules:
    - Global rules in ~/.gemini/GEMINI.md -> home directory
    - Project rules: GEMINI.md in current directory or parent -> directory containing GEMINI.md
    - Sub-directory rules: GEMINI.md in subdirectories -> directory containing GEMINI.md
    
    Args:
        rule_file: Path to the rule file (GEMINI.md)
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: Global rules in ~/.gemini/GEMINI.md
    # Return the .gemini directory's parent (which would be home directory)
    if parent.name == ".gemini" and rule_file.name.upper() == "GEMINI.MD":
        return parent.parent  # Home directory
    
    # Case 2: Project or sub-directory rules
    # For Gemini CLI, the directory containing GEMINI.md is the project root
    # (could be actual project root or a subdirectory)
    return parent


def get_file_metadata(rule_file: Path) -> Dict[str, str]:
    """
    Get file metadata (size and last modified timestamp).
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Dict with 'size' (int) and 'last_modified' (str) keys
    """
    stat = rule_file.stat()
    return {
        'size': stat.st_size,
        'last_modified': datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z"
    }


def read_file_content(rule_file: Path, file_size: int) -> Tuple[str, bool]:
    """
    Read file content, truncating if necessary.
    
    Args:
        rule_file: Path to the rule file
        file_size: Size of the file in bytes
        
    Returns:
        Tuple of (content, truncated) where truncated is True if file was truncated
    """
    if file_size > MAX_CONFIG_FILE_SIZE:
        logger.warning(
            f"Rule file {rule_file} exceeds size limit "
            f"({file_size} > {MAX_CONFIG_FILE_SIZE} bytes). Truncating."
        )
        return read_truncated_file(rule_file), True
    
    return rule_file.read_text(encoding='utf-8', errors='replace'), False


def read_truncated_file(file_path: Path) -> str:
    """
    Read file content up to max size bytes.
    
    Args:
        file_path: Path to the file
        
    Returns:
        Truncated file content as string
    """
    try:
        with open(file_path, 'rb') as f:
            content_bytes = f.read(MAX_CONFIG_FILE_SIZE)
            return content_bytes.decode('utf-8', errors='replace')
    except Exception as e:
        logger.warning(f"Error reading truncated file {file_path}: {e}")
        return ""


def should_process_directory(directory: Path, root_path: Path) -> bool:
    """
    Check if a directory should be processed.
    
    Args:
        directory: Path to directory
        root_path: Root search path
        
    Returns:
        True if directory should be processed
    """
    if should_skip_path(directory):
        return False

    try:
        depth = len(directory.relative_to(root_path).parts)
        return depth <= MAX_SEARCH_DEPTH
    except ValueError:
        return False


def should_process_file(file_path: Path, root_path: Path) -> bool:
    """
    Check if a file should be processed.
    
    Args:
        file_path: Path to file
        root_path: Root search path
        
    Returns:
        True if file should be processed
    """
    if should_skip_path(file_path):
        return False

    try:
        depth = len(file_path.relative_to(root_path).parts)
        return depth <= MAX_SEARCH_DEPTH
    except ValueError:
        return False


def get_top_level_directories(root_path: Path) -> List[Path]:
    """
    Get top-level directories from root path, skipping system directories.
    
    Args:
        root_path: Root path to iterate (typically Path("/"))
        
    Returns:
        List of top-level directory paths
    """
    top_level_dirs = []
    for item in root_path.iterdir():
        try:
            if item.is_dir() and not should_skip_system_path(item):
                top_level_dirs.append(item)
        except (PermissionError, OSError):
            continue
    return top_level_dirs


def scan_user_directories(check_func) -> Optional[Path]:
    """
    Scan /Users directory for tool installations when running as root.
    
    When running as root, this function scans all user directories in /Users
    to find tool installations. This is useful for MDM deployments where
    tools are installed per-user.
    
    Args:
        check_func: Function that takes a user_dir Path and returns a Path
                    if tool is found, or None if not found.
                    Signature: check_func(user_dir: Path) -> Optional[Path]
    
    Returns:
        Path to the first found tool installation, or None if not found
    """
    if not is_running_as_root():
        return None
    
    users_dir = Path("/Users")
    if not users_dir.exists():
        return None
    
    for user_dir in users_dir.iterdir():
        if user_dir.is_dir() and not user_dir.name.startswith('.'):
            try:
                result = check_func(user_dir)
                if result:
                    return result
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping user directory {user_dir}: {e}")
                continue
    
    return None


def extract_project_level_rules_with_fallback(
    root_path: Path,
    tool_dir_name: str,
    extract_from_dir_func,
    walk_for_dirs_func,
    projects_by_root: Dict[str, List[Dict]]
) -> None:
    """
    Shared helper for extracting project-level rules with root path handling.
    
    This function handles the common pattern of:
    1. If searching from root (/), get top-level directories and walk each
    2. If searching from non-root, use rglob to find tool directories
    3. Fallback to home directory if root access fails
    
    Args:
        root_path: Root directory to search from (Path("/") for system-wide, or home)
        tool_dir_name: Name of the tool directory to search for (e.g., ".cursor", ".windsurf")
        extract_from_dir_func: Function to extract rules from a found tool directory
                              Signature: func(tool_dir: Path, projects_by_root: Dict)
        walk_for_dirs_func: Function to recursively walk for tool directories
                           Signature: func(root_path: Path, current_dir: Path, 
                                          projects_by_root: Dict, current_depth: int)
        projects_by_root: Dictionary to populate with rules grouped by project root
    """
    # When searching from root, iterate top-level directories first to avoid system paths
    if root_path == Path("/"):
        try:
            # Get top-level directories, skipping system ones
            top_level_dirs = get_top_level_directories(root_path)
            
            # Search each top-level directory (like /Users, /opt, etc.)
            for top_dir in top_level_dirs:
                try:
                    walk_for_dirs_func(root_path, top_dir, projects_by_root, current_depth=1)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to home directory
            logger.info("Falling back to home directory search")
            home_path = Path.home()
            extract_project_level_rules_with_fallback(
                home_path, tool_dir_name, extract_from_dir_func, 
                walk_for_dirs_func, projects_by_root
            )
    else:
        # For non-root paths, use standard rglob
        for tool_dir in root_path.rglob(tool_dir_name):
            try:
                if not should_process_directory(tool_dir, root_path):
                    continue

                # Extract rules from this tool directory
                extract_from_dir_func(tool_dir, projects_by_root)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {tool_dir}: {e}")
                continue


def walk_for_tool_directories(
    root_path: Path,
    current_dir: Path,
    tool_dir_name: str,
    extract_from_dir_func,
    projects_by_root: Dict[str, List[Dict]],
    current_depth: int = 0
) -> None:
    """
    Recursively walk directory tree looking for tool-specific directories.
    
    This is a generic helper that can be used by any tool's rules extractor.
    
    Args:
        root_path: Root search path (for depth calculation)
        current_dir: Current directory being processed
        tool_dir_name: Name of the tool directory to look for (e.g., ".cursor", ".windsurf")
        extract_from_dir_func: Function to extract rules from a found tool directory
                              Signature: func(tool_dir: Path, projects_by_root: Dict)
        projects_by_root: Dictionary to populate with rules
        current_depth: Current recursion depth
    """
    # Check depth limit
    if current_depth > MAX_SEARCH_DEPTH:
        return

    try:
        for item in current_dir.iterdir():
            try:
                # Check if we should skip this path
                if should_skip_path(item) or should_skip_system_path(item):
                    continue
                
                # Check depth for this item
                try:
                    depth = len(item.relative_to(root_path).parts)
                    if depth > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue
                
                if item.is_dir():
                    # Found the tool directory!
                    if item.name == tool_dir_name:
                        # Extract rules from this tool directory
                        extract_from_dir_func(item, projects_by_root)
                        # Don't recurse into tool directory
                        continue
                    
                    # Recurse into subdirectories
                    walk_for_tool_directories(
                        root_path, item, tool_dir_name, extract_from_dir_func,
                        projects_by_root, current_depth + 1
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


def extract_project_level_mcp_configs_with_fallback(
    root_path: Path,
    tool_dir_name: str,
    global_tool_dir: Path,
    extract_from_dir_func,
    walk_for_configs_func,
    should_skip_func
) -> List[Dict]:
    """
    Shared helper for extracting project-level MCP configs with root path handling.
    
    This function handles the common pattern of:
    1. If searching from root (/), get top-level directories and walk each
    2. If searching from non-root, use rglob to find tool directories
    3. Fallback to home directory if root access fails
    
    Args:
        root_path: Root directory to search from (Path("/") for system-wide, or home)
        tool_dir_name: Name of the tool directory to search for (e.g., ".cursor", ".windsurf")
        global_tool_dir: Path to the global tool directory to skip
        extract_from_dir_func: Function to extract MCP from a found tool directory
                              Signature: func(tool_dir: Path, projects: List, global_dir: Path)
        walk_for_configs_func: Function to recursively walk for MCP configs
                             Signature: func(root_path: Path, current_dir: Path, projects: List,
                                            global_dir: Path, should_skip: Callable, depth: int)
        should_skip_func: Function to check if a path should be skipped
    
    Returns:
        List of project dicts with MCP configs
    """
    projects = []
    
    try:
        # Get top-level directories, skipping system ones
        top_level_dirs = get_top_level_directories(root_path)
        
        # Search each top-level directory
        for top_dir in top_level_dirs:
            try:
                walk_for_configs_func(
                    root_path, top_dir, projects, global_tool_dir,
                    should_skip_func, current_depth=1
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {top_dir}: {e}")
                continue
    except (PermissionError, OSError) as e:
        logger.warning(f"Error accessing root directory: {e}")
        # Fallback to home directory
        logger.info("Falling back to home directory search")
        home_path = Path.home()
        
        for tool_dir in home_path.rglob(tool_dir_name):
            try:
                if not should_process_directory(tool_dir, home_path):
                    continue
                extract_from_dir_func(tool_dir, projects, global_tool_dir)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {tool_dir}: {e}")
                continue
    
    return projects
