"""
MCP config extraction for the GitHub Copilot CLI on Windows systems.

The parser and the User-scope read are OS-agnostic, so this subclass inherits
them from the macOS extractor. Only the Workspace ``.mcp.json`` walk differs on
Windows (drive-letter root + Windows system dirs); we override the
``_workspace_search_roots`` / ``_should_skip_workspace_path`` seams accordingly.
"""

import logging
from pathlib import Path
from typing import List, Tuple

from ...macos.copilot_cli.mcp_config_extractor import (
    MacOSCopilotCliMCPConfigExtractor,
)
from ...windows_extraction_helpers import should_skip_path

logger = logging.getLogger(__name__)

# Windows system dirs skipped by the workspace walk (mirrors Windows Claude Code).
# Lower-cased: NTFS is case-insensitive, so we match dir names case-insensitively.
_WINDOWS_SYSTEM_DIRS = frozenset({
    "windows", "program files", "program files (x86)", "programdata",
    "system volume information", "$recycle.bin", "recovery",
    "perflogs", "boot", "system32", "syswow64", "winsxs",
    "config.msi", "documents and settings", "msocache",
})


class WindowsCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Copilot CLI MCP extractor on Windows; overrides only the workspace walk."""

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (Windows): the
        home drive's top-level dirs (system dirs skipped), or home as fallback."""
        root_path = Path(Path.home().anchor or "C:\\")
        try:
            top_level_dirs = [
                item for item in root_path.iterdir()
                if item.is_dir() and not self._should_skip_workspace_path(item)
            ]
            return [(root_path, top_dir) for top_dir in top_level_dirs]
        except (PermissionError, OSError) as exc:
            logger.debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the Windows workspace walk: shared SKIP_DIRS plus a
        case-insensitive Windows system-dir match."""
        return should_skip_path(item) or item.name.lower() in _WINDOWS_SYSTEM_DIRS
