"""
Claude Code rules extraction for Linux.

Same logic as macOS but uses /home/ instead of /Users/ for user directories,
and skips macOS-only managed-settings paths.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...claude_rules_helpers import (
    is_claude_md_file,
    is_claude_local_md_file,
    build_rules_project_list,
    extract_user_rules_from_rules_directory,
    extract_rules_from_rules_directory,
)
from ...coding_tool_base import BaseClaudeRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    add_rule_to_project,
    extract_and_add_rule,
    extract_single_rule_file,
    find_claude_project_root,
    get_linux_user_homes,
    is_user_level_tool_dir,
    should_process_directory,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)

logger = logging.getLogger(__name__)

CLAUDE_DIR_NAME = ".claude"


class LinuxClaudeRulesExtractor(BaseClaudeRulesExtractor):
    """Extractor for Claude Code rules on Linux systems."""

    def extract_all_claude_rules(self) -> Dict:
        user_rules: List[Dict] = []
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_level_rules(user_rules)
        self._extract_project_level_rules(projects_by_root)

        return {
            "managed_rules": [],
            "user_rules": user_rules,
            "project_rules": build_rules_project_list(projects_by_root),
        }

    def _extract_user_level_rules(self, user_rules: List[Dict]) -> None:
        def extract_for_user(user_home: Path) -> None:
            claude_dir = user_home / CLAUDE_DIR_NAME
            if not claude_dir.exists() or not claude_dir.is_dir():
                return
            try:
                for item in claude_dir.iterdir():
                    if item.is_file() and is_claude_md_file(item.name):
                        rule_info = extract_single_rule_file(
                            item, find_claude_project_root, scope="user"
                        )
                        if rule_info:
                            rule_info["project_path"] = rule_info.pop("project_root", None)
                            user_rules.append(rule_info)

                extract_user_rules_from_rules_directory(
                    claude_dir / "rules",
                    extract_single_rule_file,
                    find_claude_project_root,
                    user_rules,
                )
            except Exception as e:
                logger.debug(f"Error extracting user rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_project_level_rules(
        self, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Walk each user home recursively for project-level Claude rule files."""
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_claude_files(
                    user_home, user_home, projects_by_root, current_depth=0
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_claude_files(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
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
                        if item.name == CLAUDE_DIR_NAME:
                            if is_user_level_tool_dir(item):
                                continue
                            self._extract_rules_from_claude_directory(item, projects_by_root)
                            continue
                        if item.is_symlink():
                            continue
                        self._walk_for_claude_files(
                            root_path, item, projects_by_root, current_depth + 1
                        )
                    elif item.is_file():
                        if item.name == ".clauderules" or is_claude_md_file(item.name):
                            if should_process_file(item, root_path):
                                extract_and_add_rule(
                                    item, find_claude_project_root, add_rule_to_project,
                                    projects_by_root, scope="project",
                                )
                        elif is_claude_local_md_file(item.name):
                            if should_process_file(item, root_path):
                                extract_and_add_rule(
                                    item, find_claude_project_root, add_rule_to_project,
                                    projects_by_root, scope="local",
                                )
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass

    def _extract_rules_from_claude_directory(
        self, claude_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        try:
            clauderules_file = claude_dir / ".clauderules"
            if clauderules_file.exists() and clauderules_file.is_file():
                extract_and_add_rule(
                    clauderules_file, find_claude_project_root, add_rule_to_project,
                    projects_by_root, scope="project",
                )
            for item in claude_dir.iterdir():
                if item.is_file() and is_claude_md_file(item.name):
                    extract_and_add_rule(
                        item, find_claude_project_root, add_rule_to_project,
                        projects_by_root, scope="project",
                    )

            def _extract_and_add(file_path, find_root_func, pbr, scope="project"):
                extract_and_add_rule(file_path, find_root_func, add_rule_to_project, pbr, scope=scope)

            extract_rules_from_rules_directory(
                claude_dir / "rules", find_claude_project_root,
                _extract_and_add, projects_by_root, scope="project",
            )
        except Exception as e:
            logger.debug(f"Error extracting rules from {claude_dir}: {e}")
