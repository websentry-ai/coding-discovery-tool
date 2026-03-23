"""
Cursor rules extraction for macOS systems.

Extracts Cursor IDE configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.

Supports two scopes:
- user: Global user rules in ~/.cursor/rules/*.mdc
- project: Project-specific rules in .cursor/*.mdc, .cursor/rules/*.mdc, .cursorrules
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_cursor_project_root,
    extract_project_level_rules_with_fallback,
    walk_for_tool_directories,
    is_running_as_root,
    scan_user_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


def _is_cursor_rule_md_file(filename: str) -> bool:
    """Check if filename is a Cursor rule .md file (excludes hidden files and non-rule .md)."""
    return filename.lower().endswith(".md") and not filename.startswith(".")


def _is_agents_md_file(filename: str) -> bool:
    """Check if filename is an AGENTS.md file (case-insensitive)."""
    return filename.lower() == "agents.md"


class MacOSCursorRulesExtractor(BaseCursorRulesExtractor):
    """Extractor for Cursor rules on macOS systems."""

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on macOS.

        Extracts rules from two scopes:
        - user: Global user rules in ~/.cursor/rules/*.mdc (scope="user")
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

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")

        logger.info(f"Searching for Cursor rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_user_level_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract user-level Cursor rules from ~/.cursor/.

        When running as root, scans all user directories.

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
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                if rule_info:
                    project_root = str(user_home)  # User home is the "project root" for user rules
                    add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract .md rule files from ~/.cursor/
            for md_file in user_cursor_dir.glob("*.md"):
                if _is_cursor_rule_md_file(md_file.name):
                    rule_info = extract_single_rule_file(md_file, find_cursor_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract from ~/.cursor/rules/
            rules_dir = user_cursor_dir / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for mdc_file in rules_dir.glob("*.mdc"):
                    rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

                # Extract .md rule files from ~/.cursor/rules/
                for md_file in rules_dir.glob("*.md"):
                    if _is_cursor_rule_md_file(md_file.name):
                        rule_info = extract_single_rule_file(md_file, find_cursor_project_root, scope="user")
                        if rule_info:
                            project_root = str(user_home)
                            add_rule_to_project(rule_info, project_root, projects_by_root)

        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_cursor_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            """Wrapper to use shared walk helper with tool-specific extraction."""
            walk_for_tool_directories(
                root, current, ".cursor", self._extract_rules_from_cursor_directory,
                projects, current_depth
            )
        
        extract_project_level_rules_with_fallback(
            root_path,
            ".cursor",
            self._extract_rules_from_cursor_directory,
            walk_for_cursor_dirs,
            projects_by_root
        )


    def _extract_rules_from_cursor_directory(self, cursor_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .cursor directory (project scope).

        Args:
            cursor_dir: Path to .cursor directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract .mdc files directly from .cursor directory (project scope)
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        # Extract .md rule files from .cursor directory (project scope)
        for md_file in cursor_dir.glob("*.md"):
            if _is_cursor_rule_md_file(md_file.name):
                rule_info = extract_single_rule_file(md_file, find_cursor_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        # Also check .cursor/rules/ subdirectory (if it exists)
        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract .md rule files from .cursor/rules/
            for md_file in rules_dir.glob("*.md"):
                if _is_cursor_rule_md_file(md_file.name):
                    rule_info = extract_single_rule_file(md_file, find_cursor_project_root, scope="project")
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)

        # Check for legacy .cursorrules file in project root (project scope)
        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_cursor_project_root, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        # Check for AGENTS.md at project root (case-insensitive)
        for item in project_root_path.iterdir():
            if item.is_file() and _is_agents_md_file(item.name):
                rule_info = extract_single_rule_file(item, find_cursor_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
                break  # Only one AGENTS.md per directory

        # Walk for nested AGENTS.md in subdirectories
        self._walk_for_agents_md(project_root_path, project_root_path, projects_by_root, current_depth=0)

    def _walk_for_agents_md(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Walk project subdirectories looking for nested AGENTS.md files.

        Args:
            root_path: Project root path (for depth calculation)
            current_dir: Current directory being walked
            projects_by_root: Dictionary to populate with rules
            current_depth: Current recursion depth
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item) or should_skip_system_path(item):
                        continue

                    if item.is_dir():
                        # Skip .cursor and hidden directories
                        if item.name.startswith("."):
                            continue

                        if item.is_symlink():
                            continue

                        # Recurse into subdirectories
                        self._walk_for_agents_md(root_path, item, projects_by_root, current_depth + 1)

                    elif item.is_file() and _is_agents_md_file(item.name):
                        rule_info = extract_single_rule_file(item, find_cursor_project_root, scope="project")
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


