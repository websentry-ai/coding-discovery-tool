"""
Cursor rules extraction for macOS systems.

Extracts Cursor IDE configuration files (.mdc files) from all projects
on the user's machine, grouping them by project root.
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
    get_top_level_directories,
    should_process_directory,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)


class MacOSCursorRulesExtractor(BaseCursorRulesExtractor):
    """Extractor for Cursor rules on macOS systems."""

    def extract_all_cursor_rules(self) -> List[Dict]:
        """
        Extract all Cursor rules from all projects on macOS.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")
        
        logger.info(f"Searching for Cursor rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # When searching from root, iterate top-level directories first to avoid system paths
        if root_path == Path("/"):
            try:
                # Get top-level directories, skipping system ones
                top_level_dirs = get_top_level_directories(root_path)
                
                # Search each top-level directory (like /Users, /opt, etc.)
                for top_dir in top_level_dirs:
                    try:
                        self._walk_for_cursor_directories(root_path, top_dir, projects_by_root, current_depth=1)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                # Fallback to home directory
                logger.info("Falling back to home directory search")
                home_path = Path.home()
                self._extract_project_level_rules(home_path, projects_by_root)
        else:
            # For non-root paths, use standard rglob
            for cursor_dir in root_path.rglob(".cursor"):
                try:
                    if not should_process_directory(cursor_dir, root_path):
                        continue

                    # Extract rules from this .cursor directory
                    self._extract_rules_from_cursor_directory(cursor_dir, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {cursor_dir}: {e}")
                    continue

    def _walk_for_cursor_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .cursor directories.
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            projects_by_root: Dictionary to populate with rules
            current_depth: Current recursion depth
        """
        # Check depth limit
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    # Check if we should skip this path
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
                        # Found a .cursor directory!
                        if item.name == ".cursor":
                            # Extract rules from this .cursor directory
                            self._extract_rules_from_cursor_directory(item, projects_by_root)
                            # Don't recurse into .cursor directory
                            continue
                        
                        # Recurse into subdirectories
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
        Extract all rule files from a .cursor directory.
        
        Args:
            cursor_dir: Path to .cursor directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract .mdc files directly from .cursor directory
        for mdc_file in cursor_dir.glob("*.mdc"):
            rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        # Also check .cursor/rules/ subdirectory (if it exists)
        rules_dir = cursor_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            for mdc_file in rules_dir.glob("*.mdc"):
                rule_info = extract_single_rule_file(mdc_file, find_cursor_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

        # Check for legacy .cursorrules file in project root
        project_root_path = cursor_dir.parent
        legacy_file = project_root_path / ".cursorrules"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_cursor_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)


