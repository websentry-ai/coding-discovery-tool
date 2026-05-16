"""MCP config extraction for Claude Code on Linux."""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseMCPConfigExtractor
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_claude_mcp_fields,
    extract_dual_path_configs_with_root_support,
    walk_for_claude_project_mcp_configs,
    extract_managed_mcp_config,
    extract_claude_plugin_mcp_configs_with_root_support,
    extract_claudeai_mcp_servers_with_root_support,
)

logger = logging.getLogger(__name__)


class LinuxClaudeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Claude Code MCP config on Linux systems."""

    MCP_CONFIG_PATH_PREFERRED = Path.home() / ".claude.json"
    MCP_CONFIG_PATH_FALLBACK = Path.home() / ".claude" / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        all_projects: List[Dict] = []

        extract_managed_mcp_config(all_projects)

        user_local_configs = extract_dual_path_configs_with_root_support(
            self.MCP_CONFIG_PATH_PREFERRED,
            self.MCP_CONFIG_PATH_FALLBACK,
            self._extract_from_config_file,
            tool_name="Claude Code",
        )
        all_projects.extend(user_local_configs)

        all_projects.extend(self._extract_project_scope_configs())
        extract_claude_plugin_mcp_configs_with_root_support(all_projects)
        extract_claudeai_mcp_servers_with_root_support(all_projects)

        return {"projects": all_projects} if all_projects else None

    def _extract_project_scope_configs(self) -> List[Dict]:
        """Walk each user home for project-scope .mcp.json files."""
        projects: List[Dict] = []

        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)

        for user_home in get_linux_user_homes():
            try:
                walk_for_claude_project_mcp_configs(
                    user_home, user_home, projects, should_skip, current_depth=0
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return projects

    def _extract_from_config_file(self, config_path: Path) -> List[Dict]:
        try:
            if not config_path.exists():
                return []
            content = config_path.read_text(encoding="utf-8", errors="replace")
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {config_path}: {e}")
                return []
            return extract_claude_mcp_fields(config_data, config_path)
        except PermissionError as e:
            logger.warning(f"Permission denied reading {config_path}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error reading MCP config {config_path}: {e}")
            return []
