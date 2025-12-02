"""
Kilo Code rules extraction for Windows systems.

Extracts Kilo Code configuration files from .kilocode/rules directories and
global rules directory on the user's machine, grouping them by project root.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseKiloCodeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

logger = logging.getLogger(__name__)


def find_kilocode_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Kilo Code rule file.
    
    For Kilo Code rules:
    - Files in .kilocode/rules/ directory -> parent of .kilocode (project root)
    - Global rules in ~/.kilocode/rules/ -> home directory
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .kilocode/rules directory
    if parent.name == "rules" and parent.parent.name == ".kilocode":
        project_root = parent.parent.parent
        # Case 2: Global rules (in ~/.kilocode/rules/)
        if project_root == Path.home():
            return Path.home()
        # Case 3: Workspace rules (in project/.kilocode/rules/)
        return project_root
    
    # Default: return parent directory
    return parent


class WindowsKiloCodeRulesExtractor(BaseKiloCodeRulesExtractor):
    """Extractor for Kilo Code rules on Windows systems."""

    def extract_all_kilocode_rules(self) -> List[Dict]:
        """
        Extract all Kilo Code rules from all projects on Windows.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Extract project-level rules from root drive (for MDM deployment)
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        logger.info(f"Searching for Kilo Code rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Kilo Code rules from ~/.kilocode/rules/.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        user_home = Path.home()
        global_rules_path = user_home / ".kilocode" / "rules"
        
        if global_rules_path.exists() and global_rules_path.is_dir():
            try:
                # Extract all .md files from global rules directory
                for rule_file in global_rules_path.glob("*.md"):
                    if rule_file.is_file():
                        # Use custom find_project_root function for Kilo Code
                        rule_info = self._extract_single_rule_file_with_root(rule_file)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Kilo Code rules: {e}")

    def _extract_single_rule_file_with_root(self, rule_file: Path) -> Dict:
        """
        Extract a single rule file with metadata using Kilo Code-specific project root finder.
        
        Args:
            rule_file: Path to the rule file
            
        Returns:
            Dict with file info or None if extraction fails
        """
        try:
            if not rule_file.exists() or not rule_file.is_file():
                return None

            from ...windows_extraction_helpers import get_file_metadata, read_file_content
            file_metadata = get_file_metadata(rule_file)
            project_root = find_kilocode_project_root(rule_file)
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

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects using optimized walker.
        
        Uses parallel processing for top-level directories to improve performance.
        
        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Process top-level directories in parallel for better performance
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Use parallel processing for top-level directories
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_kilocode_directories, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()  # Raises exception if any occurred
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_kilocode_directories(root_path, root_path, projects_by_root, current_depth=0)
    
    def _walk_for_kilocode_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .kilocode directories.
        
        This optimized walker:
        - Skips ignored directories early (before recursing)
        - Checks depth limits to avoid searching too deep
        - Stops recursing into project subdirectories once a project is found
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
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
                    system_dirs = self._get_system_directories()
                    if should_skip_path(item, system_dirs):
                        continue
                    
                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        # Path not relative to root (different drive on Windows)
                        continue
                    
                    if item.is_dir():
                        # Found a .kilocode directory!
                        if item.name == ".kilocode":
                            # Extract rules from this .kilocode directory
                            self._extract_rules_from_kilocode_directory(item, projects_by_root)
                            # Don't recurse into .kilocode directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_kilocode_directories(root_path, item, projects_by_root, current_depth + 1)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_kilocode_directory(
        self, kilocode_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .kilocode directory.
        
        Args:
            kilocode_dir: Path to .kilocode directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract all .md files from .kilocode/rules/ subdirectory
        rules_dir = kilocode_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for rule_file in rules_dir.glob("*.md"):
                if rule_file.is_file():
                    rule_info = self._extract_single_rule_file_with_root(rule_file)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

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

