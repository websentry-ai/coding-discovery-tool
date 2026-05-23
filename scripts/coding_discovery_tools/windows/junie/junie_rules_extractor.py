"""
Junie rules extraction for Windows systems.

Extracts Junie rules from .md files:
- Global rules: %USERPROFILE%\\.junie\\*.md
- Project-level rules: <project>\\.junie\\*.md
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseJunieRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    is_running_as_admin,
    is_user_level_tool_dir,
    should_skip_path,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)

JUNIE_DIR_NAME = ".junie"


def find_junie_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a Junie rule file.

    - Rules in project\\.junie\\*.md -> parent of .junie is project root
    - Global rules in ~\\.junie\\*.md -> home directory is project root
    """
    parent = rule_file.parent

    if parent.name == JUNIE_DIR_NAME:
        return parent.parent

    return parent


class WindowsJunieRulesExtractor(BaseJunieRulesExtractor):
    """Extractor for Junie rules on Windows systems."""

    def extract_all_junie_rules(self) -> List[Dict]:
        """Extract all Junie rules from all projects on Windows."""
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path(Path.home().anchor)  # e.g. "C:\\"
        logger.info(f"Searching for Junie rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract global Junie rules from ~\\.junie\\, scanning all users when admin."""
        def extract_for_user(user_home: Path) -> None:
            junie_dir = user_home / JUNIE_DIR_NAME
            if not junie_dir.exists() or not junie_dir.is_dir():
                return
            try:
                for md_file in junie_dir.glob("*.md"):
                    if md_file.is_file() and not should_skip_path(md_file, set()):
                        rule_info = extract_single_rule_file(md_file, find_junie_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Junie rules for {user_home}: {e}")

        if is_running_as_admin():
            users_dir = Path(Path.home().anchor) / "Users"
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
        """Walk the drive recursively for project-level .junie directories."""
        self._walk_for_junie_dirs(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_junie_dirs(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recursively walk directory tree looking for .junie directories."""
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            system_dirs = get_windows_system_directories()
            for item in current_dir.iterdir():
                try:
                    if should_skip_path(item, system_dirs):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name == JUNIE_DIR_NAME:
                            # Skip user-level ~\.junie — handled by _extract_global_rules.
                            if is_user_level_tool_dir(item):
                                continue
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
        """Extract all .md files from a project-level .junie directory."""
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
