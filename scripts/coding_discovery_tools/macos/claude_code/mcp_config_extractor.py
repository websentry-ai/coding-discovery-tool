"""
MCP config extraction for Claude Code on macOS systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import (
    extract_claude_mcp_fields,
    extract_dual_path_configs_with_root_support,
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
        
        Checks two possible locations:
        1. ~/.claude.json (preferred - main Claude Code config file)
        2. ~/.claude/mcp.json (fallback - separate MCP config file)
        
        When running as root, collects MCP configs from ALL user directories.
        
        Extracts only MCP-related fields (mcpServers, mcpContextUris, 
        enabledMcpjsonServers, disabledMcpjsonServers) from the config file.
        
        Returns:
            Dict with MCP config info (projects array) or None if not found
        """
        # Extract configs using dual-path helper
        all_projects = extract_dual_path_configs_with_root_support(
            self.MCP_CONFIG_PATH_PREFERRED,
            self.MCP_CONFIG_PATH_FALLBACK,
            self._extract_from_config_file,
            tool_name="Claude Code"
        )
        
        # Return None if no configs found
        if not all_projects:
            return None
        
        return {
            "projects": all_projects
        }
    
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
            
            stat = config_path.stat()
            content = config_path.read_text(encoding='utf-8', errors='replace')
            
            # Parse JSON
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {config_path}: {e}")
                return []
            
            # Extract only MCP-related configuration
            projects = extract_claude_mcp_fields(config_data)
            return projects
        except PermissionError as e:
            logger.warning(f"Permission denied reading MCP config {config_path}: {e}")
            return []
        except Exception as e:
            logger.warning(f"Error reading MCP config {config_path}: {e}")
            return []

