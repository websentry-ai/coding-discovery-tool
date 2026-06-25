"""
MCP config extraction for Augment Code on macOS.

Augment loads MCP servers from the User config (``~/.augment/settings.json``,
optional ``~/.augment/mcp*.json``) and Workspace files
(``<project>/.augment/settings.json``). MCP servers may live at the top-level
``mcpServers`` key OR nested under ``augment.advanced.mcpServers`` (and a flat
unwrapped form is tolerated). Mirrors ``MacOSCopilotCliMCPConfigExtractor``.
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
    _strip_jsonc_comments,
    _strip_trailing_commas,
)
from .augment import _resolve_augment_dir

logger = logging.getLogger(__name__)

_TOOL_NAME = "Augment Code"
_AUGMENT_DIR_NAME = ".augment"
_SETTINGS_FILENAME = "settings.json"
# Glob for any additional user-scope MCP files (~/.augment/mcp*.json).
_MCP_FILE_GLOB = "mcp*.json"


def _extract_servers_obj(config_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Resolve the server mapping from a parsed Augment config.

    Order of precedence:
    1. top-level ``mcpServers`` (canonical wrapped form)
    2. nested ``augment.advanced.mcpServers``
    3. flat top-level object of ``{name: {config}}`` — a value counts as a server
       iff it is a dict carrying a ``command`` or ``url``.
    """
    wrapped = config_data.get("mcpServers")
    if isinstance(wrapped, dict):
        return wrapped

    advanced = config_data.get("augment")
    if isinstance(advanced, dict):
        advanced = advanced.get("advanced")
        if isinstance(advanced, dict):
            nested = advanced.get("mcpServers")
            if isinstance(nested, dict):
                return nested

    return {
        name: value
        for name, value in config_data.items()
        if isinstance(value, dict)
        and ("command" in value or "url" in value)
    }


class MacOSAugmentMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Augment Code MCP config on macOS systems."""

    def extract_mcp_config(
        self, plugin_lookup: Optional[Dict] = None
    ) -> Optional[Dict]:
        """
        Extract Augment MCP config: User (``~/.augment/settings.json`` +
        ``~/.augment/mcp*.json``, root-aware) plus Workspace
        (``<project>/.augment/settings.json``). Returns a ``projects`` dict, or
        None if empty.
        """
        projects = extract_ide_global_configs_with_root_support(
            self._extract_user_configs_for_user,
            tool_name=_TOOL_NAME,
        )

        projects.extend(self._extract_workspace_configs())

        if not projects:
            return None

        return {"projects": projects}

    def _extract_user_configs_for_user(self, user_home: Path) -> List[Dict]:
        """Read the User-scope MCP config(s) for a single user's ``~/.augment``."""
        augment_dir = _resolve_augment_dir(user_home)
        configs: List[Dict] = []

        settings_config = self._read_mcp_config(
            augment_dir / _SETTINGS_FILENAME, str(augment_dir), "user"
        )
        if settings_config:
            configs.append(settings_config)

        try:
            for mcp_file in sorted(augment_dir.glob(_MCP_FILE_GLOB)):
                config = self._read_mcp_config(mcp_file, str(augment_dir), "user")
                if config:
                    configs.append(config)
        except (PermissionError, OSError) as exc:
            logger.debug(f"Error globbing Augment MCP files in {augment_dir}: {exc}")

        return configs

    # -- Workspace scope: project-root .augment/settings.json ----------------

    def _extract_workspace_configs(self) -> List[Dict]:
        """Walk ``_workspace_search_roots`` for project ``.augment/settings.json``."""
        projects: List[Dict] = []
        for root_path, start_dir in self._workspace_search_roots():
            try:
                start_depth = len(start_dir.relative_to(root_path).parts)
            except ValueError:
                start_depth = 0
            try:
                self._walk_for_workspace_configs(
                    root_path, start_dir, projects, current_depth=start_depth
                )
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping workspace scan of {start_dir}: {exc}")
            except Exception as exc:
                logger.debug(f"Error scanning workspace dir {start_dir}: {exc}")
        return projects

    def _walk_for_workspace_configs(
        self,
        root_path: Path,
        current_dir: Path,
        projects: List[Dict],
        current_depth: int = 0,
    ) -> None:
        """Recursively look for ``<project>/.augment/settings.json`` (bounded)."""
        from ...constants import MAX_SEARCH_DEPTH

        if current_depth > MAX_SEARCH_DEPTH:
            return
        try:
            for item in current_dir.iterdir():
                try:
                    if self._should_skip_workspace_path(item):
                        continue
                    if not item.is_dir() or item.is_symlink():
                        continue
                    if item.name == _AUGMENT_DIR_NAME:
                        config = self._read_mcp_config(
                            item / _SETTINGS_FILENAME, str(item.parent), "project"
                        )
                        if config:
                            projects.append(config)
                        continue
                    self._walk_for_workspace_configs(
                        root_path, item, projects, current_depth + 1
                    )
                except (PermissionError, OSError):
                    continue
                except Exception as exc:
                    logger.debug(f"Error processing {item}: {exc}")
                    continue
        except (PermissionError, OSError):
            pass
        except Exception as exc:
            logger.debug(f"Error walking {current_dir}: {exc}")

    def _workspace_search_roots(self) -> List[Tuple[Path, Path]]:
        """``(root_path, start_dir)`` pairs for the project walk (macOS)."""
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

    def _read_mcp_config(
        self, config_path: Path, tool_path: str, scope: str
    ) -> Optional[Dict]:
        """
        Read and parse an Augment config file for MCP servers.

        Strips JSONC comments + trailing commas, resolves the server mapping from
        the top-level or nested or flat form, and transforms to the array shape.
        All IO is wrapped — never crashes.

        Returns a dict with ``path``, ``mcpServers`` and ``scope`` keys, or None.
        """
        try:
            if not config_path.is_file():
                return None

            content = config_path.read_text(encoding="utf-8", errors="replace")
            content = _strip_trailing_commas(_strip_jsonc_comments(content))
            config_data = json.loads(content)

            if not isinstance(config_data, dict):
                return None

            mcp_servers_obj = _extract_servers_obj(config_data)
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)

            if mcp_servers_array:
                return {
                    "path": tool_path,
                    "mcpServers": mcp_servers_array,
                    "scope": scope,
                }
        except json.JSONDecodeError as exc:
            logger.warning(f"Invalid JSON in {_TOOL_NAME} config {config_path}: {exc}")
        except PermissionError as exc:
            logger.debug(f"Permission denied reading {_TOOL_NAME} config {config_path}: {exc}")
        except Exception as exc:
            logger.warning(f"Error reading {_TOOL_NAME} config {config_path}: {exc}")

        return None
