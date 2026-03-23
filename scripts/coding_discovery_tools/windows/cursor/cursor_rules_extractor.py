"""
Cursor rules extraction for Windows systems.

Extracts Cursor IDE configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.

Supports two scopes:
- user: Global user rules in %USERPROFILE%\\.cursor\\rules\\*.mdc
- project: Project-specific rules in .cursor\\*.mdc, .cursor\\rules\\*.mdc, .cursorrules
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...cursor_rules_helpers import is_cursor_rule_md_file, is_agents_md_file
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_project_root,
    should_skip_path,
    is_running_as_admin,
)

logger = logging.getLogger(__name__)


class WindowsCursorRulesExtractor(BaseCursorRulesExtractor):
    """Extractor for Cursor rules on Windows systems."""

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on Windows.

        Extracts rules from two scopes:
        - user: Global user rules in %USERPROFILE%\\.cursor\\rules\\*.mdc (scope="user")
        - project: Project-specific rules (scope="project")

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (with scope field)
        """
        projects_by_root = {}

        # Extract user-level rules from ~/.cursor/
        logger.info("Extracting user-level Cursor rules...")
        self._extract_user_level_rules(projects_by_root)

        # Extract project-level rules from root drive (for MDM deployment)
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)

        logger.info(f"Searching for Cursor rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_user_level_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract user-level Cursor rules from %USERPROFILE%\\.cursor\\.

        When running as admin, scans all user directories.

        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract user-level rules for a specific user."""
            user_cursor_dir = user_home / ".cursor"

            if not user_cursor_dir.exists() or not user_cursor_dir.is_dir():
                return

            # Extract .mdc files from ~/.cursor/
            for mdc_file in user_cursor_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="user")
                if rule_info:
                    project_root = str(user_home)  # User home is the "project root" for user rules
                    add_rule_to_project(rule_info, project_root, projects_by_root)

            for md_file in user_cursor_dir.glob("*.md"):
                if is_cursor_rule_md_file(md_file.name):
                    rule_info = extract_single_rule_file(md_file, find_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract from ~/.cursor/rules/
            rules_dir = user_cursor_dir / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for mdc_file in rules_dir.glob("*.mdc"):
                    rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

                for md_file in rules_dir.glob("*.md"):
                    if is_cursor_rule_md_file(md_file.name):
                        rule_info = extract_single_rule_file(md_file, find_project_root, scope="user")
                        if rule_info:
                            project_root = str(user_home)
                            add_rule_to_project(rule_info, project_root, projects_by_root)

        # When running as admin, scan all user directories
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        # Skip system user directories
                        if user_dir.name.lower() in ['public', 'default', 'default user', 'all users']:
                            continue
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            extract_for_user(Path.home())

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
                    executor.submit(self._walk_for_cursor_directories, root_path, dir_path, projects_by_root, 1, system_dirs)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()  # Raises exception if any occurred
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_cursor_directories(root_path, root_path, projects_by_root, current_depth=0)
    
    def _walk_for_cursor_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
        system_dirs: set = None
    ) -> None:
        """
        Recursively walk directory tree looking for .cursor directories.

        This optimized walker:
        - Skips ignored directories early (before recursing)
        - Checks depth limits to avoid searching too deep
        - Stops recursing into project subdirectories once a project is found

        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects_by_root: Dictionary to populate with rules
            current_depth: Current recursion depth
            system_dirs: Cached set of system directory names to skip
        """
        # Check depth limit
        if current_depth > MAX_SEARCH_DEPTH:
            return

        if system_dirs is None:
            system_dirs = self._get_system_directories()

        try:
            for item in current_dir.iterdir():
                try:
                    # Check if we should skip this path
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
                        # Found a .cursor directory!
                        if item.name == ".cursor":
                            # Extract rules from this .cursor directory
                            self._extract_rules_from_cursor_directory(item, projects_by_root)
                            # Don't recurse into .cursor directory
                            continue

                        if item.is_symlink():
                            continue

                        # Recurse into subdirectories
                        self._walk_for_cursor_directories(root_path, item, projects_by_root, current_depth + 1, system_dirs)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .cursor directory (project scope).

        Args:
            cursor_dir: Path to .cursor directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract .mdc files directly from .cursor directory (project scope)
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        for md_file in cursor_dir.glob("*.md"):
            if is_cursor_rule_md_file(md_file.name):
                rule_info = extract_single_rule_file(md_file, find_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        # Also check .cursor/rules/ subdirectory (if it exists)
        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            for md_file in rules_dir.glob("*.md"):
                if is_cursor_rule_md_file(md_file.name):
                    rule_info = extract_single_rule_file(md_file, find_project_root, scope="project")
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

        # Check for legacy .cursorrules file in project root (project scope)
        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        try:
            for item in project_root_path.iterdir():
                if item.is_file() and is_agents_md_file(item.name):
                    rule_info = extract_single_rule_file(item, find_project_root, scope="project")
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                    break  # Only one AGENTS.md per directory
        except (PermissionError, OSError):
            pass

        # Walk for nested AGENTS.md in subdirectories (skip root — already handled above)
        system_dirs = self._get_system_directories()
        try:
            for subdir in project_root_path.iterdir():
                try:
                    if subdir.is_dir() and not subdir.name.startswith(".") and not subdir.is_symlink():
                        if not should_skip_path(subdir, system_dirs):
                            self._walk_for_agents_md(project_root_path, subdir, projects_by_root, current_depth=1, system_dirs=system_dirs)
                except (PermissionError, OSError):
                    continue
        except (PermissionError, OSError):
            pass

    def _walk_for_agents_md(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
        system_dirs: set = None
    ) -> None:
        """
        Walk project subdirectories looking for nested AGENTS.md files.

        Args:
            root_path: Project root path (for depth calculation)
            current_dir: Current directory being walked
            projects_by_root: Dictionary to populate with rules
            current_depth: Current recursion depth
            system_dirs: Cached set of system directory names to skip
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        if system_dirs is None:
            system_dirs = self._get_system_directories()

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item, system_dirs):
                        continue

                    if item.is_dir():
                        if item.name.startswith("."):
                            continue

                        if item.is_symlink():
                            continue

                        self._walk_for_agents_md(root_path, item, projects_by_root, current_depth + 1, system_dirs)

                    elif item.is_file() and is_agents_md_file(item.name):
                        rule_info = extract_single_rule_file(item, find_project_root, scope="project")
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking for AGENTS.md in {current_dir}: {e}")

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

