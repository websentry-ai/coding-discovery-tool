"""
Grok Bot config extraction for macOS systems.

Extracts:
- Global config: ~/.grokbot/settings.json

settings.json carries the device-side policy: ``localToolPermission`` (may the
bot run tasks on this computer), ``egressTunnelEnabled`` / ``localEgressAllowed``
(route the bot's network traffic through this computer) and the MCP server ids
and disabled tools it keeps locally. Skills and MCP server definitions are
stored on the bot's cloud computer, not on the device, so there is nothing
further to collect.

The file is recorded as RAW TEXT through the shared rules mechanism, with
secret values redacted by ``extract_rule_file_contained``.
"""

import logging
from pathlib import Path
from typing import Dict, List

from ...coding_tool_base import BaseGrokBotRulesExtractor
from ...rule_read_helpers import extract_rule_file_contained
from ...macos_extraction_helpers import (
    add_rule_to_project,
    build_project_list,
    is_running_as_root,
    scan_user_directories,
)

logger = logging.getLogger(__name__)

GROK_BOT_DATA_DIR = ".grokbot"

_GLOBAL_CONFIG_FILES = ("settings.json",)

# SECURITY: the local-exec daemon keeps its sealed credential and gateway
# connection beside settings.json. They must never be read, even if the
# allow-list above is widened later.
_NEVER_READ = frozenset({
    "local-exec-daemon-credential.json",
    "local-exec-daemon-connection.json",
})


class MacOSGrokBotRulesExtractor(BaseGrokBotRulesExtractor):
    """Extractor for Grok Bot config on macOS systems."""

    def extract_all_grok_bot_rules(self) -> List[Dict]:
        """
        Extract Grok Bot config for every user home in scope.

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
        """Extract ~/.grokbot config for one user home."""
        try:
            data_dir = user_home / GROK_BOT_DATA_DIR

            def home_root(_rule_file: Path, _home: Path = user_home) -> Path:
                return _home

            for name in _GLOBAL_CONFIG_FILES:
                if name in _NEVER_READ:
                    continue
                config_file = data_dir / name
                if not config_file.is_file():
                    continue
                # Strict read: a symlinked or hard-linked settings.json could point
                # at the daemon credential beside it, which _NEVER_READ checks by
                # name only.
                rule_info = extract_rule_file_contained(config_file, home_root)
                if rule_info:
                    rule_info["scope"] = "user"
                    project_root = rule_info.get("project_root")
                    if project_root:
                        add_rule_to_project(rule_info, project_root, projects_by_root)
        except Exception as e:
            logger.debug(f"Error extracting Grok Bot config for {user_home}: {e}")
