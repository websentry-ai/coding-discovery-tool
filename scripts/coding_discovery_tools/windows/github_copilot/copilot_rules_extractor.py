import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseGitHubCopilotRulesExtractor
from ...constants import MAX_SEARCH_DEPTH, traverses_other_tool_config_dir
from ...claude_code_skills_helpers import is_user_level_claude_subdir
from ...windows_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    get_file_metadata,
    read_file_content,
    should_skip_path,
    is_running_as_admin,
    get_windows_system_directories,
)

logger = logging.getLogger(__name__)

# Windows system directories to skip during searches
WINDOWS_SYSTEM_DIRS = get_windows_system_directories()


def find_github_copilot_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a GitHub Copilot rule file.

    For GitHub Copilot rules:
    - Global VS Code rules in %APPDATA%\\Code\\User\\prompts\\ -> User AppData\\Roaming
    - Global JetBrains rules in %LOCALAPPDATA%\\github-copilot\\intellij\\ -> User AppData\\Local
    - Workspace rules anywhere under .github\\ (e.g. .github\\instructions\\**,
      .github\\prompts\\) -> parent of the nearest .github ancestor (project root)
    - AGENTS.md at project root -> parent directory (via default fallback)

    Args:
        rule_file: Path to the rule file

    Returns:
        Project root path
    """
    parent = rule_file.parent

    # VS Code global rules in prompts directory
    if parent.name == "prompts":
        if parent.parent.name == "User":
            return parent.parent

    # JetBrains global rules in intellij directory
    if parent.name == "intellij":
        if parent.parent.name == "github-copilot":
            return parent.parent

    # Workspace/user rules nested under a config dir — .github (instructions/
    # prompts), .claude (Claude-format rules), or .copilot (user instructions).
    # Walk to the nearest such ancestor and return its parent (project root or
    # user home).
    for ancestor in rule_file.parents:
        if ancestor.name in (".github", ".claude", ".copilot"):
            return ancestor.parent

    return parent


class WindowsGitHubCopilotRulesExtractor(BaseGitHubCopilotRulesExtractor):
    """Extractor for GitHub Copilot rules on Windows systems."""

    JETBRAINS_IDE_PATTERNS = [
        "IntelliJ", "PyCharm", "WebStorm", "PhpStorm", "GoLand",
        "Rider", "CLion", "RustRover", "RubyMine", "DataGrip", "DataSpell"
    ]

    def extract_all_github_copilot_rules(self, tool_name: str = None) -> List[Dict]:
        """
        Extract GitHub Copilot rules from all projects on Windows.
        """
        projects_by_root = {}

        tool_name_lower = tool_name.lower() if tool_name else ""

        # Extract global rules based on tool type
        if not tool_name or "vs code" in tool_name_lower or "vscode" in tool_name_lower:
            self._extract_global_vscode_rules(projects_by_root)

        if not tool_name or self._is_jetbrains_tool(tool_name):
            self._extract_global_jetbrains_rules(projects_by_root)

        # Extract workspace rules
        root_path = Path("C:\\")
        logger.info(f"Searching for GitHub Copilot workspace rules from root: {root_path}")
        self._extract_workspace_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    def _is_jetbrains_tool(self, tool_name: str) -> bool:
        """
        Check if the tool name corresponds to any JetBrains IDE.

        """
        if not tool_name:
            return False

        tool_name_lower = tool_name.lower()

        for pattern in self.JETBRAINS_IDE_PATTERNS:
            if pattern.lower() in tool_name_lower:
                return True

        return False

    def _extract_global_vscode_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global GitHub Copilot rules from VS Code.

        Location: %APPDATA%\\Code\\User\\prompts\\*.instructions.md

        """
        def add_user_rules(directory: Path, patterns) -> None:
            """Collect each ``patterns`` match under ``directory`` as a user rule."""
            try:
                if not directory.is_dir():
                    return
                rule_files = []
                for pattern in patterns:
                    rule_files += list(directory.glob(pattern))
                for rule_file in rule_files:
                    if not rule_file.is_file():
                        continue
                    rule_info = self._extract_rule_with_scope(
                        rule_file, find_github_copilot_project_root, scope="user"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                            logger.debug(f"Found VS Code global rule: {rule_file}")
            except Exception as e:
                logger.debug(f"Error extracting GitHub Copilot user rules from {directory}: {e}")

        def extract_for_user(user_home: Path) -> None:
            """Extract global VS Code rules for a specific user.

            Default user-profile instruction locations per the VS Code Copilot
            custom-instructions docs: the VS Code User prompts dir (instructions +
            prompt files), ``~/.copilot/instructions`` (Copilot format), and
            ``~/.claude/rules`` (Claude format).
            """
            add_user_rules(
                user_home / "AppData" / "Roaming" / "Code" / "User" / "prompts",
                ("*.instructions.md", "*.prompt.md"),
            )
            add_user_rules(user_home / ".copilot" / "instructions", ("**/*.instructions.md",))
            add_user_rules(user_home / ".claude" / "rules", ("**/*.md",))

        if is_running_as_admin():
            self._scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _extract_global_jetbrains_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global GitHub Copilot rules from JetBrains IDEs.

        Location: %LOCALAPPDATA%\\github-copilot\\intellij\\global-copilot-instructions.md

        These rules are STRICTLY for JetBrains IDEs only and will not be reported
        for VS Code.
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global JetBrains rules for a specific user."""
            # %LOCALAPPDATA% = user_home\\AppData\\Local
            jetbrains_rule_path = (
                user_home / "AppData" / "Local" / "github-copilot" / "intellij" / "global-copilot-instructions.md"
            )

            if jetbrains_rule_path.exists() and jetbrains_rule_path.is_file():
                try:
                    rule_info = self._extract_rule_with_scope(
                        jetbrains_rule_path,
                        find_github_copilot_project_root,
                        scope="user"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
                            logger.debug(f"Found JetBrains global rule: {jetbrains_rule_path}")
                except Exception as e:
                    logger.debug(f"Error extracting GitHub Copilot JetBrains rules for {user_home}: {e}")

        if is_running_as_admin():
            self._scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _scan_user_directories(self, extract_func) -> None:
        """
        Scan all user directories and call the extract function for each.
        """
        users_dir = Path("C:\\Users")
        if not users_dir.exists():
            return

        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                if user_dir.name.lower() in ['public', 'default', 'default user', 'all users']:
                    continue
                try:
                    extract_func(user_dir)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")
                    continue

    def _extract_workspace_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level workspace rules recursively from all projects.
        """
        try:
            top_level_dirs = self._get_top_level_directories(root_path)

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
            self._extract_workspace_rules_from_home(home_path, projects_by_root)

    def _get_top_level_directories(self, root_path: Path) -> List[Path]:
        """
        Get top-level directories to search, skipping Windows system directories.
        """
        dirs = []
        try:
            for item in root_path.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    if item.name in WINDOWS_SYSTEM_DIRS:
                        continue
                    dirs.append(item)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error listing {root_path}: {e}")

        return dirs

    def _extract_workspace_rules_from_home(self, home_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Fallback method to search from home directory.
        """
        for github_dir in home_path.rglob(".github"):
            try:
                if should_skip_path(github_dir, WINDOWS_SYSTEM_DIRS):
                    continue

                copilot_instructions = github_dir / "copilot-instructions.md"
                if copilot_instructions.exists() and copilot_instructions.is_file():
                    rule_info = self._extract_rule_with_scope(
                        copilot_instructions,
                        find_github_copilot_project_root,
                        scope="project"
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
                    if should_skip_path(item, WINDOWS_SYSTEM_DIRS):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if item.is_dir():
                        if item.name == ".github":
                            # Check copilot-instructions.md
                            copilot_instructions = item / "copilot-instructions.md"
                            if copilot_instructions.exists() and copilot_instructions.is_file():
                                rule_info = self._extract_rule_with_scope(
                                    copilot_instructions,
                                    find_github_copilot_project_root,
                                    scope="project"
                                )
                                if rule_info:
                                    project_root = rule_info.get('project_root')
                                    if project_root:
                                        add_rule_to_project(rule_info, project_root, projects_by_root)
                                        logger.debug(f"Found workspace rule: {copilot_instructions}")
                            # Check path-specific instructions in .github/instructions/
                            self._extract_path_specific_instructions(item, projects_by_root)
                            # Check reusable prompt files in .github/prompts/
                            self._extract_prompt_files(item, projects_by_root)
                            continue

                        if item.name == ".claude":
                            # Workspace (Claude format) instructions: .claude/rules/**/*.md
                            self._extract_claude_rules(item, projects_by_root)
                            continue

                        # Check AGENTS.md at project root level
                        agents_md = item / "AGENTS.md"
                        if agents_md.exists() and agents_md.is_file():
                            rule_info = self._extract_rule_with_scope(
                                agents_md,
                                find_github_copilot_project_root,
                                scope="project"
                            )
                            if rule_info:
                                project_root = rule_info.get('project_root')
                                if project_root:
                                    add_rule_to_project(rule_info, project_root, projects_by_root)

                        if item.is_symlink():
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

    def _extract_path_specific_instructions(
        self, github_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract path-specific instructions from .github\\instructions\\**.

        These are VS Code path-scoped custom instructions (``*.instructions.md``)
        that apply to specific file patterns within a project. Per the docs they
        live under ``.github/instructions/`` and may be nested in subdirectories,
        so the search is recursive (``rglob``) and depth-gated.

        Args:
            github_dir: Path to the .github directory
            projects_by_root: Dict to populate with rule info
        """
        instructions_dir = github_dir / "instructions"
        if not instructions_dir.exists() or not instructions_dir.is_dir():
            return

        try:
            for rule_file in instructions_dir.rglob("*.instructions.md"):
                try:
                    if len(rule_file.relative_to(github_dir).parts) > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue
                if rule_file.is_file():
                    rule_info = self._extract_rule_with_scope(
                        rule_file,
                        find_github_copilot_project_root,
                        scope="project"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading instructions directory {instructions_dir}: {e}")

    def _extract_prompt_files(
        self, github_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract reusable prompt files from .github\\prompts\\*.prompt.md.

        These are VS Code prompt files scoped to the project. Per the docs they
        live directly under ``.github/prompts/`` (non-recursive). They are
        emitted as ordinary project-scoped rules — the backend ingests them via
        the same allowlisted rule shape as instructions; no extra fields.

        Args:
            github_dir: Path to the .github directory
            projects_by_root: Dict to populate with rule info
        """
        prompts_dir = github_dir / "prompts"
        if not prompts_dir.exists() or not prompts_dir.is_dir():
            return

        try:
            for rule_file in prompts_dir.glob("*.prompt.md"):
                if rule_file.is_file():
                    rule_info = self._extract_rule_with_scope(
                        rule_file,
                        find_github_copilot_project_root,
                        scope="project"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading prompts directory {prompts_dir}: {e}")

    def _extract_claude_rules(
        self, claude_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """
        Extract workspace (Claude-format) instructions from .claude\\rules\\**\\*.md.

        VS Code Copilot reads project instructions from the ``.claude/rules``
        folder (recursively) per the custom-instructions docs. Emitted as ordinary
        project-scoped rules (same allowlisted shape as .github instructions).

        Args:
            claude_dir: Path to the .claude directory
            projects_by_root: Dict to populate with rule info
        """
        rules_dir = claude_dir / "rules"
        if not rules_dir.exists() or not rules_dir.is_dir():
            return

        # The user-home ~/.claude/rules is collected as USER scope in
        # _extract_global_vscode_rules; don't also collect it here as project
        # scope (add_rule_to_project does not dedupe).
        if is_user_level_claude_subdir(rules_dir):
            return
        # Don't pull rules out of another tool's bundled config dir, e.g. an
        # installed extension package's .claude under a tool's extensions dir.
        if traverses_other_tool_config_dir(claude_dir, allow={".claude"}):
            return

        try:
            for rule_file in rules_dir.rglob("*.md"):
                try:
                    if len(rule_file.relative_to(claude_dir).parts) > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue
                if rule_file.is_file():
                    rule_info = self._extract_rule_with_scope(
                        rule_file,
                        find_github_copilot_project_root,
                        scope="project"
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading claude rules directory {rules_dir}: {e}")

    def _extract_rule_with_scope(
        self,
        rule_file: Path,
        find_project_root_func,
        scope: str
    ) -> Dict:
        """
        Extract a single rule file with metadata including scope.

        Args:
            rule_file: Path to the rule file
            find_project_root_func: Function to find project root
            scope: Scope of the rule ("user" for global rules, "project" for workspace rules)

        Returns:
            Dict with file info including scope, or None if extraction fails
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
                "truncated": truncated,
                "scope": scope
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
