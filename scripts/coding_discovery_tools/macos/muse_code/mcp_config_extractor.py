"""
MCP config extraction for Muse Code on macOS systems.

Muse Code keeps user MCP servers under ``mcpServers`` in
``~/.config/muse/settings.json`` (``{"schema_version": 1, "mcpServers": {...}}``).
The project-level ``.mcp.json`` it also reads is shared with Claude Code and is
reported there.

The file is read through ``read_rule_file_contained`` in strict mode, the same
read the config extractor uses: under a root scan a symlink could otherwise file
another user's servers under this home, and a FIFO would hang the scan.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories
from ...mcp_extraction_helpers import transform_mcp_servers_to_array
from ...rule_read_helpers import read_rule_file_contained

logger = logging.getLogger(__name__)

MUSE_CODE_SETTINGS_PATH = Path(".config") / "muse" / "settings.json"


class MacOSMuseCodeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Muse Code MCP config on macOS systems."""

    def extract_mcp_config(self, plugin_lookup=None) -> Optional[Dict]:
        """
        Extract Muse Code MCP servers from ~/.config/muse/settings.json.

        When running as root, collects the config of every user.

        Returns:
            Dict with a projects array, or None when no servers are configured.
        """
        projects: List[Dict] = []

        def extract_for_user(user_home: Path) -> None:
            project = self._read_user_servers(user_home)
            if project:
                projects.append(project)

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

        if not projects:
            return None
        return {"projects": projects}

    def _read_user_servers(self, user_home: Path) -> Optional[Dict]:
        """One home's MCP servers, keyed on the home, or None."""
        settings_file = user_home / MUSE_CODE_SETTINGS_PATH
        try:
            if not settings_file.is_file():
                return None
            contained = read_rule_file_contained(settings_file, user_home)
            if contained is None:
                return None
            content, truncated, _size, _mtime = contained
            if truncated:
                logger.warning(f"Muse Code settings too large to parse: {settings_file}")
                return None
            servers = json.loads(content).get("mcpServers", {})
            if not isinstance(servers, dict) or not servers:
                return None
            server_list = transform_mcp_servers_to_array(servers)
            if not server_list:
                return None
            return {"path": str(user_home), "mcpServers": server_list}
        except (json.JSONDecodeError, AttributeError) as e:
            logger.warning(f"Invalid Muse Code settings {settings_file}: {e}")
        except Exception as e:
            logger.warning(f"Error reading Muse Code MCP config {settings_file}: {e}")
        return None
