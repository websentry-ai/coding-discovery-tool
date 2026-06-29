"""
Augment Code rules/guidelines extraction for macOS.

Augment Code (config under ``~/.augment/``) discovers guidelines from several
sources (paths verified against the architect's revised D3):

  - User (scope "user"):
      ``~/.augment/user-guidelines.md``
      ``~/.augment/rules/**/*.{md,mdx}``
  - Project (scope "project"):
      repo-root ``.augment-guidelines`` (single file)
      ``<ws>/.augment/rules/**/*.{md,mdx}`` (recursive, bounded)
      ``AGENTS.md`` and ``CLAUDE.md`` discovered hierarchically/recursively
      (depth-bounded), mirroring how Augment walks subdir -> parents to root.

Both ``.md`` and ``.mdx`` rule files are supported. User rules are grouped under
``~/.augment`` as their ``project_root`` so they coalesce with the user's MCP
servers + skills. Rule dicts are built ONLY via ``extract_single_rule_file`` with
an explicit ``scope`` — no frontmatter is parsed into the dict (the backend drops
any rule carrying a key outside its allowlist; frontmatter stays in ``content``).
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseAugmentRulesExtractor
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
from .augment import _resolve_augment_dir

logger = logging.getLogger(__name__)

AUGMENT_DIR_NAME = ".augment"
RULES_DIR_NAME = "rules"
USER_GUIDELINES_FILENAME = "user-guidelines.md"
PROJECT_GUIDELINES_FILENAME = ".augment-guidelines"
# Project-root agent files Augment discovers hierarchically (subdir -> root).
HIERARCHICAL_RULE_FILES = ("AGENTS.md", "CLAUDE.md")
_RULE_SUFFIXES = (".md", ".mdx")


def _is_augment_rule_file(name: str) -> bool:
    """True for ``.md`` / ``.mdx`` files (the Augment rules suffixes)."""
    lower = name.lower()
    return lower.endswith(_RULE_SUFFIXES)


def _make_fixed_root_finder(project_root: Path):
    """Return a ``find_project_root_func`` that always yields ``project_root``."""
    def _finder(_rule_file: Path) -> Path:
        return project_root
    return _finder


def _find_augment_dir_root(rule_file: Path) -> Path:
    """Project root for a file under a project's ``.augment/`` tree -> parent of
    ``.augment``."""
    for ancestor in rule_file.parents:
        if ancestor.name == AUGMENT_DIR_NAME:
            return ancestor.parent
    return rule_file.parent


def _find_self_dir_root(rule_file: Path) -> Path:
    """Project root for a repo-level rule file -> the directory containing it."""
    return rule_file.parent


class MacOSAugmentRulesExtractor(BaseAugmentRulesExtractor):
    """Extractor for Augment Code rules on macOS systems."""

    def extract_all_augment_rules(self) -> List[Dict]:
        """
        Extract all Augment Code rules from all projects on macOS.

        Returns:
            List of project dicts, each containing:
            - project_root: Path to the project root directory
            - rules: List of rule file dicts (without project_root field)
        """
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_user_rules(projects_by_root)
        # Compute the user-home ``~/.augment`` set ONCE (via the all-users seam)
        # so the project walk skips them instead of re-collecting user rules as
        # scope "project".
        user_augment_dirs = self._user_augment_dirs()
        self._extract_project_level_rules(
            self._filesystem_root(), projects_by_root, user_augment_dirs
        )

        return build_project_list(projects_by_root)

    # -- User (user-scope) ---------------------------------------------------

    def _extract_user_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract ``~/.augment/user-guidelines.md`` + ``~/.augment/rules/**``.

        Each user's ``~/.augment`` becomes the ``project_root`` so user rules
        coalesce with that user's MCP servers + skills.
        """
        def extract_for_user(user_home: Path) -> None:
            try:
                config_dir = _resolve_augment_dir(user_home)
                root_finder = _make_fixed_root_finder(config_dir)

                self._add_rule_file(
                    config_dir / USER_GUIDELINES_FILENAME,
                    root_finder,
                    "user",
                    projects_by_root,
                )
                self._add_rules_tree(
                    config_dir / RULES_DIR_NAME,
                    root_finder,
                    "user",
                    projects_by_root,
                )
            except Exception as e:
                logger.debug(f"Error extracting user Augment rules for {user_home}: {e}")

        self._scan_all_user_homes(extract_for_user)

    # -- Project (project-scope) ---------------------------------------------

    def _extract_project_level_rules(
        self,
        root_path: Path,
        projects_by_root: Dict[str, List[Dict]],
        user_augment_dirs: set,
    ) -> None:
        """Walk for project-level rules from the filesystem root."""
        if root_path == self._filesystem_root():
            try:
                for top_dir in self._iter_top_level_dirs(root_path):
                    try:
                        self._walk_for_project_rules(
                            root_path, top_dir, projects_by_root, user_augment_dirs, current_depth=1
                        )
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            except (PermissionError, OSError) as e:
                logger.warning(f"Error accessing root directory: {e}")
        else:
            self._walk_for_project_rules(
                root_path, root_path, projects_by_root, user_augment_dirs, current_depth=0
            )

    def _walk_for_project_rules(
        self,
        root_path: Path,
        current_dir: Path,
        projects_by_root: Dict[str, List[Dict]],
        user_augment_dirs: set,
        current_depth: int = 0,
    ) -> None:
        """Recursively walk collecting ``.augment-guidelines``, ``.augment/rules/**``,
        and hierarchical ``AGENTS.md`` / ``CLAUDE.md``.

        Symlinked directories are skipped (loop/perf risk on customer machines).
        User-home ``~/.augment`` dirs (in ``user_augment_dirs``) are skipped — they
        are already collected as user scope; descending here would re-emit the same
        ``~/.augment/rules/**`` files as scope "project" (different project_root, so
        ``_deduplicate_project_items`` — which dedups within one project — misses it).
        """
        if current_depth > MAX_SEARCH_DEPTH:
            return

        # Files in THIS directory: .augment-guidelines + AGENTS.md/CLAUDE.md
        # (the latter discovered at every level, not just the repo root).
        self._extract_dir_level_files(current_dir, projects_by_root)

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

                    # Skip non-dirs and symlinked dirs BEFORE the .augment
                    # handling / recursion (mirrors the mcp + settings walk
                    # ordering) so a symlinked .augment can't be followed.
                    if not item.is_dir() or item.is_symlink():
                        continue

                    if item.name == AUGMENT_DIR_NAME:
                        # .augment/rules/** lives here; skip the user-home
                        # ~/.augment (collected as user scope) to avoid
                        # re-emitting user rules as scope "project"; otherwise
                        # handle this project's tree (don't recurse in).
                        if item.resolve() in user_augment_dirs:
                            continue
                        self._add_rules_tree(
                            item / RULES_DIR_NAME,
                            _find_augment_dir_root,
                            "project",
                            projects_by_root,
                        )
                        continue

                    self._walk_for_project_rules(
                        root_path, item, projects_by_root, user_augment_dirs, current_depth + 1
                    )

                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_dir_level_files(
        self, project_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Collect ``.augment-guidelines`` + hierarchical ``AGENTS.md`` / ``CLAUDE.md``
        in ``project_dir`` (the directory itself, grouped under itself as root)."""
        self._add_rule_file(
            project_dir / PROJECT_GUIDELINES_FILENAME,
            _find_self_dir_root,
            "project",
            projects_by_root,
        )
        for file_name in HIERARCHICAL_RULE_FILES:
            self._add_rule_file(
                project_dir / file_name,
                _find_self_dir_root,
                "project",
                projects_by_root,
            )

    # -- Shared building blocks ----------------------------------------------

    def _add_rules_tree(
        self,
        rules_dir: Path,
        find_project_root_func,
        scope: str,
        projects_by_root: Dict[str, List[Dict]],
    ) -> None:
        """Add every ``.md``/``.mdx`` under ``rules_dir`` (recursive, bounded)."""
        try:
            if not rules_dir.is_dir() or rules_dir.is_symlink():
                return
            self._walk_rules_dir(
                rules_dir, find_project_root_func, scope, projects_by_root, current_depth=0
            )
        except (PermissionError, OSError) as e:
            logger.debug(f"Error reading rules dir {rules_dir}: {e}")
        except Exception as e:
            logger.debug(f"Error processing rules dir {rules_dir}: {e}")

    def _walk_rules_dir(
        self,
        current_dir: Path,
        find_project_root_func,
        scope: str,
        projects_by_root: Dict[str, List[Dict]],
        current_depth: int = 0,
    ) -> None:
        """Recurse a bounded ``rules/`` tree collecting ``.md``/``.mdx`` files."""
        if current_depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current_dir.iterdir():
                try:
                    if item.is_dir():
                        if item.is_symlink():
                            continue
                        self._walk_rules_dir(
                            item, find_project_root_func, scope, projects_by_root, current_depth + 1
                        )
                    elif item.is_file() and _is_augment_rule_file(item.name):
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
        """Read one rule file (explicit scope) and add it under its project root."""
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

    def _user_augment_dirs(self) -> set:
        """Resolved set of user-home ``~/.augment`` dirs to skip in the project walk.

        Built via the all-users ``_scan_all_user_homes`` seam so it works per-OS
        (the Linux/Windows subclasses override only that seam). Mirrors the
        settings extractor's identically-named helper.
        """
        dirs = set()

        def collect(user_home: Path) -> None:
            try:
                dirs.add(_resolve_augment_dir(user_home).resolve())
            except (PermissionError, OSError):
                pass

        self._scan_all_user_homes(collect)
        return dirs

    # -- OS-specific seams (overridden by the Windows/Linux subclasses) -------

    def _is_privileged(self) -> bool:
        """True when scanning all users (root on macOS)."""
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
        """Skip project/system dirs AND other-tool config dirs (``~/.<tool>``).

        ``.augment`` is NOT in OTHER_TOOL_CONFIG_DIRS, so the walk still descends
        into it to collect ``.augment/rules/**``.
        """
        return (
            should_skip_path(item)
            or should_skip_system_path(item)
            or traverses_other_tool_config_dir(item)
        )
