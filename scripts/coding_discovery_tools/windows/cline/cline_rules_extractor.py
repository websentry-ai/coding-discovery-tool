"""
Cline rules extraction for Windows systems.

Extracts Cline configuration files from .clinerules directories and global rules
on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClineRulesExtractor
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    walk_for_tool_directories,
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


class WindowsClineRulesExtractor(BaseClineRulesExtractor):
    """Extractor for Cline rules on Windows systems."""

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
        """
        Extract global Cline rules from ~/Documents/Cline/Rules or ~/Cline/Rules.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        user_home = Path.home()
        
        # Primary location: ~/Documents/Cline/Rules
        global_rules_path = user_home / "Documents" / "Cline" / "Rules"
        
        # Fallback location: ~/Cline/Rules
        if not global_rules_path.exists():
            global_rules_path = user_home / "Cline" / "Rules"
        
        if global_rules_path.exists() and global_rules_path.is_dir():
            try:
                # Extract all .md files from global rules directory
                for rule_file in global_rules_path.glob("*.md"):
                    if rule_file.is_file():
                        # Use custom find_project_root function for Cline
                        rule_info = self._extract_single_rule_file_with_root(rule_file)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Cline rules: {e}")

    def _extract_single_rule_file_with_root(self, rule_file: Path) -> Dict:
        """
        Extract a single rule file with metadata using Cline-specific project root finder.
        
        Args:
            rule_file: Path to the rule file
            
        Returns:
            Dict with file info or None if extraction fails
        """
        try:
            if not rule_file.exists() or not rule_file.is_file():
                return None

            from ...windows_extraction_helpers import get_file_metadata, read_file_content
            file_metadata = get_file_metadata(rule_file)
            project_root = find_cline_project_root(rule_file)
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
        Extract project-level rules recursively from all projects.

        Uses the shared directory index so the drive is walked once for all tools,
        not walked again by each tool.

        Args:
            root_path: Root directory to search from (root drive for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        walk_for_tool_directories(
            root_path, root_path, ".clinerules",
            self._extract_rules_from_clinerules_directory, projects_by_root,
        )

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
            if rule_file.is_file():
                rule_info = self._extract_single_rule_file_with_root(rule_file)
                if rule_info:
                    project_root = rule_info.get('project_root')
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)

