"""
Claude Code rules extraction for macOS systems.

Extracts Claude Code configuration files (.clauderules and claude.md) from all projects
on the user's machine, grouping them by project root.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseClaudeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    find_claude_project_root,
    get_top_level_directories,
    should_process_directory,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)


class MacOSClaudeRulesExtractor(BaseClaudeRulesExtractor):
    """Extractor for Claude Code rules on macOS systems."""

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on macOS.
        
        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root = {}

        # Extract project-level rules from system root (for MDM deployment)
        root_path = Path("/")
        
        logger.info(f"Searching for Claude rules from root: {root_path}")
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
                        self._walk_for_claude_files(root_path, top_dir, projects_by_root, current_depth=1)
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
            # Search for .clauderules files (current format)
            for claude_file in root_path.rglob(".clauderules"):
                try:
                    if not should_process_file(claude_file, root_path):
                        continue

                    rule_info = extract_single_rule_file(claude_file, find_claude_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {claude_file}: {e}")
                    continue

            # Search for .claude directories
            for claude_dir in root_path.rglob(".claude"):
                try:
                    if not should_process_directory(claude_dir, root_path):
                        continue

                    # Extract .clauderules from .claude directory
                    self._extract_rules_from_claude_directory(claude_dir, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {claude_dir}: {e}")
                    continue

            # Search for legacy claude.md files
            for claude_file in root_path.rglob("claude.md"):
                try:
                    if not should_process_file(claude_file, root_path):
                        continue

                    rule_info = extract_single_rule_file(claude_file, find_claude_project_root)
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {claude_file}: {e}")
                    continue

    def _walk_for_claude_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for Claude rule files.
        
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
                        # Found a .claude directory!
                        if item.name == ".claude":
                            # Extract rules from this .claude directory
                            self._extract_rules_from_claude_directory(item, projects_by_root)
                            # Don't recurse into .claude directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_claude_files(root_path, item, projects_by_root, current_depth + 1)
                    elif item.is_file():
                        # Check for .clauderules or claude.md files
                        if item.name == ".clauderules" or item.name == "claude.md":
                            if should_process_file(item, root_path):
                                rule_info = extract_single_rule_file(item, find_claude_project_root)
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

    def _extract_rules_from_claude_directory(self, claude_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .claude directory.
        
        Args:
            claude_dir: Path to .claude directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # Extract .clauderules from .claude directory (current format)
        clauderules_file = claude_dir / ".clauderules"
        if clauderules_file.exists() and clauderules_file.is_file():
            rule_info = extract_single_rule_file(clauderules_file, find_claude_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)

        # Extract legacy claude.md from .claude directory
        legacy_file = claude_dir / "claude.md"
        if legacy_file.exists() and legacy_file.is_file():
            rule_info = extract_single_rule_file(legacy_file, find_claude_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
