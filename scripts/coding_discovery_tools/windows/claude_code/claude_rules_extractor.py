"""
Claude Code rules extraction for Windows systems.

Extracts Claude Code configuration files (.clauderules and CLAUDE.md) from all projects
on the user's machine, grouping them by project root.

Rules are stored in:
- User-level: ~/.claude/CLAUDE.md (any casing)
- Project-level: **/.claude/CLAUDE.md, **/.clauderules, **/CLAUDE.md (any casing)
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClaudeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_project_root,
    is_running_as_admin,
    should_skip_path,
)

logger = logging.getLogger(__name__)

CLAUDE_DIR_NAME = ".claude"


def _is_claude_md_file(filename: str) -> bool:
    """Check if filename is a CLAUDE.md file (case-insensitive)."""
    return filename.lower() == "claude.md"


def build_rules_project_list(projects_by_root: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Convert projects dictionary to list format with 'rules' key.

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


class WindowsClaudeRulesExtractor(BaseClaudeRulesExtractor):
    """Extractor for Claude Code rules on Windows systems."""

    def extract_all_claude_rules(self) -> Dict:
        """
        Extract all Claude Code rules from all projects on Windows.

        Returns:
            Dict with:
            - user_rules: List of user-level rule dicts (global, scope: "user")
            - project_rules: List of project dicts with project_root and rules
        """
        user_rules = []
        projects_by_root = {}

        # Extract user-level rules from ~/.claude/CLAUDE.md
        self._extract_user_level_rules(user_rules)

        # Extract project-level rules from root drive (for MDM deployment)
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)

        logger.info(f"Searching for Claude rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return {
            "user_rules": user_rules,
            "project_rules": build_rules_project_list(projects_by_root)
        }

    def _extract_user_level_rules(self, user_rules: List[Dict]) -> None:
        """
        Extract user-level rules from ~/.claude/ directory.

        Looks for CLAUDE.md (case-insensitive) in the user's .claude directory.

        Args:
            user_rules: List to populate with user-level rules
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract user-level rules for a specific user."""
            claude_dir = user_home / CLAUDE_DIR_NAME

            if not claude_dir.exists() or not claude_dir.is_dir():
                return

            try:
                # Look for CLAUDE.md (case-insensitive) in .claude directory
                for item in claude_dir.iterdir():
                    if item.is_file() and _is_claude_md_file(item.name):
                        rule_info = extract_single_rule_file(item, find_project_root, scope="user")
                        if rule_info:
                            # Remove project_root from user rules (it's the home dir, not meaningful)
                            rule_info.pop('project_root', None)
                            user_rules.append(rule_info)
            except Exception as e:
                logger.debug(f"Error extracting user-level rules for {user_home}: {e}")

        # When running as admin, scan all user directories
        if is_running_as_admin():
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
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.

        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir()
                              if item.is_dir() and not should_skip_path(item, system_dirs)]

            # Use parallel processing for top-level directories
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_claude_files, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_claude_files(root_path, root_path, projects_by_root, current_depth=0)
    
    def _walk_for_claude_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for Claude rule files.

        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects_by_root: Dictionary to populate with rules
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
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
                        # Check if this is a .claude directory
                        if item.name == CLAUDE_DIR_NAME:
                            # Skip user-level .claude directories (already extracted)
                            if self._is_user_level_claude_dir(item):
                                continue
                            # Extract rules from this .claude directory
                            self._extract_rules_from_claude_directory(item, projects_by_root)
                            # Don't recurse into .claude directory
                            continue

                        # Recurse into other directories
                        self._walk_for_claude_files(root_path, item, projects_by_root, current_depth + 1)

                    elif item.is_file():
                        # Check for .clauderules or CLAUDE.md files (case-insensitive for claude.md)
                        if item.name == ".clauderules" or _is_claude_md_file(item.name):
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
            logger.debug(f"Error walking {current_dir}: {e}")

    def _is_user_level_claude_dir(self, claude_dir: Path) -> bool:
        """
        Check if a .claude directory is at the user level (in home directory).

        Args:
            claude_dir: Path to the .claude directory

        Returns:
            True if this is a user-level .claude directory
        """
        try:
            parent_of_claude = claude_dir.parent

            # Check if parent of .claude is a home directory
            if parent_of_claude == Path.home():
                return True

            # For admin scanning, check if it's under C:\Users\<username>
            if str(parent_of_claude).startswith('C:\\Users\\'):
                parent_parts = parent_of_claude.parts
                # C:\Users\<username> has 3 parts: ('C:\\', 'Users', '<username>')
                if len(parent_parts) == 3:
                    return True

            return False
        except Exception:
            return False

    def _extract_rules_from_claude_directory(self, claude_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .claude directory.

        Args:
            claude_dir: Path to .claude directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        try:
            # Extract .clauderules from .claude directory (current format)
            clauderules_file = claude_dir / ".clauderules"
            if clauderules_file.exists() and clauderules_file.is_file():
                rule_info = extract_single_rule_file(clauderules_file, find_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract CLAUDE.md (case-insensitive) from .claude directory
            for item in claude_dir.iterdir():
                if item.is_file() and _is_claude_md_file(item.name):
                    rule_info = extract_single_rule_file(item, find_project_root, scope="project")
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {claude_dir}: {e}")

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