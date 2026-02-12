"""
Roo Code rules extraction for macOS systems.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseRooRulesExtractor
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


class MacOSRooRulesExtractor(BaseRooRulesExtractor):
    """Extractor for Roo Code rules on macOS systems."""

    ROO_RULES_DIRS = ["rules", "rules-architect", "rules-ask", "rules-code", "rules-debug", "rules-test"]

    def extract_all_roo_rules(self) -> List[Dict]:
        """
        Extract all Roo Code rules from all projects on macOS.
        """
        projects_by_root = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path("/")

        logger.info(f"Searching for Roo Code rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Roo Code rules from ~/Documents/Roo/Rules or ~/Roo/Rules.

        When running as root, scans all user directories.

        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_rules_path = user_home / "Documents" / "Roo" / "Rules"

            if not global_rules_path.exists():
                global_rules_path = user_home / "Roo" / "Rules"

            if global_rules_path.exists() and global_rules_path.is_dir():
                try:
                    for rule_file in global_rules_path.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, global_rules_path):
                            rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Roo Code rules for {user_home}: {e}")

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

                for top_dir in top_level_dirs:
                    try:
                        self._walk_for_roo_directories(root_path, top_dir, projects_by_root, current_depth=1)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search")
                home_path = Path.home()
                self._extract_project_level_rules(home_path, projects_by_root)
        else:
            for roo_dir in root_path.rglob(".roo"):
                try:
                    if not should_process_directory(roo_dir, root_path):
                        continue

                    self._extract_rules_from_roo_directory(roo_dir, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {roo_dir}: {e}")
                    continue

    def _walk_for_roo_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .roo directories.

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
                        if rule_file.is_file() and should_process_file(rule_file, roo_dir.parent):
                            rule_info = extract_single_rule_file(rule_file, find_roo_project_root)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error accessing .roo directory {roo_dir}: {e}")
        except Exception as e:
            logger.debug(f"Error extracting rules from {roo_dir}: {e}")
