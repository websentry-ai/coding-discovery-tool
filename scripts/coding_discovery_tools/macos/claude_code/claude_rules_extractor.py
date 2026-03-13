"""
Claude Code rules extraction for macOS systems.

Extracts Claude Code configuration files (.clauderules and CLAUDE.md) from all projects
on the user's machine, grouping them by project root.

Rules are stored in:
- Managed: /Library/Application Support/ClaudeCode/CLAUDE.md
- User-level: ~/.claude/CLAUDE.md (any casing)
- Project-level: **/.claude/CLAUDE.md, **/.clauderules, **/CLAUDE.md (any casing)
- Local: **/CLAUDE.local.md (any casing, personal project-specific preferences)
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClaudeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_claude_project_root,
    get_top_level_directories,
    is_running_as_root,
    scan_user_directories,
    should_process_directory,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)

CLAUDE_DIR_NAME = ".claude"


def _is_claude_md_file(filename: str) -> bool:
    """Check if filename is a CLAUDE.md file (case-insensitive)."""
    return filename.lower() == "claude.md"


def _is_claude_local_md_file(filename: str) -> bool:
    """Check if filename is a CLAUDE.local.md file (case-insensitive)."""
    return filename.lower() == "claude.local.md"


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


class MacOSClaudeRulesExtractor(BaseClaudeRulesExtractor):
    """Extractor for Claude Code rules on macOS systems."""

    MANAGED_RULES_PATH = Path("/Library/Application Support/ClaudeCode/CLAUDE.md")

    def extract_all_claude_rules(self) -> Dict:
        """
        Extract all Claude Code rules from all projects on macOS.

        Returns:
            Dict with:
            - managed_rules: List of managed rule dicts (org-level, scope: "managed")
            - user_rules: List of user-level rule dicts (global, scope: "user")
            - project_rules: List of project dicts with project_root and rules
        """
        managed_rules = []
        user_rules = []
        projects_by_root = {}

        # Extract managed rules from /Library/Application Support/ClaudeCode/CLAUDE.md
        self._extract_managed_rules(managed_rules)

        # Extract user-level rules from ~/.claude/CLAUDE.md
        self._extract_user_level_rules(user_rules)

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")

        logger.info(f"Searching for Claude rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return {
            "managed_rules": managed_rules,
            "user_rules": user_rules,
            "project_rules": build_rules_project_list(projects_by_root)
        }

    def _extract_managed_rules(self, managed_rules: List[Dict]) -> None:
        """
        Extract managed rules from /Library/Application Support/ClaudeCode/CLAUDE.md.

        Args:
            managed_rules: List to populate with managed rules
        """
        if not self.MANAGED_RULES_PATH.exists():
            return

        try:
            rule_info = extract_single_rule_file(
                self.MANAGED_RULES_PATH, find_claude_project_root, scope="managed"
            )
            if rule_info:
                rule_info["project_path"] = rule_info.pop("project_root", None)
                managed_rules.append(rule_info)
        except Exception as e:
            logger.debug(f"Error extracting managed rules: {e}")

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
                        rule_info = extract_single_rule_file(item, find_claude_project_root, scope="user")
                        if rule_info:
                            rule_info["project_path"] = rule_info.pop("project_root", None)
                            user_rules.append(rule_info)
            except Exception as e:
                logger.debug(f"Error extracting user-level rules for {user_home}: {e}")

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
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_claude_files(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                # Fallback to home directory
                logger.info("Falling back to home directory search for rules")
                home_path = Path.home()
                self._walk_for_claude_files(home_path, home_path, projects_by_root, current_depth=0)
        else:
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
                            if should_process_file(item, root_path):
                                rule_info = extract_single_rule_file(item, find_claude_project_root, scope="project")
                                if rule_info:
                                    project_root = rule_info.get('project_root')
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                        # Check for CLAUDE.local.md files (case-insensitive)
                        elif _is_claude_local_md_file(item.name):
                            if should_process_file(item, root_path):
                                rule_info = extract_single_rule_file(item, find_claude_project_root, scope="local")
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

            # For root scanning, check if it's under /Users/<username>
            if str(parent_of_claude).startswith('/Users/'):
                parent_parts = parent_of_claude.parts
                # /Users/<username> has 3 parts: ('/', 'Users', '<username>')
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
                rule_info = extract_single_rule_file(clauderules_file, find_claude_project_root, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

            # Extract CLAUDE.md (case-insensitive) from .claude directory
            for item in claude_dir.iterdir():
                if item.is_file() and _is_claude_md_file(item.name):
                    rule_info = extract_single_rule_file(item, find_claude_project_root, scope="project")
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {claude_dir}: {e}")
