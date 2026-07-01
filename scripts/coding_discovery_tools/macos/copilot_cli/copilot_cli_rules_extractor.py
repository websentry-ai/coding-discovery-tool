"""
GitHub Copilot CLI rules/instructions extraction for macOS.

This is for the standalone ``@github/copilot`` CLI (config under ``~/.copilot/``),
a distinct product from the GitHub Copilot IDE extension/plugin — so it does NOT
reuse the IDE ``MacOSGitHubCopilotRulesExtractor`` (which reads the wrong
``.github/copilot/`` path). It mirrors the single-product rules extractors
(Codex / Gemini CLI) and reuses the shared helpers in
``macos_extraction_helpers``.

Sources detected (all docs-verified against the Copilot CLI custom-instructions
docs):

  - G1 (global, scope "user"):  ``<config_dir>/copilot-instructions.md``
  - G2 (global, scope "user"):  ``<config_dir>/instructions/**/*.instructions.md``
  - P1 (project, scope "project"): repo-root ``.github/copilot-instructions.md``
  - P2 (project, scope "project"): ``.github/instructions/**/*.instructions.md``
  - P3 (project, scope "project"): repo-root ``AGENTS.md`` / ``CLAUDE.md`` /
        ``GEMINI.md`` (root only — not nested)
  - E1 (scope "user", current user only): each dir listed in
        ``COPILOT_CUSTOM_INSTRUCTIONS_DIRS`` contributes ``AGENTS.md`` and
        ``.github/instructions/**/*.instructions.md``

``<config_dir>`` is resolved via ``_resolve_copilot_dir`` (honors ``COPILOT_HOME``
for the running user; falls back to ``<user_home>/.copilot`` for others). Global
rules are grouped under ``<config_dir>`` as their ``project_root`` so they
coalesce with the CLI's MCP servers (which key on the same dir) into one project.

IMPORTANT (backend contract): rule dicts are built ONLY via the shared
``extract_single_rule_file`` helper with an EXPLICIT ``scope`` — no frontmatter
(``applyTo`` / ``excludeAgent``) is ever parsed into the dict, because the backend
silently drops any rule carrying a key outside its 8-field allowlist. The
frontmatter stays verbatim inside ``content``.
"""

import logging
import os
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseCopilotCliRulesExtractor
from ...constants import MAX_SEARCH_DEPTH, traverses_other_tool_config_dir
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_single_rule_file,
    get_top_level_directories,
    is_running_as_root,
    scan_user_directories,
    should_skip_path,
    should_skip_system_path,
)
from .copilot_cli import _resolve_copilot_dir

logger = logging.getLogger(__name__)

# Global (user-scope) instruction file at the config-dir root.
GLOBAL_INSTRUCTIONS_FILENAME = "copilot-instructions.md"
# Directory (under the config dir AND under a project's .github) holding
# recursive ``*.instructions.md`` files.
INSTRUCTIONS_DIR_NAME = "instructions"
INSTRUCTIONS_FILE_SUFFIX = ".instructions.md"
# Project-root agent files Copilot CLI reads — repo root ONLY, not nested.
PROJECT_ROOT_RULE_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
GITHUB_DIR_NAME = ".github"
# Repo-root marker: the CLI loads root AGENTS.md/CLAUDE.md/GEMINI.md from the
# repository root, which is canonically the directory holding ``.git``. Gating
# P3 on this presence is what keeps a NESTED AGENTS.md (e.g. in a subpackage)
# from being mis-collected as if it were a project-root rule.
GIT_DIR_NAME = ".git"
PROJECT_INSTRUCTIONS_FILENAME = "copilot-instructions.md"
# Env var (current user only) listing extra instruction dirs.
CUSTOM_INSTRUCTIONS_DIRS_ENV = "COPILOT_CUSTOM_INSTRUCTIONS_DIRS"


def _make_fixed_root_finder(project_root: Path):
    """Return a ``find_project_root_func`` that always yields ``project_root``.

    Global / env rules are deliberately grouped under one fixed root (the config
    dir, or an env-listed dir) regardless of how deeply nested the file is, so
    they coalesce with that root's MCP servers. ``extract_single_rule_file``
    requires a root-finder callable, so we adapt the fixed value to that shape.
    """
    def _finder(_rule_file: Path) -> Path:
        return project_root
    return _finder


def _find_github_project_root(rule_file: Path) -> Path:
    """Project root for a file under a repo's ``.github/`` tree.

    ``.github/copilot-instructions.md`` -> parent of ``.github``.
    ``.github/instructions/<...>/x.instructions.md`` -> parent of ``.github``.
    """
    for ancestor in rule_file.parents:
        if ancestor.name == GITHUB_DIR_NAME:
            return ancestor.parent
    return rule_file.parent


def _find_self_dir_root(rule_file: Path) -> Path:
    """Project root for a repo-root rule file -> the directory containing it."""
    return rule_file.parent


class MacOSCopilotCliRulesExtractor(BaseCopilotCliRulesExtractor):
    """Extractor for GitHub Copilot CLI rules on macOS systems."""

    def extract_all_copilot_cli_rules(self) -> List[Dict]:
        """
        Extract all GitHub Copilot CLI rules from all projects on macOS.

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root: Dict[str, List[Dict]] = {}

        # Global (user-scope) rules: G1, G2 — root-aware (all users when root).
        self._extract_global_rules(projects_by_root)

        # Env-listed (user-scope) rules: E1 — current user only.
        self._extract_env_custom_instructions(projects_by_root)

        # Project (project-scope) rules: P1, P2, P3 — recursive walk.
        self._extract_project_level_rules(self._filesystem_root(), projects_by_root)

        return build_project_list(projects_by_root)

    # -- Global (user-scope) -------------------------------------------------

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract G1 + G2 global rules from the resolved config dir.

        When running as root, scans every user's home; otherwise the current
        user only. Each user's config dir becomes the ``project_root`` so global
        rules coalesce with that user's CLI MCP servers.
        """
        def extract_for_user(user_home: Path) -> None:
            try:
                config_dir = _resolve_copilot_dir(user_home)
                root_finder = _make_fixed_root_finder(config_dir)

                # G1: <config_dir>/copilot-instructions.md
                self._add_rule_file(
                    config_dir / GLOBAL_INSTRUCTIONS_FILENAME,
                    root_finder,
                    "user",
                    projects_by_root,
                )

                # G2: <config_dir>/instructions/**/*.instructions.md
                self._add_instructions_tree(
                    config_dir / INSTRUCTIONS_DIR_NAME,
                    root_finder,
                    "user",
                    projects_by_root,
                )
            except Exception as e:
                logger.debug(f"Error extracting global Copilot CLI rules for {user_home}: {e}")

        self._scan_all_user_homes(extract_for_user)

    # -- Env-listed (user-scope) --------------------------------------------

    def _extract_env_custom_instructions(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract E1 dirs from ``COPILOT_CUSTOM_INSTRUCTIONS_DIRS``.

        The env var reflects ONLY the running user's shell, so this applies to
        the current user alone (skipped during a root all-users scan, where each
        other user's env is not visible). Each listed dir contributes its
        ``AGENTS.md`` and ``.github/instructions/**/*.instructions.md``, grouped
        under that dir as ``project_root``.
        """
        try:
            if self._is_privileged():
                return
            raw = os.environ.get(CUSTOM_INSTRUCTIONS_DIRS_ENV) or ""
            for entry in raw.split(","):
                name = entry.strip()
                if not name:
                    continue
                try:
                    custom_dir = Path(os.path.expandvars(os.path.expanduser(name)))
                    root_finder = _make_fixed_root_finder(custom_dir)

                    # AGENTS.md at the custom dir root.
                    self._add_rule_file(
                        custom_dir / "AGENTS.md",
                        root_finder,
                        "user",
                        projects_by_root,
                    )
                    # .github/instructions/**/*.instructions.md under the dir.
                    self._add_instructions_tree(
                        custom_dir / GITHUB_DIR_NAME / INSTRUCTIONS_DIR_NAME,
                        root_finder,
                        "user",
                        projects_by_root,
                    )
                except Exception as e:
                    logger.debug(f"Error reading custom instructions dir '{name}': {e}")
        except Exception as e:
            logger.debug(f"Error processing {CUSTOM_INSTRUCTIONS_DIRS_ENV}: {e}")

    # -- Project (project-scope) --------------------------------------------

    def _extract_project_level_rules(
        self, root_path: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Walk for project-level rules (P1, P2, P3) from the filesystem root."""
        if root_path == self._filesystem_root():
            try:
                for top_dir in self._iter_top_level_dirs(root_path):
                    try:
                        self._walk_for_project_rules(root_path, top_dir, projects_by_root, current_depth=1)
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
        else:
            self._walk_for_project_rules(root_path, root_path, projects_by_root, current_depth=0)

    def _walk_for_project_rules(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recursively walk looking for ``.github`` dirs and repo-root rule files.

        Symlinked directories are skipped (customer machines: loop/perf risk).
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    if self._should_skip(item):
                        continue

                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue

                    if not item.is_dir():
                        continue

                    if item.name == GITHUB_DIR_NAME:
                        # P1 + P2 live under .github; handle here, don't recurse in.
                        self._extract_github_dir_rules(item, projects_by_root)
                        continue

                    if item.is_symlink():
                        continue

                    # P3: repo-root agent files in this directory.
                    self._extract_project_root_files(item, projects_by_root)

                    self._walk_for_project_rules(root_path, item, projects_by_root, current_depth + 1)

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_github_dir_rules(
        self, github_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract P1 (.github/copilot-instructions.md) + P2 (.github/instructions/**)."""
        # P1
        self._add_rule_file(
            github_dir / PROJECT_INSTRUCTIONS_FILENAME,
            _find_github_project_root,
            "project",
            projects_by_root,
        )
        # P2
        self._add_instructions_tree(
            github_dir / INSTRUCTIONS_DIR_NAME,
            _find_github_project_root,
            "project",
            projects_by_root,
        )

    def _extract_project_root_files(
        self, project_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract P3 repo-root AGENTS.md / CLAUDE.md / GEMINI.md.

        All three are doc-valid Copilot CLI instruction files (GitHub Copilot CLI
        custom-instructions reference), so all are collected. But repos commonly
        symlink ``AGENTS.md -> CLAUDE.md`` (or keep byte-identical copies), which
        would emit the SAME instruction content as two rule rows. Dedupe within
        this repo root by ``realpath`` (symlink) and by content (copy) so shared
        content is reported once.

        Only fires when ``project_dir`` is a repository root (holds ``.git``), so
        a NESTED AGENTS.md inside a subdirectory is intentionally NOT collected.
        """
        try:
            if not (project_dir / GIT_DIR_NAME).is_dir():
                return
        except OSError:
            return

        seen_realpaths = set()
        seen_contents = set()
        for file_name in PROJECT_ROOT_RULE_FILES:
            rule_file = project_dir / file_name
            try:
                if not rule_file.is_file():
                    continue
                real = os.path.realpath(rule_file)
                if real in seen_realpaths:
                    logger.debug(f"Skipping {rule_file}: symlink/realpath dup of a collected root rule")
                    continue
                rule_info = extract_single_rule_file(rule_file, _find_self_dir_root, scope="project")
                if not rule_info:
                    continue
                content = rule_info.get("content")
                if content is not None and content in seen_contents:
                    logger.debug(f"Skipping {rule_file}: identical content to a collected root rule")
                    continue
                project_root = rule_info.get("project_root")
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
                    seen_realpaths.add(real)
                    if content is not None:
                        seen_contents.add(content)
            except (PermissionError, OSError) as e:
                logger.debug(f"Permission/OS error reading root rule {rule_file}: {e}")
            except Exception as e:
                logger.debug(f"Error extracting root rule {rule_file}: {e}")

    # -- Shared building blocks ---------------------------------------------

    def _add_instructions_tree(
        self,
        instructions_dir: Path,
        find_project_root_func,
        scope: str,
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        """Add every ``*.instructions.md`` under ``instructions_dir`` (recursive).

        Symlinked subdirectories are skipped during the recursion.
        """
        try:
            if not instructions_dir.is_dir() or instructions_dir.is_symlink():
                return
            self._walk_instructions_dir(
                instructions_dir, find_project_root_func, scope, projects_by_root, current_depth=0
            )
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading instructions dir {instructions_dir}: {e}")
        except Exception as e:
            logger.debug(f"Error processing instructions dir {instructions_dir}: {e}")

    def _walk_instructions_dir(
        self,
        current_dir: Path,
        find_project_root_func,
        scope: str,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recurse a bounded ``instructions/`` tree collecting ``*.instructions.md``."""
        if current_depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current_dir.iterdir():
                try:
                    if item.is_dir():
                        if item.is_symlink():
                            continue
                        self._walk_instructions_dir(
                            item, find_project_root_func, scope, projects_by_root, current_depth + 1
                        )
                    elif item.is_file() and item.name.endswith(INSTRUCTIONS_FILE_SUFFIX):
                        self._add_rule_file(item, find_project_root_func, scope, projects_by_root)
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _add_rule_file(
        self,
        rule_file: Path,
        find_project_root_func,
        scope: str,
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        """Read one rule file (explicit scope) and add it under its project root.

        Built via the shared ``extract_single_rule_file`` only — no frontmatter
        parsing, so the dict stays within the backend's field allowlist.
        """
        try:
            if not rule_file.is_file():
                return
            rule_info = extract_single_rule_file(rule_file, find_project_root_func, scope=scope)
            if rule_info:
                project_root = rule_info.get("project_root")
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except (PermissionError, OSError) as e:
            logger.debug(f"Permission/OS error reading rule file {rule_file}: {e}")
        except Exception as e:
            logger.debug(f"Error extracting rule file {rule_file}: {e}")

    # -- OS-specific seams (overridden by the Windows subclass) --------------
    #
    # The G/E/P source set and the depth-bounded walk above are OS-agnostic;
    # only these five primitives differ between macOS and Windows. The Windows
    # subclass overrides exactly these — keeping the walk and source logic
    # shared (DRY). See WindowsCopilotCliRulesExtractor.

    def _is_privileged(self) -> bool:
        """True when scanning all users (root on macOS) — gates E1 (per-user env)."""
        return is_running_as_root()

    def _scan_all_user_homes(self, extract_for_user) -> None:
        """Invoke ``extract_for_user(home)`` for every user home (all users when root)."""
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

    def _filesystem_root(self) -> Path:
        """Root the project walk starts from (POSIX ``/`` on macOS)."""
        return Path("/")

    def _iter_top_level_dirs(self, root_path: Path) -> List[Path]:
        """Top-level dirs under the filesystem root, system dirs excluded."""
        return list(get_top_level_directories(root_path))

    def _should_skip(self, item: Path) -> bool:
        """Whether a path is skipped during the project walk.

        Skips project/system dirs AND other-tool config dirs (``~/.<tool>``) so
        the walk never descends into another tool's installed-extension packages
        (e.g. ``~/.antigravity/extensions/<pkg>/.github``) and mis-attributes
        their bundled instructions to Copilot CLI.
        """
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )
