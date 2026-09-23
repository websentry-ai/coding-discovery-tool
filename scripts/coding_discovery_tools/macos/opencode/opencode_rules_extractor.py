"""
OpenCode rules extraction for macOS systems.

Extracts OpenCode configuration files from:
- Global: ~/.config/opencode/{agent,agents,commands}/*.md and
          ~/.config/opencode/opencode.json[c]
- Project-level (recursive): <project>/.opencode/{agent,agents,commands}/*.md,
          <project>/.opencode/opencode.json[c], and the project-root sibling
          <project>/opencode.json[c]

Config files are recorded as raw text through the rules mechanism; they are
never json.loads-ed (opencode.jsonc allows comments and trailing commas).

A project with only a root-level opencode.json[c] (no .opencode/ dir) is
discovered via the shared index's file markers; when both exist, the `.opencode/`
route records the root sibling and the file route is skipped to avoid duplicates.
"""

import logging
from pathlib import Path
from typing import List, Dict

from ...coding_tool_base import BaseOpenCodeRulesExtractor
from ...rule_read_helpers import extract_rule_file_contained
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    should_process_file,
    is_running_as_root,
    scan_user_directories,
    extract_project_level_rules_with_fallback,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

# Sub-directories holding markdown rule files. Docs moved to the plural
# `agents/`; the singular is kept for already-installed users. `skills/` is
# deliberately excluded: Agent Skills are owned by the skills extractor and
# would otherwise be reported twice.
_RULE_SUBDIRS = ("agent", "agents", "commands")

_CONFIG_FILENAMES = ("opencode.json", "opencode.jsonc")
# Project-root marker files: a project may carry only `<project>/opencode.json[c]`
# with no `.opencode/` dir. These are indexed alongside hidden dirs
# (project_dir_index.INDEXED_FILE_MARKERS), so finding them is free.
OPENCODE_ROOT_MARKERS = frozenset(_CONFIG_FILENAMES)


def iter_opencode_config_files(opencode_dir: Path, include_root_siblings: bool) -> List[Path]:
    """
    All rule/config files belonging to one OpenCode config directory.

    Args:
        opencode_dir: A `.opencode/` project dir or the global `~/.config/opencode/` dir
        include_root_siblings: Also return `<opencode_dir.parent>/opencode.json[c]`
            (the project-root config that sits next to `.opencode/`). False for
            the global dir, where the parent is `~/.config`.
    """
    files: List[Path] = []
    for subdir in _RULE_SUBDIRS:
        rules_dir = opencode_dir / subdir
        try:
            if rules_dir.is_dir():
                files.extend(sorted(rules_dir.glob("*.md")))
        except OSError:
            continue
    for name in _CONFIG_FILENAMES:
        cfg = opencode_dir / name
        try:
            if cfg.is_file():
                files.append(cfg)
        except OSError:
            continue
    if include_root_siblings:
        for name in _CONFIG_FILENAMES:
            cfg = opencode_dir.parent / name
            try:
                if cfg.is_file():
                    files.append(cfg)
            except OSError:
                continue
    return files


def find_opencode_project_root(rule_file: Path) -> Path:
    """
    Find the project root for an OpenCode rule/config file.

    Global:  ~/.config/opencode/<subdir>/x.md    -> ~
             ~/.config/opencode/opencode.json[c]  -> ~
    Project: <project>/.opencode/<subdir>/x.md    -> <project>
             <project>/.opencode/opencode.json[c] -> <project>
             <project>/opencode.json[c]           -> <project>  (sibling of .opencode/)

    Branches on the directory names rather than a fixed number of parent hops
    so all five shapes resolve correctly.
    """
    parent = rule_file.parent
    for _ in range(2):
        if parent.name == ".opencode":
            return parent.parent
        if parent.name == "opencode" and parent.parent.name == ".config":
            return parent.parent.parent
        parent = parent.parent
    # Project-root sibling (opencode.json next to .opencode/)
    return rule_file.parent


class MacOSOpenCodeRulesExtractor(BaseOpenCodeRulesExtractor):
    """Extractor for OpenCode rules on macOS systems."""

    def extract_all_opencode_rules(self) -> List[Dict]:
        """
        Extract all OpenCode rules from all projects on macOS.
        
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
        
        logger.info(f"Searching for OpenCode rules from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        # Convert dictionary to list of project objects
        return build_project_list(projects_by_root)

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract global OpenCode rules from ~/.config/opencode/agent/*.md.
        
        When running as root, scans all user directories.
        
        Args:
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def extract_for_user(user_home: Path) -> None:
            """Extract global rules and config for a specific user."""
            global_dir = user_home / ".config" / "opencode"

            try:
                if not global_dir.is_dir():
                    return
                for rule_file in iter_opencode_config_files(global_dir, include_root_siblings=False):
                    if should_process_file(rule_file, user_home):
                        rule_info = extract_rule_file_contained(
                            rule_file,
                            find_opencode_project_root,
                            scope="user",
                            user_home=user_home,
                        )
                        if rule_info:
                            project_root = rule_info.get('project_root')
                            if project_root:
                                add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global OpenCode rules for {user_home}: {e}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())

    def _extract_project_level_rules(self, root_path: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract project-level rules recursively from all projects.
        
        Searches for .opencode/agent/*.md files in all projects.
        
        Args:
            root_path: Root directory to search from (system root for MDM)
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        def walk_for_opencode_dirs(root: Path, current: Path, projects: Dict, current_depth: int = 0) -> None:
            """Wrapper to use shared walk helper with tool-specific extraction."""
            walk_for_tool_directories(
                root, current, ".opencode", self._extract_rules_from_opencode_directory,
                projects, current_depth,
                file_marker_names=OPENCODE_ROOT_MARKERS,
                extract_from_file_func=self._extract_rules_from_root_config,
            )
        
        extract_project_level_rules_with_fallback(
            root_path,
            ".opencode",
            self._extract_rules_from_opencode_directory,
            walk_for_opencode_dirs,
            projects_by_root,
            file_marker_names=OPENCODE_ROOT_MARKERS,
            extract_from_file_func=self._extract_rules_from_root_config
        )

    def _extract_rules_from_opencode_directory(self, opencode_dir: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Extract all rule files from a .opencode directory.
        
        Args:
            opencode_dir: Path to .opencode directory
            projects_by_root: Dictionary to populate with rules grouped by project root
        """
        # A project is emitted only when at least one rule/config file exists;
        # a `.opencode/` holding only e.g. `sessions/` yields nothing.
        try:
            for rule_file in iter_opencode_config_files(opencode_dir, include_root_siblings=True):
                if should_process_file(rule_file, opencode_dir.parent):
                    rule_info = extract_rule_file_contained(
                        rule_file,
                        find_opencode_project_root
                    )
                    if rule_info:
                        project_root = rule_info.get('project_root')
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting rules from {opencode_dir}: {e}")

    def _extract_rules_from_root_config(self, config_file: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """
        Record a project-root `opencode.json[c]` that has no `.opencode/` dir.

        When `.opencode/` exists beside it, `_extract_rules_from_opencode_directory`
        already records the root sibling, so this route steps aside.
        """
        try:
            # `.opencode/opencode.json` and a root file beside a `.opencode/` dir are
            # both recorded by the directory route; only the bare root file is ours.
            if config_file.parent.name == ".opencode" or (config_file.parent / ".opencode").is_dir():
                return
            if not should_process_file(config_file, config_file.parent):
                return
            rule_info = extract_rule_file_contained(config_file, find_opencode_project_root)
            if rule_info:
                project_root = rule_info.get('project_root')
                if project_root:
                    add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting root config {config_file}: {e}")
