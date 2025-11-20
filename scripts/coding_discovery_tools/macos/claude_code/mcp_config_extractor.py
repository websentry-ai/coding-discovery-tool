"""
MCP config extraction for Claude Code on macOS systems.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import extract_claude_mcp_fields

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
        
        Extracts only MCP-related fields (mcpServers, mcpContextUris, 
        enabledMcpjsonServers, disabledMcpjsonServers) from the config file.
        
        Returns:
            Dict with MCP config info or None if not found
        """
        # Try preferred location first
        config_path = self.MCP_CONFIG_PATH_PREFERRED
        if not config_path.exists():
            # Fallback to alternative location
            config_path = self.MCP_CONFIG_PATH_FALLBACK
            if not config_path.exists():
                return None

        try:
            stat = config_path.stat()
            content = config_path.read_text(encoding='utf-8', errors='replace')
            
            # Parse JSON
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {config_path}: {e}")
                return None

            # Extract only MCP-related configuration
            projects = extract_claude_mcp_fields(config_data)
            
            # Return projects array (even if empty)
            return {
                "projects": projects
            }
        except PermissionError as e:
            logger.warning(f"Permission denied reading MCP config {config_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading MCP config {config_path}: {e}")
            return None

