"""
Gemini CLI rules extraction for macOS systems.

Extracts Gemini CLI configuration files (GEMINI.md) from:
- Global context: ~/.gemini/GEMINI.md
- Project context: GEMINI.md in current working directory or any parent directory
- Sub-directory context: GEMINI.md files in subdirectories
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseGeminiCliRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_gemini_cli_project_root,
    should_process_file,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)


class MacOSGeminiCliRulesExtractor(BaseGeminiCliRulesExtractor):
    """Extractor for Gemini CLI rules on macOS systems."""

    def extract_all_gemini_cli_rules(self) -> List[Dict]:
        """
        Extract all Gemini CLI rules from all projects on macOS.
        
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
        
        logger.info(f"Searching for Gemini CLI rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global Gemini CLI rules from ~/.gemini/GEMINI.md.
        
        When running as root, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules for a specific user."""
            global_rules_path = user_home / ".gemini" / "GEMINI.md"
            
            if global_rules_path.exists() and global_rules_path.is_file():
                try:
                    if should_process_file(global_rules_path, user_home):
                        rule_info = extract_single_rule_file(global_rules_path, find_gemini_cli_project_root)
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting global Gemini CLI rules for {user_home}: {e}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Searches for GEMINI.md files in:
        - Current working directory
        - Parent directories
        - Subdirectories
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        from ...macos_extraction_helpers import should_skip_path, should_skip_system_path, get_top_level_directories
        
        def extract_gemini_md_file(gemini_md_file: Path) -> None:
            """Extract a single GEMINI.md file if it should be processed."""
            try:
                # Skip if in .gemini directory (we handle global rules separately)
                if gemini_md_file.parent.name == ".gemini":
                    return
                
                # Check if we should process this file
                if should_process_file(gemini_md_file, gemini_md_file.parent):
                    rule_info = extract_single_rule_file(gemini_md_file, find_gemini_cli_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting GEMINI.md from {gemini_md_file}: {e}")
        
        def process_gemini_md_files(search_path: Path, base_path: Path) -> None:
            """Process all GEMINI.md files found in search_path."""
            try:
                for gemini_md_file in search_path.rglob("GEMINI.md"):
                    try:
                        # Check depth
                        try:
                            depth = len(gemini_md_file.relative_to(base_path).parts)
                            if depth > MAX_SEARCH_DEPTH:
                                continue
                        except ValueError:
                            continue
                        
                        # Skip if path contains directories we should skip
                        if should_skip_path(gemini_md_file) or should_skip_system_path(gemini_md_file):
                            continue
                        
                        extract_gemini_md_file(gemini_md_file)
                    except (PermissionError, OSError):
                        continue
                    except Exception as e:
                        logger.debug(f"Error processing {gemini_md_file}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.debug(f"Error searching for GEMINI.md files in {search_path}: {e}")
        
        # When searching from root, iterate top-level directories first to avoid system paths
        if root_path == Path("/"):
            try:
                # Get top-level directories, skipping system ones
                top_level_dirs = get_top_level_directories(root_path)
                
                # Search each top-level directory (like /Users, /opt, etc.)
                for top_dir in top_level_dirs:
                    try:
                        process_gemini_md_files(top_dir, root_path)
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
            # For non-root paths, use rglob to find all GEMINI.md files
            process_gemini_md_files(root_path, root_path)

