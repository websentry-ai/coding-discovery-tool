"""
MCP configuration extraction for Cursor on Linux
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

from ...coding_tool_base import BaseMCPConfigExtractor

logger = logging.getLogger(__name__)


class LinuxCursorMCPConfigExtractor(BaseMCPConfigExtractor):
    """MCP config extractor for Cursor on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration for Cursor on Linux.

        Returns:
            Dict with MCP config info or None if not found
        """
        # Cursor stores settings in ~/.config/Cursor/User/globalStorage/saoudrizwan.claude-dev
        config_base = Path.home() / ".config" / "Cursor" / "User" / "globalStorage" / "saoudrizwan.claude-dev"

        if not config_base.exists():
            # Try alternative location
            config_base = Path.home() / ".cursor" / "globalStorage" / "saoudrizwan.claude-dev"
            if not config_base.exists():
                logger.debug("Cursor MCP config directory not found")
                return None

        # Look for MCP configuration file
        mcp_config_file = config_base / "mcpServers.json"
        if not mcp_config_file.exists():
            # Try alternative name
            mcp_config_file = config_base / "mcp_servers.json"
            if not mcp_config_file.exists():
                logger.debug("No MCP config file found for Cursor")
                return None

        return self._extract_mcp_config_from_file(mcp_config_file)

    def _extract_mcp_config_from_file(self, file_path: Path) -> Optional[Dict]:
        """
        Extract MCP config from a file.

        Args:
            file_path: Path to the MCP config file

        Returns:
            Dict with file metadata and content, or None if extraction fails
        """
        try:
            if not file_path.exists():
                return None

            stat = file_path.stat()
            content = file_path.read_text(encoding='utf-8', errors='replace')

            return {
                "file_path": str(file_path),
                "file_name": file_path.name,
                "content": content,
                "size": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat() + "Z"
            }
        except Exception as e:
            logger.warning(f"Error reading MCP config file {file_path}: {e}")
            return None