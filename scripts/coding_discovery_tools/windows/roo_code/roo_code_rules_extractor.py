"""
Roo Code rules extraction for Windows systems.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseRooRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

logger = logging.getLogger(__name__)


def find_roo_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Roo Code rule file.
    """
    parent = rule_file.parent

    if parent.name == "rules" or parent.name.startswith("rules-"):
        if parent.parent.name == ".roo":
            return parent.parent.parent

    if parent.name == "Rules":
        if parent.parent.name == "Roo":
            return parent.parent.parent
    return parent


class WindowsRooRulesExtractor(BaseRooRulesExtractor):
    """Extractor for Roo Code rules on Windows systems."""

    ROO_RULES_DIRS = ["rules", "rules-architect", "rules-ask", "rules-code", "rules-debug", "rules-test"]

    def extract_all_roo_rules(self) -> List[Dict]:
        """
        Extract all Roo Code rules from all projects on Windows.
        """
        projects_by_root = {}
        self._extract_global_rules(projects_by_root)
        root_drive = Path.home().anchor
        root_path = Path(root_drive)

        logger.info(f"Searching for Roo Code rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Roo Code rules from ~/Documents/Roo/Rules or ~/Roo/Rules.
        """
        user_home = Path.home()
        global_rules_path = user_home / "Documents" / "Roo" / "Rules"
        if not global_rules_path.exists():
            global_rules_path = user_home / "Roo" / "Rules"

        if global_rules_path.exists() and global_rules_path.is_dir():
            try:
                for rule_file in global_rules_path.glob("*.md"):
                    if rule_file.is_file():
                        rule_info = self._extract_single_rule_file_with_root(rule_file)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Roo Code rules: {e}")

    def _extract_single_rule_file_with_root(self, rule_file: Path) -> Dict:
        """
        Extract a single rule file with metadata using Roo-specific project root finder.
        """
        try:
            if not rule_file.exists() or not rule_file.is_file():
                return None

            from ...windows_extraction_helpers import get_file_metadata, read_file_content
            file_metadata = get_file_metadata(rule_file)
            project_root = find_roo_project_root(rule_file)
            content, truncated = read_file_content(rule_file, file_metadata['size'])

            return {
                "file_path": str(rule_file),
                "file_name": rule_file.name,
                "project_root": str(project_root) if project_root else None,
                "content": content,
                "size": file_metadata['size'],
                "last_modified": file_metadata['last_modified'],
                "truncated": truncated
            }

        except PermissionError as e:
            logger.warning(f"Permission denied reading {rule_file}: {e}")
            return None
        except UnicodeDecodeError as e:
            logger.warning(f"Unable to decode {rule_file} as text: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading rule file {rule_file}: {e}")
            return None

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects using optimized walker.
        """
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir()
                            if item.is_dir() and not should_skip_path(item, system_dirs)]

            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_roo_directories, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }

                for future in as_completed(futures):
                    try:
                        future.result()  # Raises exception if any occurred
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_roo_directories(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_roo_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .roo directories.
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
                        if item.name == ".roo":
                            self._extract_rules_from_roo_directory(item, projects_by_root)
                            continue
                        self._walk_for_roo_directories(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_roo_directory(
        self, roo_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .roo directory.

        Searches for rules in:
        - .roo/rules/*.md
        - .roo/rules-architect/*.md
        - .roo/rules-ask/*.md
        - .roo/rules-code/*.md
        - .roo/rules-debug/*.md
        - .roo/rules-test/*.md
        - And any other .roo/rules-*/*.md directories
        """
        try:
            for item in roo_dir.iterdir():
                if item.is_dir() and (item.name == "rules" or item.name.startswith("rules-")):
                    for rule_file in item.glob("*.md"):
                        if rule_file.is_file():
                            rule_info = self._extract_single_rule_file_with_root(rule_file)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing .roo directory {roo_dir}: {e}")
        except Exception as e:
            logger.debug(f"Error extracting rules from {roo_dir}: {e}")

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
