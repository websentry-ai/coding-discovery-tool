"""
Codex rules extraction for Windows systems.

Extracts Codex rules from AGENTS.md files:
- Global rules: ~/.codex/AGENTS.md
- Project-level rules: AGENTS.md or AGENTS.override.md in project directories
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCodexRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

logger = logging.getLogger(__name__)

# Codex rule file names
AGENTS_MD = "AGENTS.md"
AGENTS_OVERRIDE_MD = "AGENTS.override.md"


def find_codex_project_root(agents_file: Path) -> Path:
    """
    Find the project root for a Codex AGENTS.md file.
    
    For Codex:
    - Global rules in ~/.codex/AGENTS.md -> home directory
    - Project-level AGENTS.md -> directory containing the file (project root)
    
    Args:
        agents_file: Path to the AGENTS.md file
        
    Returns:
        Path to the project root
    """
    parent = agents_file.parent
    
    # Case 1: Global rules in ~/.codex/AGENTS.md
    if parent.name == ".codex":
        return parent.parent  # Home directory
    
    # Case 2: Project-level rules - the directory containing AGENTS.md is the project root
    return parent


class WindowsCodexRulesExtractor(BaseCodexRulesExtractor):
    """Extractor for Codex rules on Windows systems."""

    def extract_all_codex_rules(self) -> List[Dict]:
        """
        Extract all Codex rules from all projects on Windows.
        
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
        
        logger.info(f"Searching for Codex rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Codex rules from ~/.codex/AGENTS.md.
        
        When running as administrator, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_agents_path = user_home / ".codex" / AGENTS_MD
            
            if global_agents_path.exists() and global_agents_path.is_file():
                try:
                    # Check if file should be processed (not in skip directories)
                    if not should_skip_path(global_agents_path):
                        rule_info = extract_single_rule_file(
                            global_agents_path, 
                            find_codex_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Codex rules for {user_home}: {e}")
        
        # When running as administrator, scan all user directories
        if self._is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Searches for AGENTS.md and AGENTS.override.md files in:
        - Project directories
        - Subdirectories
        
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
                    executor.submit(self._walk_for_agents_files, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_agents_files(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_agents_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for AGENTS.md files.
        
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
                        continue
                    
                    if item.is_file():
                        # Look for AGENTS.md or AGENTS.override.md
                        if item.name == AGENTS_MD or item.name == AGENTS_OVERRIDE_MD:
                            # Skip if in .codex directory (we handle global rules separately)
                            if item.parent.name == ".codex":
                                continue
                            
                            # Extract this AGENTS.md file
                            self._extract_agents_file(item, projects_by_root)
                    
                    elif item.is_dir():
                        # Recurse into subdirectories
                        self._walk_for_agents_files(root_path, item, projects_by_root, current_depth + 1)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_agents_file(self, agents_file: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract a single AGENTS.md file if it should be processed.
        
        Args:
            agents_file: Path to the AGENTS.md file
            projects_by_root: Dictionary to populate with rules
        """
        try:
            rule_info = extract_single_rule_file(agents_file, find_codex_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting AGENTS.md from {agents_file}: {e}")

    def _is_running_as_admin(self) -> bool:
        """
        Check if the current process is running as administrator.
        
        Returns:
            True if running as administrator, False otherwise
        """
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                return current_user in ["administrator", "system"]
            except Exception:
                return False

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
