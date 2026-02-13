"""
Junie rules extraction for macOS systems.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseJunieRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_top_level_directories,
    should_process_directory,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)

JUNIE_DIR_NAME = ".junie"


def find_junie_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a Junie rule file.

    For Junie:
    - Rules in project/.junie/*.md -> parent of .junie is project root
    - Global rules in ~/.junie/*.md -> home directory is project root
    """
    parent = rule_file.parent

    if parent.name == JUNIE_DIR_NAME:
        return parent.parent

    return parent


class MacOSJunieRulesExtractor(BaseJunieRulesExtractor):
    """Extractor for Junie rules on macOS systems."""

    def extract_all_junie_rules(self) -> List[Dict]:
        """
        Extract all Junie rules from all projects on macOS.
        """
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path("/")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Junie rules from ~/.junie/ directory.
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            junie_dir = user_home / JUNIE_DIR_NAME

            if not junie_dir.exists() or not junie_dir.is_dir():
                return

            try:
                # Extract all .md files from the .junie directory
                for md_file in junie_dir.glob("*.md"):
                    if md_file.is_file() and should_process_file(md_file, junie_dir):
                        rule_info = extract_single_rule_file(
                            md_file,
                            find_junie_project_root
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Junie rules for {user_home}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        """
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for dir_path in top_level_dirs:
                    if should_process_directory(dir_path, root_path):
                        self._walk_for_junie_dirs(root_path, dir_path, projects_by_root, current_depth=1)
            except (PermissionError, OSError):
                pass
        else:
            self._walk_for_junie_dirs(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_junie_dirs(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .junie directories.
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
                        # Check if this is a .junie directory
                        if item.name == JUNIE_DIR_NAME:
                            if item.parent.name in ('~', '') or (str(item.parent).startswith('/Users/') and item.parent.parent == Path('/Users')):
                                continue

                            # Extract all .md files from this .junie directory
                            self._extract_junie_dir_rules(item, projects_by_root)
                        else:
                            self._walk_for_junie_dirs(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_junie_dir_rules(self, junie_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all .md files from a .junie directory.
        """
        try:
            for md_file in junie_dir.glob("*.md"):
                if md_file.is_file():
                    rule_info = extract_single_rule_file(md_file, find_junie_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {junie_dir}: {e}")
