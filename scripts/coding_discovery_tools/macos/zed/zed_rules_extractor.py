"""
Zed config extraction for macOS systems.

Extracts:
- Global config: ~/.config/zed/{settings.json,global_settings.json,keymap.json,AGENTS.md}
- Project config: **/.zed/{settings.json,tasks.json,debug.json} plus the
  project-root ``.rules`` instructions file.

NOTE: Zed keeps its config in ``~/.config/zed`` on macOS TOO — it does not use
``~/Library/Application Support`` for settings — so the global path is identical
on macOS and Linux and only the user-enumeration seam differs.

Config files are recorded as RAW TEXT through the shared rules mechanism. They
are never ``json.loads``-ed: Zed settings are JSONC (comments + trailing commas
are legal and idiomatic), so parsing would raise on ordinary user files and
would reformat what the payload carries.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseZedRulesExtractor
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    extract_project_level_rules_with_fallback,
    extract_single_rule_file,
    is_running_as_root,
    is_user_level_tool_dir,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
)

logger = logging.getLogger(__name__)

# Files under ``<project>/.zed/``.
_PROJECT_CONFIG_FILES = ("settings.json", "tasks.json", "debug.json")

# Either of these opens the project gate.
_PROJECT_GATE_FILES = ("settings.json",)

# The project-root instructions file Zed's agent reads (sibling of ``.zed``).
_PROJECT_ROOT_RULES_FILE = ".rules"

# Files under ``~/.config/zed/``.
_GLOBAL_CONFIG_FILES = ("settings.json", "global_settings.json", "keymap.json", "AGENTS.md")

# Either of these opens the global gate: a bare ``~/.config/zed`` holding only
# downloaded extensions/state is not a configured install.
_GLOBAL_GATE_FILES = ("settings.json", "global_settings.json")


def find_zed_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a Zed config file.

    Global:  ~/.config/zed/settings.json -> ~   (file -> zed -> .config -> home)
    Root rules: <project>/.rules -> <project>   (the file's own directory)
    Project: <project>/.zed/settings.json -> <project>  (file -> .zed -> project)
    """
    if ".config/zed" in str(rule_file):
        return rule_file.parent.parent.parent
    if rule_file.name == _PROJECT_ROOT_RULES_FILE:
        return rule_file.parent
    return rule_file.parent.parent


class MacOSZedRulesExtractor(BaseZedRulesExtractor):
    """Extractor for Zed config on macOS systems."""

    def extract_all_zed_rules(self) -> List[Dict]:
        """
        Extract all Zed config files from all projects on macOS.

        Returns:
            List of project dicts with ``project_root`` and ``rules``.
        """
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path("/")
        logger.info(f"Searching for Zed config from root: {root_path}")
        self._extract_project_level_rules(root_path, projects_by_root)

        return build_project_list(projects_by_root)

    # -- OS seams -----------------------------------------------------------

    def _for_each_user_home(self, fn) -> None:
        """Run ``fn(user_home)`` for every home in scope. Linux overrides this."""
        if is_running_as_root():
            scan_user_directories(fn)
        else:
            fn(Path.home())

    def _is_user_level_dir(self, tool_dir: Path) -> bool:
        """True when ``tool_dir`` is a user-level ``~/.zed``. Linux overrides this."""
        return is_user_level_tool_dir(tool_dir)

    # -- extraction ---------------------------------------------------------

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract ~/.config/zed config for every user home in scope."""

        def extract_for_user(user_home: Path) -> None:
            try:
                config_dir = user_home / ".config" / "zed"
                if not any((config_dir / name).is_file() for name in _GLOBAL_GATE_FILES):
                    return
                for name in _GLOBAL_CONFIG_FILES:
                    config_file = config_dir / name
                    if not config_file.is_file():
                        continue
                    rule_info = extract_single_rule_file(
                        config_file, find_zed_project_root, scope="user"
                    )
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global Zed config for {user_home}: {e}")

        self._for_each_user_home(extract_for_user)

    def _extract_project_level_rules(
        self, root_path: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract ``**/.zed`` project config recursively from ``root_path``."""

        def walk_for_zed_dirs(root: Path, current: Path, projects: Dict,
                              current_depth: int = 0) -> None:
            walk_for_tool_directories(
                root, current, ".zed", self._extract_rules_from_zed_directory,
                projects, current_depth
            )

        extract_project_level_rules_with_fallback(
            root_path,
            ".zed",
            self._extract_rules_from_zed_directory,
            walk_for_zed_dirs,
            projects_by_root,
        )

    def _extract_rules_from_zed_directory(
        self, zed_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract config files from a single ``.zed`` directory."""
        try:
            # A user-level ``~/.zed`` is not where Zed keeps user config
            # (``~/.config/zed`` is), so anything found there is not a project.
            if self._is_user_level_dir(zed_dir):
                return
            # Gate: only a real settings.json marks a configured project.
            if not any((zed_dir / name).is_file() for name in _PROJECT_GATE_FILES):
                return

            project_root_dir = zed_dir.parent
            candidates = [zed_dir / name for name in _PROJECT_CONFIG_FILES]
            candidates.append(project_root_dir / _PROJECT_ROOT_RULES_FILE)

            for config_file in candidates:
                if not config_file.is_file():
                    continue
                if not should_process_file(config_file, project_root_dir):
                    continue
                rule_info = extract_single_rule_file(config_file, find_zed_project_root)
                if rule_info:
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting Zed config from {zed_dir}: {e}")
