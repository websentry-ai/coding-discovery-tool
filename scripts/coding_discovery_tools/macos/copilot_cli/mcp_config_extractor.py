"""
MCP config extraction for the GitHub Copilot CLI.

The CLI stores its MCP servers in ``~/.copilot/mcp-config.json``. The file is
JSON with comments (``//`` and ``/* */``) and trailing commas are both
tolerated, since the file is commonly hand-edited (review P1). The server map
may appear under ``mcpServers``, under ``servers``, or — for the GitHub CLI's
Claude-style unwrapped form — as a flat top-level object of ``{name: config}``
entries (review P1-4).

The parsing here is platform-neutral and the all-users branch is handled by
``extract_ide_global_configs_with_root_support`` (which already supports both
macOS ``/Users`` and Windows ``C:\\Users`` admin scans), so this extractor is
OS-agnostic. The Windows package reuses it via a thin subclass; do not fork the
parser. The class keeps the ``MacOS`` name for historical/import stability.
"""

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_ide_global_configs_with_root_support,
    transform_mcp_servers_to_array,
)

logger = logging.getLogger(__name__)

_TOOL_NAME = "GitHub Copilot CLI"
_CLI_DIR_NAME = ".copilot"
# MCP servers live ONLY in mcp-config.json. The detector accepts config.json /
# settings.json as install *markers*, but those hold general CLI settings
# (model, theme, trusted dirs) — not MCP servers — so reading only this file is
# intentional, not an oversight of the marker-set union (review W3). The CLI's
# own `--additional-mcp-config` docs confirm it augments ~/.copilot/mcp-config.json.
_MCP_CONFIG_FILENAME = "mcp-config.json"

# String-aware JSONC comment stripper. Removes // line comments and /* */ block
# comments without mangling URLs or quoted strings that contain comment-like
# sequences. The block-comment branch uses ``[\s\S]*?`` so it spans newlines on
# its own; we deliberately do NOT enable re.DOTALL, because that would let the
# ``//.*$`` line-comment branch swallow newlines (and thus the rest of the file)
# instead of stopping at end-of-line. Only re.MULTILINE is set so ``$`` anchors
# to each line end.
_JSONC_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*")|(/\*[\s\S]*?\*/)|(//[^\n]*)',
    re.MULTILINE,
)

# String-aware trailing-comma stripper. Hand-edited configs often leave a comma
# before a closing ``}`` or ``]`` (e.g. ``{"mcpServers": {...},}``), which is
# invalid JSON and would otherwise raise JSONDecodeError -> silently 0 servers
# (review P1). Group 1 captures a full quoted string so a comma INSIDE a string
# value (e.g. ``"args": ["a,"]``) is preserved verbatim; otherwise we keep just
# the bracket and drop the dangling comma. Applied AFTER comment stripping (so
# ``},  // note`` -> ``}`` first) and BEFORE json.loads.
_TRAILING_COMMA_PATTERN = re.compile(
    r'("(?:\\.|[^"\\])*")|,(\s*[}\]])'
)


def _strip_jsonc_comments(raw: str) -> str:
    """Remove // and /* */ comments from JSONC text, preserving string literals."""
    def _replace(match: "re.Match") -> str:
        # Group 1 is a quoted string — keep it verbatim. Comment groups -> "".
        if match.group(1):
            return match.group(1)
        return ""

    return _JSONC_PATTERN.sub(_replace, raw)


def _strip_trailing_commas(raw: str) -> str:
    """Remove trailing commas before } or ], preserving commas inside strings."""
    def _replace(match: "re.Match") -> str:
        # Group 1 is a quoted string — keep it verbatim (commas inside stay).
        # Otherwise group 2 is the bracket after a dangling comma; drop comma.
        if match.group(1) is not None:
            return match.group(1)
        return match.group(2)

    return _TRAILING_COMMA_PATTERN.sub(_replace, raw)


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
        Extract GitHub Copilot CLI MCP configuration on macOS.

        Reads ``~/.copilot/mcp-config.json`` for every relevant user (root-aware
        via ``extract_ide_global_configs_with_root_support``).

        Returns:
            Dict with a ``projects`` array, or None if no configs found.
        """
        projects = extract_ide_global_configs_with_root_support(
            self._extract_cli_configs_for_user,
            tool_name=_TOOL_NAME,
        )

        if not projects:
            return None

        return {"projects": projects}

    def _extract_cli_configs_for_user(self, user_home: Path) -> List[Dict]:
        """
        Extract the Copilot CLI MCP config for a single user.

        Reads ``user_home/.copilot/mcp-config.json`` and returns a single-entry
        list with the ``~/.copilot`` directory as the project path, or an empty
        list when the file is absent, unparseable, or has no servers.
        """
        copilot_dir = user_home / _CLI_DIR_NAME
        config_path = copilot_dir / _MCP_CONFIG_FILENAME

        config = self._read_cli_mcp_config(config_path, str(copilot_dir))
        return [config] if config else []

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
            Dict with ``path`` and ``mcpServers`` keys, or None.
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
                return {
                    "path": tool_path,
                    "mcpServers": mcp_servers_array,
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
