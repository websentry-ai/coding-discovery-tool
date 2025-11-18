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

    MCP_CONFIG_PATH = Path.home() / ".claude.json"

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Claude Code MCP configuration on macOS.
        
        Extracts only MCP-related fields (mcpServers, mcpContextUris, 
        enabledMcpjsonServers, disabledMcpjsonServers) from the config file.
        
        Returns:
            Dict with MCP config info or None if not found
        """
        if not self.MCP_CONFIG_PATH.exists():
            return None

        try:
            stat = self.MCP_CONFIG_PATH.stat()
            content = self.MCP_CONFIG_PATH.read_text(encoding='utf-8', errors='replace')
            
            # Parse JSON
            try:
                config_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in MCP config {self.MCP_CONFIG_PATH}: {e}")
                return None

            # Extract only MCP-related configuration
            projects = extract_claude_mcp_fields(config_data)
            
            # Return projects array (even if empty)
            return {
                "projects": projects
            }
        except PermissionError as e:
            logger.warning(f"Permission denied reading MCP config {self.MCP_CONFIG_PATH}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading MCP config {self.MCP_CONFIG_PATH}: {e}")
            return None

