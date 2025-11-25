r"""
Cline rules extraction for Windows systems.

According to Cline documentation (https://docs.cline.bot/features/cline-rules):
- Global rules: Documents/Cline/Rules (all markdown files)
- Workspace rules: **/.clinerules/** (all markdown files in folders)
- Workspace rules: **/.clinerules (single file)
- Workspace rules: **/AGENTS.md (AGENTS.md standard support)

Global Rules:
  - Windows: Documents/Cline/Rules (uses system Documents folder)
  - All .md files in this directory are processed as global rules

Workspace Rules:
  - .clinerules/ directory: All .md files inside are processed
  - .clinerules file: Single file in project root
  - AGENTS.md: Fallback support for AGENTS.md standard
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict, Optional

from ...coding_tool_base import BaseClineRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

# Note: extract_single_rule_file uses find_project_root which we need to update
# For now, we'll handle project root finding inline

logger = logging.getLogger(__name__)


def find_cline_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for a Cline rule file.
    
    Determines project root based on file location:
    - .clinerules/*.md -> parent of .clinerules (2 levels up)
    - .clinerules -> directory containing the file (1 level up)
    - AGENTS.md -> directory containing the file
    
    Args:
        rule_file: Path to the rule file
        
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .clinerules directory (folder system)
    if parent.name == ".clinerules":
        return parent.parent
    
    # Case 2: File is directly in project root (.clinerules or AGENTS.md)
    return parent


class WindowsClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on Windows systems."""

    GLOBAL_RULES_PATH = Path.home() / "Documents" / "Cline" / "Rules"

    def extract_all_cline_rules(self) -> List[Dict]:
        """
        Extract all Cline rules from all projects on Windows.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract global rules
        self._extract_global_rules(projects_by_root)

        # Extract project-level rules from root drive (for MDM deployment)
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        logger.info(f"Searching for Cline rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        r"""
        Extract global Cline rules from Documents/Cline/Rules.
        
        According to Cline documentation (https://docs.cline.bot/features/cline-rules):
        - Global rules are stored in Documents/Cline/Rules on Windows
        - Uses system Documents folder (typically %USERPROFILE%/Documents/Cline/Rules)
        - All markdown files (*.md) in this directory are processed as global rules
        - These rules apply to every conversation globally
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        if not self.GLOBAL_RULES_PATH.exists():
            logger.debug(f"Global Cline rules directory not found: {self.GLOBAL_RULES_PATH}")
            return
        
        try:
            # Extract all markdown files from global rules directory
            # Cline automatically processes all Markdown files inside the Rules directory
            for rule_file in self.GLOBAL_RULES_PATH.glob("*.md"):
                if rule_file.is_file():
                    rule_info = extract_single_rule_file(rule_file)
                    if rule_info:
                        # For global rules, use the Rules directory as project root
                        # Override project_root since extract_single_rule_file might use different logic
                        rule_info['project_root'] = str(self.GLOBAL_RULES_PATH)
                        project_root = str(self.GLOBAL_RULES_PATH)
                        add_rule_to_project(rule_info, project_root, projects_by_root)
                        logger.debug(f"Extracted global Cline rule: {rule_file.name}")
        except Exception as e:
            logger.debug(f"Error extracting global Cline rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects using optimized walker.
        
        Uses parallel processing for top-level directories to improve performance.
        
        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Process top-level directories in parallel for better performance
        try:
            system_dirs = self._get_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Use parallel processing for top-level directories
            with ThreadPoolExecutor(max_workers=4) as executor:
                futures = {
                    executor.submit(self._walk_for_cline_rules, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()  # Raises exception if any occurred
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_cline_rules(root_path, root_path, projects_by_root, current_depth=0)
    
    def _walk_for_cline_rules(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for Cline rule files.
        
        This optimized walker:
        - Skips ignored directories early (before recursing)
        - Checks depth limits to avoid searching too deep
        - Stops recursing into project subdirectories once a project is found
        
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
                    system_dirs = self._get_system_directories()
                    if should_skip_path(item, system_dirs):
                        continue
                    
                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        # Path not relative to root (different drive on Windows)
                        continue
                    
                    if item.is_dir():
                        # Found a .clinerules directory!
                        if item.name == ".clinerules":
                            # Extract rules from this .clinerules directory
                            self._extract_rules_from_clinerules_directory(item, projects_by_root)
                            # Don't recurse into .clinerules directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_cline_rules(root_path, item, projects_by_root, current_depth + 1)
                    elif item.is_file():
                        # Check for .clinerules single file or AGENTS.md
                        if item.name == ".clinerules" or item.name == "AGENTS.md":
                            rule_info = extract_single_rule_file(item)
                            if rule_info:
                                # Override project_root using Cline-specific logic
                                rule_info['project_root'] = str(find_cline_project_root(item))
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

    def _extract_rules_from_clinerules_directory(self, clinerules_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .clinerules directory.
        
        According to Cline documentation (https://docs.cline.bot/features/cline-rules):
        - Cline automatically processes ALL Markdown files inside .clinerules/ directory
        - Files are combined into a unified set of rules
        - Numeric prefixes (e.g., 01-coding.md) help organize files in logical sequence
        
        Args:
            clinerules_dir: Path to .clinerules directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract all markdown files from .clinerules directory
        # Cline processes all .md files in the directory
        for rule_file in clinerules_dir.glob("*.md"):
            if rule_file.is_file():
                rule_info = extract_single_rule_file(rule_file)
                if rule_info:
                    # Override project_root using Cline-specific logic
                    rule_info['project_root'] = str(find_cline_project_root(rule_file))
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
                        logger.debug(f"Extracted workspace Cline rule from .clinerules/: {rule_file.name}")

    def _get_system_directories(self) -> set:
        """
        Get Windows system directories to skip.
        
        Returns:
            Set of system directory names
        """
        return {
            'Windows', 'Program Files', 'Program Files (x86)', 'ProgramData',
            'System Volume Information', '$Recycle.Bin', 'Recovery',
            'PerfLogs', 'Boot', 'System32', 'SysWOW64', 'WinSxS',
            'Config.Msi', 'Documents and Settings', 'MSOCache'
        }
