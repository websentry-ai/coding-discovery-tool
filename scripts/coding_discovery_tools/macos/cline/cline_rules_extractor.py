"""
Cline rules extraction for macOS systems.

Extracts Cline configuration files from .clinerules directories and global rules
on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClineRulesExtractor
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


def find_cline_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Cline rule file.
    
    For Cline rules:
    - Files in .clinerules/ directory -> parent of .clinerules (project root)
    - Global rules -> home directory
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .clinerules directory (workspace rules)
    if parent.name == ".clinerules":
        return parent.parent
    
    # Case 2: Global rules (in ~/Documents/Cline/Rules or ~/Cline/Rules)
    # Return the directory containing the Rules folder as project root
    if parent.name == "Rules":
        # Check if it's in Documents/Cline or just Cline
        if parent.parent.name == "Cline":
            return parent.parent.parent  # ~/Documents or ~
    
    # Default: return parent directory
    return parent


class MacOSClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on macOS systems."""

    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on macOS.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")
        
        logger.info(f"Searching for Cline rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Cline rules from ~/Documents/Cline/Rules or ~/Cline/Rules.
        
        When running as root, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            # Primary location: ~/Documents/Cline/Rules
            global_rules_path = user_home / "Documents" / "Cline" / "Rules"
            
            # Fallback location: ~/Cline/Rules
            if not global_rules_path.exists():
                global_rules_path = user_home / "Cline" / "Rules"
            
            if global_rules_path.exists() and global_rules_path.is_dir():
                try:
                    # Extract all .md files from global rules directory
                    for rule_file in global_rules_path.glob("*.md"):
                        if rule_file.is_file() and should_process_file(rule_file, global_rules_path):
                            rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Cline rules for {user_home}: {e}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

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
                        self._walk_for_clinerules_directories(root_path, top_dir, projects_by_root, current_depth=1)
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
            for clinerules_dir in root_path.rglob(".clinerules"):
                try:
                    if not should_process_directory(clinerules_dir, root_path):
                        continue

                    # Extract rules from this .clinerules directory
                    self._extract_rules_from_clinerules_directory(clinerules_dir, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {clinerules_dir}: {e}")
                    continue

    def _walk_for_clinerules_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .clinerules directories.
        
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
                        # Found a .clinerules directory!
                        if item.name == ".clinerules":
                            # Extract rules from this .clinerules directory
                            self._extract_rules_from_clinerules_directory(item, projects_by_root)
                            # Don't recurse into .clinerules directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_clinerules_directories(root_path, item, projects_by_root, current_depth + 1)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_clinerules_directory(
        self, clinerules_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract all rule files from a .clinerules directory.
        
        Args:
            clinerules_dir: Path to .clinerules directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract all .md files from .clinerules directory
        for rule_file in clinerules_dir.glob("*.md"):
            if rule_file.is_file() and should_process_file(rule_file, clinerules_dir.parent):
                rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

