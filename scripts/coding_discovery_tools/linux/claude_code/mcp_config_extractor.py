"""
MCP configuration extraction for Claude Code on Linux
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime

from ...coding_tool_base import BaseMCPConfigExtractor

logger = logging.getLogger(__name__)


class LinuxClaudeMCPConfigExtractor(BaseMCPConfigExtractor):
    """MCP config extractor for Claude Code on Linux systems."""

    def extract_mcp_config(self) -> Optional[Dict]:
        """
        Extract MCP configuration for Claude Code on Linux.

        Returns:
            Dict with MCP config info or None if not found
        """
        # Claude Code stores config in ~/.config/claude/claude_desktop_config.json
        config_file = Path.home() / ".config" / "claude" / "claude_desktop_config.json"

        if not config_file.exists():
            # Try alternative locations
            alternative_paths = [
                Path.home() / ".claude" / "claude_desktop_config.json",
                Path.home() / ".config" / "claude-code" / "config.json",
                Path.home() / ".claude" / "config.json",
            ]

            for alt_path in alternative_paths:
                if alt_path.exists():
                    config_file = alt_path
                    break
            else:
                logger.debug("Claude Code config file not found")
                return None

        return self._extract_mcp_config_from_file(config_file)

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