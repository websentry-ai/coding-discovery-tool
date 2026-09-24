"""
Muse Code config extraction for macOS systems.

Extracts:
- Global config: ~/.config/muse/settings.json

settings.json holds the user's model defaults, MCP servers, hooks and runtime
capability toggles. It is recorded as RAW TEXT through the shared rules
mechanism, with secret values redacted by ``extract_rule_file_contained``.

Project-level files Muse Code reads (AGENTS.md / CLAUDE.md, .mcp.json and
.claude/.codex/.agents skills) are shared with other agents and are reported
under those agents, not here.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseMuseCodeRulesExtractor
from ...rule_read_helpers import extract_rule_file_contained
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)

MUSE_CODE_CONFIG_DIR = Path(".config") / "muse"

_GLOBAL_CONFIG_FILES = ("settings.json",)

# SECURITY: auth.json holds the account's OAuth tokens. It must never be read,
# even if the allow-list above is widened later.
_NEVER_READ = frozenset({"auth.json"})


class MacOSMuseCodeRulesExtractor(BaseMuseCodeRulesExtractor):
    """Extractor for Muse Code config on macOS systems."""

    def extract_all_muse_code_rules(self) -> List[Dict]:
        """
        Extract Muse Code config for every user home in scope.

        Returns:
            List of project dicts with ``project_root`` and ``rules``.
        """
        projects_by_root: Dict[str, List[Dict]] = {}
        self._for_each_user_home(
            lambda user_home: self._extract_for_user(user_home, projects_by_root)
        )
        return build_project_list(projects_by_root)

    def _for_each_user_home(self, fn) -> None:
        """Run ``fn(user_home)`` for every home in scope."""
        if is_running_as_root():
            scan_user_directories(fn)
        else:
            fn(Path.home())

    def _extract_for_user(self, user_home: Path, projects_by_root: Dict[str, List[Dict]]) -> None:
        """Extract ~/.config/muse config for one user home."""
        try:
            config_dir = user_home / MUSE_CODE_CONFIG_DIR

            def home_root(_rule_file: Path, _home: Path = user_home) -> Path:
                return _home

            for name in _GLOBAL_CONFIG_FILES:
                if name in _NEVER_READ:
                    continue
                config_file = config_dir / name
                if not config_file.is_file():
                    continue
                # Strict read: a symlinked or hard-linked settings.json could point
                # at auth.json beside it, which _NEVER_READ checks by name only.
                rule_info = extract_rule_file_contained(config_file, home_root)
                if rule_info:
                    rule_info["scope"] = "user"
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting Muse Code config for {user_home}: {e}")
