import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseGitHubCopilotRulesExtractor
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
    get_file_metadata,
    read_file_content,
)

logger = logging.getLogger(__name__)


def find_github_copilot_project_root(rule_file: Path) -> Path:
    """
    For GitHub Copilot rules:
    - Global VS Code rules in ~/Library/Application Support/Code/User/prompts/ -> User directory
    - Global JetBrains rules in ~/.config/github-copilot/intellij/ -> User home directory
    - Workspace rules in .github/ -> parent of .github (project root)
    """
    parent = rule_file.parent

    if parent.name == "prompts":
        if parent.parent.name == "User":
            return parent.parent

    if parent.name == "intellij":
        if parent.parent.name == "github-copilot":
            return parent.parent.parent.parent

    # Workspace rules in .github/ directory
    if parent.name == ".github":
        return parent.parent

    return parent


class MacOSGitHubCopilotRulesExtractor(BaseGitHubCopilotRulesExtractor):
    """Extractor for GitHub Copilot rules on macOS systems."""

    JETBRAINS_IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell"
    ]

    def extract_all_github_copilot_rules(self, tool_name: str = None) -> List[Dict]:
        """
        Extract GitHub Copilot rules from all projects on macOS.
        """
        projects_by_root = {}

        tool_name_lower = tool_name.lower() if tool_name else ""

        # Extract global rules based on tool type
        if not tool_name or "vs code" in tool_name_lower or "vscode" in tool_name_lower:
            self._extract_global_vscode_rules(projects_by_root)

        # Check if this is any JetBrains IDE
        if not tool_name or self._is_jetbrains_tool(tool_name):
            self._extract_global_jetbrains_rules(projects_by_root)

        root_path = Path("/")

        logger.info(f"Searching for GitHub Copilot workspace rules from root: {root_path}")
        self._extract_workspace_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _is_jetbrains_tool(self, tool_name: str) -> bool:
        """
        Check if the tool name corresponds to any JetBrains IDE.

        Args:
            tool_name: Name of the tool (e.g., "GitHub Copilot PyCharm", "GitHub Copilot IntelliJ IDEA")

        Returns:
            True if the tool name contains any JetBrains IDE pattern
        """
        if not tool_name:
            return False

        tool_name_lower = tool_name.lower()

        # Check against all known JetBrains IDE patterns
        for pattern in self.JETBRAINS_IDE_PATTERNS:
            if pattern.lower() in tool_name_lower:
                return True

        return False

    def _extract_global_vscode_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global GitHub Copilot rules from VS Code.
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global VS Code rules for a specific user."""
            vscode_prompts_path = (
                user_home / "Library" / "Application Support" / "Code" / "User" / "prompts"
            )

            if vscode_prompts_path.exists() and vscode_prompts_path.is_dir():
                try:
                    for rule_file in vscode_prompts_path.glob("*.instructions.md"):
                        if rule_file.is_file():
                            rule_info = self._extract_rule_with_scope(
                                rule_file,
                                find_github_copilot_project_root,
                                scope="Global"
                            )
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting GitHub Copilot VS Code rules for {user_home}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_global_jetbrains_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global GitHub Copilot rules from JetBrains IDEs.
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global JetBrains rules for a specific user."""
            jetbrains_rule_path = (
                user_home / ".config" / "github-copilot" / "intellij" / "global-copilot-instructions.md"
            )

            if jetbrains_rule_path.exists() and jetbrains_rule_path.is_file():
                try:
                    rule_info = self._extract_rule_with_scope(
                        jetbrains_rule_path,
                        find_github_copilot_project_root,
                        scope="Global"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                except Exception as e:
                    logger.debug(f"Error extracting GitHub Copilot JetBrains rules for {user_home}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_workspace_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level workspace rules recursively from all projects.
        """
        if root_path == Path("/"):
            try:
                top_level_dirs = get_top_level_directories(root_path)
                for top_dir in top_level_dirs:
                    try:
                        self._walk_for_github_directories(root_path, top_dir, projects_by_root, current_depth=1)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
                logger.info("Falling back to home directory search")
                home_path = Path.home()
                self._extract_workspace_rules(home_path, projects_by_root)
        else:
            for github_dir in root_path.rglob(".github"):
                try:
                    if not should_process_directory(github_dir, root_path):
                        continue

                    copilot_instructions = github_dir / "copilot-instructions.md"
                    if copilot_instructions.exists() and copilot_instructions.is_file():
                        rule_info = self._extract_rule_with_scope(
                            copilot_instructions,
                            find_github_copilot_project_root,
                            scope="Workspace"
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {github_dir}: {e}")
                    continue

    def _walk_for_github_directories(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .github directories.
        """
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
                                    scope="Workspace"
                                )
                                if rule_info:
                                    project_root = rule_info.get('project_root')
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                            continue
                        self._walk_for_github_directories(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue

        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_rule_with_scope(
        self,
        rule_file: Path,
        find_project_root_func,
        scope: str
    ) -> Dict:
        """
        Extract a single rule file with metadata.
        """
        try:
            if not rule_file.exists() or not rule_file.is_file():
                return None

            file_metadata = get_file_metadata(rule_file)
            project_root = find_project_root_func(rule_file)
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
