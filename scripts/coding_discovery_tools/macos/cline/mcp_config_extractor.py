"""
MCP config extraction for Cline on macOS systems.

Cline stores MCP configuration in:
~/Library/Application Support/<IDE>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json

Where <IDE> can be: Code, Cursor, Windsurf, etc.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseMCPConfigExtractor
from ...mcp_extraction_helpers import transform_mcp_servers_to_array

logger = logging.getLogger(__name__)


class MacOSClineMCPConfigExtractor(BaseMCPConfigExtractor):
    """Extractor for Cline MCP config on macOS systems."""

    # Common IDE names where Cline can be installed
    IDE_NAMES = ["Cursor", "Windsurf", "VSCode"]

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract Cline MCP configuration on macOS.
        
        Cline stores MCP configuration in a JSON file at:
        ~/Library/Application Support/<IDE>/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json
        
        Where <IDE> can be Code, Cursor, Windsurf, etc.
        
        Returns:
            Dict with projects array containing MCP configs, or None if no configs found
        """
        projects = []
        base_path = Path.home() / "Library" / "Application Support"
        
        # Check each possible IDE directory
        for ide_name in self.IDE_NAMES:
            mcp_settings_path = (
                base_path / ide_name / "User" / "globalStorage" / 
                "saoudrizwan.claude-dev" / "settings" / "cline_mcp_settings.json"
            )
            
            if mcp_settings_path.exists():
                project_config = self._extract_from_path(mcp_settings_path, ide_name)
                if project_config:
                    projects.append(project_config)
        
        # Return None if no MCP configs found
        if not projects:
            return None
        
        return {
            "projects": projects
        }

    def _extract_from_path(self, mcp_settings_path: Path, ide_name: str) -> Optional[Dict]:
        """
        Extract MCP config from a specific path.
        
        Args:
            mcp_settings_path: Path to the cline_mcp_settings.json file
            ide_name: Name of the IDE (for path identification)
            
        Returns:
            Dict with path and mcpServers, or None if extraction fails
        """
        try:
            content = mcp_settings_path.read_text(encoding='utf-8', errors='replace')
            config_data = json.loads(content)
            
            mcp_servers_obj = config_data.get("mcpServers", {})
            
            # Transform mcpServers from object to array
            # This excludes 'env' field for security (similar to Cursor/Claude)
            mcp_servers_array = transform_mcp_servers_to_array(mcp_servers_obj)
            
            # Return None if no MCP servers configured
            if not mcp_servers_array:
                return None
            
            # Return as a project (using the globalStorage directory path)
            return {
                "path": str(mcp_settings_path.parent.parent.parent.parent),  # globalStorage directory
                "mcpServers": mcp_servers_array
            }
        except json.JSONDecodeError as e:
            logger.warning(f"Invalid JSON in Cline MCP config {mcp_settings_path}: {e}")
        except PermissionError as e:
            logger.warning(f"Permission denied reading Cline MCP config {mcp_settings_path}: {e}")
        except Exception as e:
            logger.warning(f"Error reading Cline MCP config {mcp_settings_path}: {e}")
        
        return None
