"""
Cline rules extraction for macOS systems.

According to Cline documentation (https://docs.cline.bot/features/cline-rules):
- Global rules: ~/Documents/Cline/Rules/ (all markdown files)
- Workspace rules: **/.clinerules/** (all markdown files in folders)
- Workspace rules: **/.clinerules (single file)
- Workspace rules: **/AGENTS.md (AGENTS.md standard support)

Global Rules:
  - macOS: ~/Documents/Cline/Rules
  - All .md files in this directory are processed as global rules

Workspace Rules:
  - .clinerules/ directory: All .md files inside are processed
  - .clinerules file: Single file in project root
  - AGENTS.md: Fallback support for AGENTS.md standard
"""

import logging
from pathlib import Path
from typing import List, Dict, Optional

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
)

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


class MacOSClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on macOS systems."""

    GLOBAL_RULES_PATH = Path.home() / "Documents" / "Cline" / "Rules"

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
        Extract global Cline rules from ~/Documents/Cline/Rules/.
        
        According to Cline documentation (https://docs.cline.bot/features/cline-rules):
        - Global rules are stored in ~/Documents/Cline/Rules/ on macOS
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
                    rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                    if rule_info:
                        # For global rules, use the Rules directory as project root
                        project_root = str(self.GLOBAL_RULES_PATH)
                        add_rule_to_project(rule_info, project_root, projects_by_root)
                        logger.debug(f"Extracted global Cline rule: {rule_file.name}")
        except Exception as e:
            logger.debug(f"Error extracting global Cline rules: {e}")

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
                        self._walk_for_cline_rules(root_path, top_dir, projects_by_root, current_depth=1)
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
            # Search for .clinerules directories
            for clinerules_dir in root_path.rglob(".clinerules"):
                try:
                    if not should_process_directory(clinerules_dir, root_path):
                        continue

                    # Extract markdown files from .clinerules directory
                    self._extract_rules_from_clinerules_directory(clinerules_dir, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {clinerules_dir}: {e}")
                    continue

            # Search for .clinerules single file
            for clinerules_file in root_path.rglob(".clinerules"):
                try:
                    if clinerules_file.is_file() and should_process_file(clinerules_file, root_path):
                        rule_info = extract_single_rule_file(clinerules_file, find_cline_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {clinerules_file}: {e}")
                    continue

            # Search for AGENTS.md files
            for agents_file in root_path.rglob("AGENTS.md"):
                try:
                    if agents_file.is_file() and should_process_file(agents_file, root_path):
                        rule_info = extract_single_rule_file(agents_file, find_cline_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {agents_file}: {e}")
                    continue

    def _walk_for_cline_rules(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for Cline rule files.
        
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
                        self._walk_for_cline_rules(root_path, item, projects_by_root, current_depth + 1)
                    elif item.is_file():
                        # Check for .clinerules single file or AGENTS.md
                        if item.name == ".clinerules" or item.name == "AGENTS.md":
                            if should_process_file(item, root_path):
                                rule_info = extract_single_rule_file(item, find_cline_project_root)
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
                rule_info = extract_single_rule_file(rule_file, find_cline_project_root)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
                        logger.debug(f"Extracted workspace Cline rule from .clinerules/: {rule_file.name}")
