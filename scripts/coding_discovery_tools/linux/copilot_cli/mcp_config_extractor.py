"""
MCP config extraction for the GitHub Copilot CLI on Linux.

The user-scope path (``~/.copilot/mcp-config.json``) is handled by
``extract_ide_global_configs_with_root_support``, which is already Linux-aware
(delegates to ``get_linux_user_homes()``). That part is inherited unchanged.

The workspace-scope walk (``<project>/.mcp.json``) uses the virtual seams
``_workspace_search_roots`` / ``_should_skip_workspace_path``. The macOS base
wires macOS ``get_top_level_directories`` and ``should_skip_system_path`` into
those seams — both exclude ``/home`` via ``SKIP_SYSTEM_DIRS``. This subclass
overrides them with their Linux equivalents so project-scope ``.mcp.json``
files under ``/home/<user>/...`` are discovered correctly.
"""

from pathlib import Path
from typing import List, Tuple

from ...linux_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...macos.copilot_cli.mcp_config_extractor import MacOSCopilotCliMCPConfigExtractor


class LinuxCopilotCliMCPConfigExtractor(MacOSCopilotCliMCPConfigExtractor):
    """Extractor for GitHub Copilot CLI MCP config on Linux systems.

    The user-scope path (``~/.copilot/mcp-config.json``) is inherited unchanged
    — ``extract_ide_global_configs_with_root_support`` is already Linux-aware.

    The workspace-scope seams (``_workspace_search_roots`` /
    ``_should_skip_workspace_path``) are overridden here so that once
    PR#155 (workspace ``.mcp.json`` discovery) lands, ``/home`` is not silently
    excluded on Linux. Until that PR merges the macOS base ignores these seams,
    but they are defined now so no follow-up is needed at merge time.
    """

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk on Linux.

        Uses Linux ``get_top_level_directories`` so ``/home`` is included (the
        macOS version excludes it via ``SKIP_SYSTEM_DIRS``). Falls back to home
        directory on permission error.
        """
        import logging as _logging
        root_path = Path("/")
        try:
            return [(root_path, top_dir) for top_dir in get_top_level_directories(root_path)]
        except (PermissionError, OSError) as exc:
            _logging.getLogger(__name__).debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the Linux workspace walk."""
        return should_skip_path(item) or should_skip_system_path(item)

    def _extract_workspace_configs(self) -> List:
        """Forward-compatible stub: calls the macOS base when the method exists,
        returns [] gracefully until PR#155 (workspace ``.mcp.json``) merges."""
        method = getattr(super(), "_extract_workspace_configs", None)
        if callable(method):
            return method()
        return []
