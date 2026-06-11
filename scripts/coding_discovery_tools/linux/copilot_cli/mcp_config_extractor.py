"""
MCP config extraction for the GitHub Copilot CLI on Linux systems.

The parser and the User-scope read are OS-agnostic (the User-scope all-users scan
is handled by the shared root-support helper, which already enumerates Linux user
homes), so this subclass inherits them from the macOS extractor. Only the
Workspace ``.mcp.json`` walk differs on Linux (the ``/`` root + Linux system
dirs); we override the ``_workspace_search_roots`` / ``_should_skip_workspace_path``
seams accordingly. Mirrors the Windows subclass.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ...linux_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.mcp_config_extractor import (
    MacOSCopilotCliMCPConfigExtractor,
)

logger = logging.getLogger(__name__)


class LinuxCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Copilot CLI MCP extractor on Linux; overrides only the workspace walk."""

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (Linux): every
        top-level dir under ``/`` (Linux system dirs excluded), or the home dir as
        a fallback."""
        root_path = Path("/")
        try:
            return [(root_path, top_dir) for top_dir in get_top_level_directories(root_path)]
        except (PermissionError, OSError) as exc:
            logger.debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the Linux workspace walk (skip + Linux system dirs)."""
        return should_skip_path(item) or should_skip_system_path(item)
