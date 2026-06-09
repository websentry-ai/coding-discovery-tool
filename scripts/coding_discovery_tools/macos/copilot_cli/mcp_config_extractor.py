"""
MCP config extraction for the GitHub Copilot CLI.

The CLI loads MCP servers from the User config (``~/.copilot/mcp-config.json``)
and Workspace files (``<project>/.mcp.json``); this extractor reads both. The
User file is parsed here; the Workspace ``.mcp.json`` is read via the shared
``walk_for_claude_project_mcp_configs``, with OS-specific roots/skip in the
``_workspace_search_roots`` / ``_should_skip_workspace_path`` seams (overridden
by the Windows subclass).
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ...coding_tool_base import BaseMCPConfigExtractor
from ...macos_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
    walk_for_claude_project_mcp_configs,
    # Re-exported here for back-compat: the settings extractor and tests import
    # these JSONC strippers from this module. Single source of truth lives in
    # mcp_extraction_helpers.
    _strip_jsonc_comments,
    _strip_trailing_commas,
)
from .copilot_cli import _resolve_copilot_dir

logger = logging.getLogger(__name__)

_TOOL_NAME = "GitHub Copilot CLI"
_CLI_DIR_NAME = ".copilot"
# User-scope MCP file. Workspace servers live in .mcp.json (project-scope walk).
_MCP_CONFIG_FILENAME = "mcp-config.json"


def _extract_servers_obj(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve the server mapping from a parsed CLI MCP config.

    Order of precedence:
    1. ``mcpServers`` (canonical wrapped form)
    2. ``servers`` (VS Code / alternate wrapped form)
    3. flat top-level object of ``{name: {config}}`` — the GitHub CLI accepts
       the unwrapped Claude-style form (review P1-4). In this fallback only,
       a value counts as a server iff it is a dict carrying a ``command`` or
       ``url`` (the fields a server is actually reachable by); this ignores
       scalar metadata and non-server objects (e.g. a VS Code-style ``inputs``
       block) so they aren't surfaced or scanned as phantom servers. The
       wrapped forms above are trusted as-is — the user declared them servers.
    """
    wrapped = config_data.get("mcpServers")
    if isinstance(wrapped, dict):
        return wrapped

    servers = config_data.get("servers")
    if isinstance(servers, dict):
        return servers

    return {
        name: value
        for name, value in config_data.items()
        if isinstance(value, dict)
        and ("command" in value or "url" in value)
    }


class MacOSCopilotCliMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for GitHub Copilot CLI MCP config on macOS systems."""

    def extract_mcp_config(
        self, plugin_lookup: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Extract GitHub Copilot CLI MCP config: User (``~/.copilot/mcp-config.json``,
        root-aware) plus Workspace (``.mcp.json`` at project roots). Each is a
        distinct project entry. Returns a ``projects`` dict, or None if empty.
        """
        projects = extract_ide_global_configs_with_root_support(
            self._extract_cli_configs_for_user,
            tool_name=_TOOL_NAME,
        )

        projects.extend(self._extract_workspace_configs())

        if not projects:
            return None

        return {"projects": projects}

    def _extract_cli_configs_for_user(self, user_home: Path) -> List[Dict]:
        """
        Extract the Copilot CLI MCP config for a single user.

        Reads ``mcp-config.json`` from the resolved config dir (``COPILOT_HOME``
        when set for this user, else ``user_home/.copilot``) and returns a
        single-entry list with that dir as the project path, or an empty list
        when the file is absent, unparseable, or has no servers.
        """
        copilot_dir = _resolve_copilot_dir(user_home)
        config_path = copilot_dir / _MCP_CONFIG_FILENAME

        config = self._read_cli_mcp_config(config_path, str(copilot_dir))
        return [config] if config else []

    # -- Workspace scope: project-root .mcp.json -----------------------------

    def _extract_workspace_configs(self) -> List[Dict]:
        """Walk ``_workspace_search_roots`` for project-scope ``.mcp.json`` files.

        Never raises — this runs on customer machines.
        """
        projects: List[Dict] = []
        for root_path, start_dir in self._workspace_search_roots():
            try:
                start_depth = len(start_dir.relative_to(root_path).parts)
            except ValueError:
                start_depth = 0
            try:
                walk_for_claude_project_mcp_configs(
                    root_path,
                    start_dir,
                    projects,
                    self._should_skip_workspace_path,
                    current_depth=start_depth,
                )
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping workspace scan of {start_dir}: {exc}")
            except Exception as exc:
                logger.debug(f"Error scanning workspace dir {start_dir}: {exc}")
        return projects

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (macOS): every
        top-level dir under ``/`` (root-aware), or the home dir as a fallback."""
        root_path = Path("/")
        try:
            return [(root_path, top_dir) for top_dir in get_top_level_directories(root_path)]
        except (PermissionError, OSError) as exc:
            logger.debug(f"Falling back to home for workspace scan: {exc}")
            home = Path.home()
            return [(home, home)]

    def _should_skip_workspace_path(self, item: Path) -> bool:
        """Skip predicate for the macOS workspace walk (system + skip dirs)."""
        return should_skip_path(item) or should_skip_system_path(item)

    def _read_cli_mcp_config(
        self, config_path: Path, tool_path: str
    ) -> Optional[Dict]:
        """
        Read and parse a Copilot CLI ``mcp-config.json`` file.

        Strips JSONC comments and trailing commas before parsing, resolves the
        server mapping from the wrapped or flat form, and transforms it to the
        array shape. All IO is wrapped — this tool runs on customer machines and
        must never crash.

        Returns:
            Dict with ``path``, ``mcpServers`` and ``scope`` keys, or None.
        """
        try:
            if not config_path.is_file():
                return None

            content = config_path.read_text(encoding='utf-8', errors='replace')
            content = _strip_jsonc_comments(content)
            content = _strip_trailing_commas(content)
            config_data = json.loads(content)

            if not isinstance(config_data, dict):
                return None

            mcp_servers_obj = _extract_servers_obj(config_data)
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

            if mcp_servers_array:
                # scope mirrors the workspace walk's "project" tag so the combined
                # projects list is consistently labelled.
                return {
                    "path": tool_path,
                    "mcpServers": mcp_servers_array,
                    "scope": "user",
                }
        except json.JSONDecodeError as exc:
            logger.warning(
                f"Invalid JSON in {_TOOL_NAME} MCP config {config_path}: {exc}"
            )
        except PermissionError as exc:
            logger.debug(
                f"Permission denied reading {_TOOL_NAME} MCP config {config_path}: {exc}"
            )
        except Exception as exc:
            logger.warning(
                f"Error reading {_TOOL_NAME} MCP config {config_path}: {exc}"
            )

        return None
