"""
Antigravity rules extraction for Windows systems.

Extracts Antigravity configuration files from:
- Project-level: .agent/rules/*.md
- Global-level: ~/.gemini/GEMINI.md
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseAntigravityRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    should_skip_path,
)

logger = logging.getLogger(__name__)


def find_antigravity_project_root(rule_file: Path) -> Path:
    """
    Find the project root directory for an Antigravity rule file.
    
    For Antigravity rules:
    - Files in .agent/rules/ directory -> parent of .agent (project root)
    - Global rules in ~/.gemini/GEMINI.md -> home directory
    
    Args:
        rule_file: Path to the rule file
    
    Returns:
        Project root path
    """
    parent = rule_file.parent
    
    # Case 1: File is in .agent/rules/ directory (project-level rules)
    if parent.name == "rules" and parent.parent.name == ".agent":
        return parent.parent.parent
    
    # Case 2: File is directly in .agent directory (shouldn't happen per spec, but handle it)
    if parent.name == ".agent":
        return parent.parent
    
    # Case 3: Global rules in ~/.gemini/GEMINI.md
    # Return the .gemini directory's parent (which would be home directory)
    if parent.name == ".gemini" and rule_file.name.upper() == "GEMINI.MD":
        return parent.parent
    
    # Fallback: use the directory containing the file
    return parent


class WindowsAntigravityRulesExtractor(BaseAntigravityRulesExtractor):
    """Extractor for Antigravity rules on Windows systems."""

    def extract_all_antigravity_rules(self) -> List[Dict]:
        """
        Extract all Antigravity rules from all projects on Windows.
        
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
        
        logger.info(f"Searching for Antigravity rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Antigravity rules from ~/.gemini/GEMINI.md.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Check current user's home directory
        global_rules_path = Path.home() / ".gemini" / "GEMINI.md"
        if global_rules_path.exists() and global_rules_path.is_file():
            try:
                rule_info = extract_single_rule_file(global_rules_path)
                if rule_info:
                    # Override project_root using our custom function
                    project_root = find_antigravity_project_root(global_rules_path)
                    rule_info['project_root'] = str(project_root)
                    add_rule_to_project(rule_info, str(project_root), projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Antigravity rules: {e}")

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
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
                    executor.submit(self._walk_for_agent_dirs, root_path, dir_path, projects_by_root, current_depth=1)
                    for dir_path in top_level_dirs
                }
                
                for future in as_completed(futures):
                    try:
                        future.result()
                    except Exception as e:
                        logger.debug(f"Error in parallel processing: {e}")
        except (PermissionError, OSError):
            # Fallback to sequential if parallel fails
            self._walk_for_agent_dirs(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_agent_dirs(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .agent directories.
        
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
                        continue
                    
                    if item.is_dir():
                        # Found a .agent directory!
                        if item.name == ".agent":
                            # Extract rules from this .agent directory
                            self._extract_rules_from_agent_directory(item, projects_by_root)
                            # Don't recurse into .agent directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_agent_dirs(root_path, item, projects_by_root, current_depth + 1)
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rules_from_agent_directory(self, agent_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .agent directory.
        
        Args:
            agent_dir: Path to .agent directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract files from .agent/rules/ subdirectory
        rules_dir = agent_dir / "rules"
        if rules_dir.exists() and rules_dir.is_dir():
            # Extract all .md files from rules directory
            for rule_file in rules_dir.glob("*.md"):
                if rule_file.is_file():
                    try:
                        rule_info = extract_single_rule_file(rule_file)
                        if rule_info:
                            # Override project_root using our custom function
                            project_root = find_antigravity_project_root(rule_file)
                            rule_info['project_root'] = str(project_root)
                            add_rule_to_project(rule_info, str(project_root), projects_by_root)
                    except Exception as e:
                        logger.debug(f"Error extracting rule from {rule_file}: {e}")

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

