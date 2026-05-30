"""GitHub Copilot rules extraction for Linux systems."""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseGitHubCopilotRulesExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...linux_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...macos_extraction_helpers import get_file_metadata, read_file_content

logger = logging.getLogger(__name__)


def find_github_copilot_project_root(rule_file: Path) -> Path:
    parent = rule_file.parent

    if parent.name == "prompts":
        if parent.parent.name == "User":
            return parent.parent

    if parent.name == "intellij":
        if parent.parent.name == "github-copilot":
            return parent.parent.parent.parent

    if parent.name == "copilot":
        if parent.parent.name == ".github":
            return parent.parent.parent

    if parent.name == ".github":
        return parent.parent

    return parent


class LinuxGitHubCopilotRulesExtractor(BaseGitHubCopilotRulesExtractor):
    """Extractor for GitHub Copilot rules on Linux systems."""

    JETBRAINS_IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell",
    ]

    def extract_all_github_copilot_rules(self, tool_name: str = None) -> List[Dict]:
        projects_by_root = {}

        tool_name_lower = tool_name.lower() if tool_name else ""

        if not tool_name or "vs code" in tool_name_lower or "vscode" in tool_name_lower:
            self._extract_global_vscode_rules(projects_by_root)

        if not tool_name or self._is_jetbrains_tool(tool_name):
            self._extract_global_jetbrains_rules(projects_by_root)

        self._extract_workspace_rules(projects_by_root)

        return build_project_list(projects_by_root)

    def _is_jetbrains_tool(self, tool_name: str) -> bool:
        if not tool_name:
            return False
        tool_name_lower = tool_name.lower()
        return any(pattern.lower() in tool_name_lower for pattern in self.JETBRAINS_IDE_PATTERNS)

    def _extract_global_vscode_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            vscode_prompts_path = user_home / ".config" / "Code" / "User" / "prompts"
            if vscode_prompts_path.exists() and vscode_prompts_path.is_dir():
                try:
                    for rule_file in vscode_prompts_path.glob("*.instructions.md"):
                        if rule_file.is_file():
                            rule_info = self._extract_rule_with_scope(
                                rule_file, find_github_copilot_project_root, scope="user"
                            )
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting GitHub Copilot VS Code rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_global_jetbrains_rules(self, projects_by_root: Dict) -> None:
        def extract_for_user(user_home: Path) -> None:
            jetbrains_rule_path = (
                user_home / ".config" / "github-copilot" / "intellij" / "global-copilot-instructions.md"
            )
            if jetbrains_rule_path.exists() and jetbrains_rule_path.is_file():
                try:
                    rule_info = self._extract_rule_with_scope(
                        jetbrains_rule_path, find_github_copilot_project_root, scope="user"
                    )
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting GitHub Copilot JetBrains rules for {user_home}: {e}")

        for user_home in get_linux_user_homes():
            try:
                extract_for_user(user_home)
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _extract_workspace_rules(self, projects_by_root: Dict) -> None:
        for user_home in get_linux_user_homes():
            try:
                self._walk_for_github_directories(
                    user_home, user_home, projects_by_root, current_depth=0
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

    def _walk_for_github_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict,
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
                        if item.name == ".github":
                            copilot_instructions = item / "copilot-instructions.md"
                            if copilot_instructions.exists() and copilot_instructions.is_file():
                                rule_info = self._extract_rule_with_scope(
                                    copilot_instructions,
                                    find_github_copilot_project_root,
                                    scope="project",
                                )
                                if rule_info:
                                    project_root = rule_info.get("project_root")
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                            self._extract_path_specific_instructions(item, projects_by_root)
                            continue

                        agents_md = item / "AGENTS.md"
                        if agents_md.exists() and agents_md.is_file():
                            rule_info = self._extract_rule_with_scope(
                                agents_md, find_github_copilot_project_root, scope="project"
                            )
                            if rule_info:
                                project_root = rule_info.get("project_root")
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)

                        if item.is_symlink():
                            continue
                        self._walk_for_github_directories(
                            root_path, item, projects_by_root, current_depth + 1
                        )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
        except (PermissionError, OSError):
            pass

    def _extract_path_specific_instructions(self, github_dir: Path, projects_by_root: Dict) -> None:
        copilot_dir = github_dir / "copilot"
        if not copilot_dir.exists() or not copilot_dir.is_dir():
            return
        try:
            for md_file in copilot_dir.glob("*.md"):
                if md_file.is_file():
                    rule_info = self._extract_rule_with_scope(
                        md_file, find_github_copilot_project_root, scope="project"
                    )
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading copilot directory {copilot_dir}: {e}")

    def _extract_rule_with_scope(self, rule_file: Path, find_project_root_func, scope: str) -> Dict:
        try:
            if not rule_file.exists() or not rule_file.is_file():
                return None
            file_metadata = get_file_metadata(rule_file)
            project_root = find_project_root_func(rule_file)
            content, truncated = read_file_content(rule_file, file_metadata["size"])
            return {
                "file_path": str(rule_file),
                "file_name": rule_file.name,
                "project_root": str(project_root) if project_root else None,
                "content": content,
                "size": file_metadata["size"],
                "last_modified": file_metadata["last_modified"],
                "truncated": truncated,
                "scope": scope,
            }
        except PermissionError as e:
            logger.warning(f"Permission denied reading {rule_file}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading rule file {rule_file}: {e}")
            return None
