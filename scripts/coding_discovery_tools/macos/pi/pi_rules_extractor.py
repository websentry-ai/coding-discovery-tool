"""
pi coding agent config extraction for macOS systems.

Extracts:
- Global config: ~/.pi/agent/{settings.json,models.json,AGENTS.md}
- Project-level config: **/.pi/{settings.json,SYSTEM.md,APPEND_SYSTEM.md}

Config files are recorded as RAW TEXT through the shared rules mechanism. They
are never ``json.loads``-ed: the agent accepts JSONC (comments + trailing
commas) and parsing would both raise and reformat what the payload carries.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BasePiRulesExtractor
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

# Project-level files under ``<project>/.pi/``.
_PROJECT_CONFIG_FILES = ("settings.json", "SYSTEM.md", "APPEND_SYSTEM.md")

# Global files under ``~/.pi/agent/``.
_GLOBAL_CONFIG_FILES = ("settings.json", "models.json", "AGENTS.md")

# SECURITY: ``auth.json`` holds the agent's OAuth tokens / API credentials.
# It must never be read, and must never reach the uploaded payload. This set is
# consulted in addition to the allow-lists above (defence in depth) so a future
# widening of those tuples cannot silently start collecting credentials.
_NEVER_READ = frozenset({"auth.json"})


def find_pi_project_root(rule_file: Path) -> Path:
    """
    Find the project root for a pi config file.

    Global: ~/.pi/agent/settings.json -> ~        (file -> agent -> .pi -> home)
    Project: <project>/.pi/settings.json -> <project>  (file -> .pi -> project)
    """
    if ".pi/agent" in str(rule_file):
        return rule_file.parent.parent.parent
    return rule_file.parent.parent


class MacOSPiRulesExtractor(BasePiRulesExtractor):
    """Extractor for pi coding agent config on macOS systems."""

    def extract_all_pi_rules(self) -> List[Dict]:
        """
        Extract all pi config files from all projects on macOS.

        Returns:
            List of project dicts with ``project_root`` and ``rules``.
        """
        projects_by_root: Dict[str, List[Dict]] = {}

        self._extract_global_rules(projects_by_root)

        root_path = Path("/")
        logger.info(f"Searching for pi coding agent config from root: {root_path}")
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
        """True when ``tool_dir`` is a user-level ``~/.pi``. Linux overrides this."""
        return is_user_level_tool_dir(tool_dir)

    # -- extraction ---------------------------------------------------------

    def _extract_global_rules(self, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract ~/.pi/agent config for every user home in scope."""

        def extract_for_user(user_home: Path) -> None:
            try:
                agent_dir = user_home / ".pi" / "agent"
                # Gate: a bare ``~/.pi/agent`` (session state only) is not a
                # configured install and must not produce a project row.
                if not (agent_dir / "settings.json").is_file():
                    return
                for name in _GLOBAL_CONFIG_FILES:
                    if name in _NEVER_READ:
                        continue
                    config_file = agent_dir / name
                    if not config_file.is_file():
                        continue
                    rule_info = extract_single_rule_file(
                        config_file, find_pi_project_root, scope="user"
                    )
                    if rule_info:
                        project_root = rule_info.get("project_root")
                        if project_root:
                            add_rule_to_project(rule_info, project_root, projects_by_root)
            except Exception as e:
                logger.debug(f"Error extracting global pi config for {user_home}: {e}")

        self._for_each_user_home(extract_for_user)

    def _extract_project_level_rules(
        self, root_path: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract ``**/.pi`` project config recursively from ``root_path``."""

        def walk_for_pi_dirs(root: Path, current: Path, projects: Dict,
                             current_depth: int = 0) -> None:
            walk_for_tool_directories(
                root, current, ".pi", self._extract_rules_from_pi_directory,
                projects, current_depth
            )

        extract_project_level_rules_with_fallback(
            root_path,
            ".pi",
            self._extract_rules_from_pi_directory,
            walk_for_pi_dirs,
            projects_by_root,
        )

    def _extract_rules_from_pi_directory(
        self, pi_dir: Path, projects_by_root: Dict[str, List[Dict]]
    ) -> None:
        """Extract config files from a single ``.pi`` directory."""
        try:
            # The user-level ``~/.pi`` is owned by _extract_global_rules; letting
            # the project walk claim it too would emit a duplicate home row.
            if self._is_user_level_dir(pi_dir):
                return
            # Gate: only a real settings.json marks a configured project.
            if not (pi_dir / "settings.json").is_file():
                return
            for name in _PROJECT_CONFIG_FILES:
                if name in _NEVER_READ:
                    continue
                config_file = pi_dir / name
                if not config_file.is_file():
                    continue
                if not should_process_file(config_file, pi_dir.parent):
                    continue
                rule_info = extract_single_rule_file(config_file, find_pi_project_root)
                if rule_info:
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting pi config from {pi_dir}: {e}")
