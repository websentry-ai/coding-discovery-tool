"""
MCP config extraction for Claude Code on macOS systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...macos_extraction_helpers import (
    get_top_level_directories,
    should_skip_path,
    should_skip_system_path,
)
from ...mcp_extraction_helpers import (
    extract_claude_mcp_fields,
    extract_dual_path_configs_with_root_support,
    walk_for_claude_project_mcp_configs,
    extract_managed_mcp_config,
    extract_claude_plugin_mcp_configs_with_root_support,
)

logger = logging.getLogger(__name__)


class MacOSClaudeMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Claude Code MCP config on macOS systems."""

    # Try both possible locations: ~/.claude.json (preferred) and ~/.claude/mcp.json (fallback)
    MCP_CONFIG_PATH_PREFERRED = Path.home() / ".claude.json"
    MCP_CONFIG_PATH_FALLBACK = Path.home() / ".claude" / "mcp.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Claude Code MCP configuration on macOS.

        Checks multiple sources:
        1. /Library/Application Support/ClaudeCode/managed-mcp.json (enterprise managed)
        2. ~/.claude.json (preferred - main Claude Code config file with user/local scope)
        3. ~/.claude/mcp.json (fallback - separate MCP config file)
        4. Project-scope .mcp.json files at project roots throughout the filesystem
        5. Plugin MCP servers from ~/.claude/plugins/*/plugin.json

        When running as root, collects MCP configs from ALL user directories.

        Extracts only MCP-related fields (mcpServers, mcpContextUris,
        enabledMcpjsonServers, disabledMcpjsonServers) from the config file.
        
        Returns:
            Dict with MCP config info (projects array) or None if not found
        """
        all_projects = []

        extract_managed_mcp_config(all_projects)

        # Extract user/local scope configs from ~/.claude.json or ~/.claude/mcp.json
        user_local_configs = extract_dual_path_configs_with_root_support(
            self.MCP_CONFIG_PATH_PREFERRED,
            self.MCP_CONFIG_PATH_FALLBACK,
            self._extract_from_config_file,
            tool_name="Claude Code"
        )
        all_projects.extend(user_local_configs)

        # Extract project-scope configs from .mcp.json files at project roots
        project_scope_configs = self._extract_project_scope_configs()
        all_projects.extend(project_scope_configs)

        # Extract plugin MCP configs from ~/.claude/plugins/*/plugin.json
        extract_claude_plugin_mcp_configs_with_root_support(all_projects)

        # Return None if no configs found
        if not all_projects:
            return None
        
        return {
            "projects": all_projects
        }

    def _extract_project_scope_configs(self) -> List[Dict]:
        """
        Extract project-scope MCP configs from .mcp.json files at project roots.
        """
        projects = []
        root_path = Path("/")

        def should_skip(item: Path) -> bool:
            return should_skip_path(item) or should_skip_system_path(item)

        try:
            top_level_dirs = get_top_level_directories(root_path)

            for top_dir in top_level_dirs:
                try:
                    walk_for_claude_project_mcp_configs(
                        root_path, top_dir, projects,
                        should_skip, current_depth=1
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            logger.info("Falling back to home directory search")
            home_path = Path.home()

            walk_for_claude_project_mcp_configs(
                home_path, home_path, projects,
                should_skip, current_depth=0
            )

        return projects
    
    def _extract_from_config_file(self, config_path: Path) -> List[Dict]:
        """
        Extract MCP projects from a single config file.
        
        Args:
            config_path: Path to the config file
            
        Returns:
            List of project dicts or empty list if extraction fails
        """
        try:
            # Check if file exists first to avoid unnecessary warnings
            if not config_path.exists():
                return []
            content = config_path.read_text(encoding='utf-8', errors='replace')
            
            # Parse JSON
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {config_path}: {e}")
                return []
            
            # Extract only MCP-related configuration
            projects = extract_claude_mcp_fields(config_data, config_path)
            return projects
        except PermissionError as e:
            logger.warning(f"Permission denied reading MCP config {config_path}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error reading MCP config {config_path}: {e}")
            return []

