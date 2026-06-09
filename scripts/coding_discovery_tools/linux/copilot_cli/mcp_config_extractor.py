"""
MCP config extraction for the GitHub Copilot CLI on Linux systems.

The parser and the User-scope read (``~/.copilot/mcp-config.json``) are
OS-agnostic, so this subclass inherits them from the macOS extractor — and the
User-scope path is already Linux-aware via
``extract_ide_global_configs_with_root_support`` (it delegates to
``get_linux_user_homes()`` for the ``/root`` + ``/home/*`` scan).

Only the **Workspace** ``.mcp.json`` walk differs on Linux. The macOS base's
``_should_skip_workspace_path`` routes through the macOS ``should_skip_system_path``,
whose ``SKIP_SYSTEM_DIRS`` includes ``/home`` — so inheriting it unchanged would
silently drop every ``/home/<user>/<repo>/.mcp.json`` on Linux. We override the
``_workspace_search_roots`` / ``_should_skip_workspace_path`` seams to use the
Linux helpers (which do NOT prune ``/home``), exactly as the Linux rules and
skills extractors do for their own walks.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ...linux_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor

logger = logging.getLogger(__name__)


class LinuxCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Copilot CLI MCP extractor on Linux; overrides only the workspace walk so
    ``/home`` is not pruned.

    User-scope ``~/.copilot/mcp-config.json`` is inherited unchanged (already
    Linux-aware via the root-support helper).
    """

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (Linux): every
        top-level dir under ``/`` via the Linux helper (which keeps ``/home``),
        or the home dir as a fallback."""
        root_path = Path("/")
        try:
            return [(root_path, top_dir) for top_dir in get_top_level_directories(root_path)]
        except (PermissionError, OSError) as exc:
            logger.debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the Linux workspace walk: the Linux system-dir
        helpers, which (unlike the macOS base) do NOT prune ``/home``."""
        return should_skip_path(item) or should_skip_system_path(item)
