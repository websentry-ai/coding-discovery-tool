"""
Cursor CLI rules extraction for Windows systems.

Extracts Cursor CLI configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseCursorCliRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_project_root,
    should_skip_path,
    is_running_as_admin,
)

logger = logging.getLogger(__name__)


class WindowsCursorCliRulesExtractor(BaseCursorCliRulesExtractor):
    """Extractor for Cursor CLI rules on Windows systems."""

    def extract_all_cursor_cli_rules(self) -> List[Dict]:
        """
        Extract all Cursor CLI rules from all projects on Windows.
        """
        projects_by_root = {}

        logger.info("Extracting user-level Cursor CLI rules...")
        self._extract_user_level_rules(projects_by_root)

        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Cursor CLI rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_user_level_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract user-level Cursor CLI rules from %USERPROFILE%\\.cursor\\.

        When running as admin, scans all user directories.
        """
        def extract_for_user(user_home: Path) -> None:
            user_cursor_dir = user_home / ".cursor"

            if not user_cursor_dir.exists() or not user_cursor_dir.is_dir():
                return

            for mdc_file in user_cursor_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="user")
                if rule_info:
                    project_root = str(user_home)
                    add_rule_to_project(rule_info, project_root, projects_by_root)

            rules_dir = user_cursor_dir / "rules"
            if rules_dir.exists() and rules_dir.is_dir():
                for mdc_file in rules_dir.glob("*.mdc"):
                    rule_info = extract_single_rule_file(mdc_file, find_project_root, scope="user")
                    if rule_info:
                        project_root = str(user_home)
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
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
        """
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir()
                            if item.is_dir() and not should_skip_path(item, system_dirs)]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_cursor_directories, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            self._walk_for_cursor_directories(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_cursor_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .cursor directories.
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    system_dirs = self._get_system_directories()
                    if should_skip_path(item, system_dirs):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name == ".cursor":
                            self._extract_rules_from_cursor_directory(item, projects_by_root)
                            continue

                        self._walk_for_cursor_directories(root_path, item, projects_by_root, current_depth + 1)

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
        """
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, scope="project")
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, scope="project")
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

    def _get_system_directories(self) -> set:
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
