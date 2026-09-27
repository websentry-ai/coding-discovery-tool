"""
Cursor rules extraction for Windows systems.

Extracts Cursor IDE configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.

Supports two scopes:
- user: Global user rules in %USERPROFILE%\\.cursor\\rules\\*.mdc
- project: Project-specific rules in .cursor\\*.mdc, .cursor\\rules\\*.mdc, .cursorrules
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorRulesExtractor
from ...constants import MAX_SEARCH_DEPTH, scan_dir_entries
from ...cursor_rules_helpers import is_agents_md_file, extract_cursor_rules_from_dir
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_and_add_rule,
    extract_single_rule_file,
    find_project_root,
    get_windows_system_directories,
    is_user_level_tool_dir,
    scan_windows_user_directories,
    should_skip_path,
    walk_for_tool_directories,
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

            extract_cursor_rules_from_dir(
                user_cursor_dir, extract_single_rule_file, find_project_root,
                add_rule_to_project, projects_by_root, str(user_home), scope="user"
            )

        scan_windows_user_directories(extract_for_user)

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.

        Finds each project's ``.cursor`` directory through the shared directory
        index, so the drive is walked once for all tools. The per-project AGENTS.md
        sweep stays inside ``_extract_rules_from_cursor_directory``.

        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        walk_for_tool_directories(
            root_path, root_path, ".cursor",
            self._extract_rules_from_cursor_directory, projects_by_root,
        )

    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .cursor directory (project scope).

        Args:
            cursor_dir: Path to .cursor directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        if is_user_level_tool_dir(cursor_dir):
            return

        # Extract .mdc and .md files from .cursor/ and .cursor/rules/
        project_root_path = cursor_dir.parent
        extract_cursor_rules_from_dir(
            cursor_dir, extract_single_rule_file, find_project_root,
            add_rule_to_project, projects_by_root, str(project_root_path), scope="project"
        )

        # Check for legacy .cursorrules file in project root (project scope)
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            extract_and_add_rule(
                legacy_file, find_project_root, add_rule_to_project,
                projects_by_root, scope="project"
            )

        try:
            for item in project_root_path.iterdir():
                if item.is_file() and is_agents_md_file(item.name):
                    extract_and_add_rule(
                        item, find_project_root, add_rule_to_project,
                        projects_by_root, scope="project"
                    )
                    break  # Only one AGENTS.md per directory
        except (PermissionError, OSError):
            pass

        # Walk for nested AGENTS.md in subdirectories (skip root -- already handled above)
        system_dirs = get_windows_system_directories()
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
            system_dirs = get_windows_system_directories()

        try:
            for _entry in scan_dir_entries(current_dir):
                item = Path(_entry.path)
                try:
                    if should_skip_path(item, system_dirs):
                        continue

                    if _entry.is_dir():
                        if item.name.startswith("."):
                            continue

                        if _entry.is_symlink():
                            continue

                        if (item / ".cursor").is_dir():
                            continue

                        self._walk_for_agents_md(root_path, item, projects_by_root, current_depth + 1, system_dirs)

                    elif _entry.is_file() and is_agents_md_file(item.name):
                        extract_and_add_rule(
                            item, find_project_root, add_rule_to_project,
                            projects_by_root, scope="project"
                        )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking for AGENTS.md in {current_dir}: {e}")


