"""
Shared helper functions for rules extraction across all platforms.

These functions are used by both Cursor and Claude Code rules extractors
on Windows and macOS to avoid code duplication.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable

from .constants import MAX_CONFIG_FILE_SIZE, SKIP_DIRS

logger = logging.getLogger(__name__)


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


def should_skip_path(path: Path, system_dirs: Optional[set] = None) -> bool:
    """
    Check if path should be skipped during search.
    
    Skips paths containing directories like node_modules, .git, venv, etc.
    Optionally skips system directories (Windows-specific).
    
    Args:
        path: Path to check
        system_dirs: Optional set of system directory names to skip (Windows-specific)
        
    Returns:
        True if path should be skipped, False otherwise
    """
    # Skip common project directories (check all path parts for nested matches)
    if any(part in SKIP_DIRS for part in path.parts):
        return True
    
    # Skip system directories if provided (Windows-specific)
    if system_dirs and path.name in system_dirs:
        return True
    
    return False


def extract_single_rule_file(
    rule_file: Path, 
    find_project_root_func: Optional[Callable[[Path], Path]] = None
) -> Optional[Dict]:
    """
    Extract a single rule file with metadata.
    
    Args:
        rule_file: Path to the rule file
        find_project_root_func: Optional function to find project root (tool-specific).
                                If None, uses default find_project_root function.
        
    Returns:
        Dict with file info (file_path, file_name, project_root, content,
        size, last_modified, truncated) or None if extraction fails
    """
    try:
        if not rule_file.exists() or not rule_file.is_file():
            return None

        file_metadata = get_file_metadata(rule_file)
        project_root = (find_project_root_func or find_project_root)(rule_file)
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


def get_file_metadata(rule_file: Path) -> Dict[str, int]:
    """
    Get file metadata (size and last modified timestamp).
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Dict with 'size' and 'last_modified' keys
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


def find_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a rule file.
    
    Determines project root based on file location:
    - .clauderules/.cursorrules in root -> directory containing the file
    - .claude/.clauderules -> parent of .claude (2 levels up)
    - claude.md in root -> directory containing the file
    - .claude/claude.md -> parent of .claude (2 levels up)
    - .cursor/*.mdc -> parent of .cursor (2 levels up)
    - .cursor/rules/*.mdc -> parent of .cursor (3 levels up from file)
    - .windsurf/rules/* -> parent of .windsurf (2 levels up from rules)
    - ~/.windsurf/global_rules.md -> home directory
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .windsurf/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".windsurf":
        return parent.parent.parent
    
    # Case 2: Global Windsurf rules file in ~/.windsurf/global_rules.md
    if parent.name == ".windsurf" and rule_file.name == "global_rules.md":
        return parent.parent
    
    # Case 3: File is in .cursor/rules/ subdirectory
    if parent.name == "rules" and parent.parent.name == ".cursor":
        return parent.parent.parent
    
    # Case 4: File is in .claude, .cursor, or .windsurf directory
    if parent.name in (".claude", ".cursor", ".windsurf"):
        return parent.parent
    
    # Case 5: Legacy .cursorrules file (in project root)
    if rule_file.name == ".cursorrules":
        return parent
    
    # Case 6: File is directly in project root
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

