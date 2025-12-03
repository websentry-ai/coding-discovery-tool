"""
OpenCode rules extraction for Windows systems.

Extracts OpenCode configuration files from:
- Global rules: %APPDATA%\.config\opencode\agent\*.md (AppData\Roaming\.config\opencode\agent\*.md)
- Project-level rules: **\.opencode\agent\*.md (recursive)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseOpenCodeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

logger = logging.getLogger(__name__)


def find_opencode_project_root(rule_file: Path) -> Path:
    """
    Find the project root for an OpenCode rule file.
    
    For global rules: AppData\Roaming\.config\opencode\agent\*.md -> home directory
    For project rules: <project>\.opencode\agent\*.md -> <project>
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Path to the project root
    """
    parent = rule_file.parent
    
    # Check if it's a global rule (in .config/opencode/agent/)
    # Path structure: <home>\AppData\Roaming\.config\opencode\agent\*.md
    if parent.name == "agent":
        parent2 = parent.parent
        if parent2.name == "opencode" and parent2.parent.name == ".config":
            # This is a global rule - go up to home directory
            # agent -> opencode -> .config -> Roaming -> AppData -> home
            return parent2.parent.parent.parent.parent
    
    # Project-level rule: go up 2 levels: agent -> .opencode -> project
    if parent.name == "agent" and parent.parent.name == ".opencode":
        return parent.parent.parent
    
    # Fallback: return parent directory
    return parent


class WindowsOpenCodeRulesExtractor(BaseOpenCodeRulesExtractor):
    """Extractor for OpenCode rules on Windows systems."""

    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects on Windows.
        
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
        
        logger.info(f"Searching for OpenCode rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global OpenCode rules from AppData\Roaming\.config\opencode\agent\*.md.
        
        When running as administrator, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_rules_dir = user_home / "AppData" / "Roaming" / ".config" / "opencode" / "agent"
            
            if global_rules_dir.exists() and global_rules_dir.is_dir():
                try:
                    # Check if directory should be processed
                    if not should_skip_path(global_rules_dir):
                        # Find all .md files in the agent directory
                        for rule_file in global_rules_dir.glob("*.md"):
                            rule_info = extract_single_rule_file(
                                rule_file,
                                find_opencode_project_root
                            )
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global OpenCode rules for {user_home}: {e}")
        
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
        
        Searches for .opencode/agent/*.md files in all projects.
        
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
                    executor.submit(self._walk_for_opencode_dirs, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_opencode_dirs(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_opencode_dirs(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .opencode directories.
        
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
                    
                    if item.is_dir():
                        # Found a .opencode directory!
                        if item.name == ".opencode":
                            # Extract rules from this .opencode directory
                            self._extract_rules_from_opencode_directory(item, projects_by_root)
                            # Don't recurse into .opencode directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_opencode_dirs(root_path, item, projects_by_root, current_depth + 1)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_opencode_directory(self, opencode_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .opencode directory.
        
        Args:
            opencode_dir: Path to .opencode directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        agent_dir = opencode_dir / "agent"
        
        if not agent_dir.exists() or not agent_dir.is_dir():
            return
        
        try:
            # Find all .md files in the agent directory
            for rule_file in agent_dir.glob("*.md"):
                if not should_skip_path(rule_file):
                    rule_info = extract_single_rule_file(
                        rule_file,
                        find_opencode_project_root
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {opencode_dir}: {e}")

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

