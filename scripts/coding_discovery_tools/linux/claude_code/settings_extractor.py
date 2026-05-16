"""
Claude Code settings extraction for Linux.

Extracts permission settings from file-based scopes only (no plist/MDM on Linux):
1. User scope:    ~/.claude/settings.json
2. Project scope: **/.claude/settings.json
3. Local scope:   **/.claude/settings.local.json

Linux has no macOS managed-settings or plist equivalents.
"""

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseClaudeSettingsExtractor
from ...linux_extraction_helpers import (
    get_linux_user_homes,
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    should_skip_path,
    should_skip_system_path,
)
from ...macos_extraction_helpers import read_file_content, walk_for_tool_directories

logger = logging.getLogger(__name__)


class LinuxClaudeSettingsExtractor(BaseClaudeSettingsExtractor):
    """Extractor for Claude Code settings on Linux systems."""

    def extract_settings(self) -> Optional[List[Dict]]:
        all_settings: List[Dict] = []

        all_settings.extend(self._extract_user_settings())
        all_settings.extend(self._extract_project_settings())

        return all_settings if all_settings else None

    # ------------------------------------------------------------------
    # User settings (~/.claude/settings.json)
    # ------------------------------------------------------------------

    def _extract_user_settings(self) -> List[Dict]:
        settings_list: List[Dict] = []

        def extract_for_user(user_home: Path) -> None:
            path = user_home / ".claude" / "settings.json"
            if path.exists() and path.is_file():
                try:
                    if should_process_file(path, user_home):
                        s = self._parse_settings_file(path, "user")
                        if s:
                            settings_list.append(s)
                except Exception as e:
                    logger.error(f"Error extracting user settings from {path}: {e}")

        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            extract_for_user(Path.home())

        return settings_list

    # ------------------------------------------------------------------
    # Project settings (**/.claude/settings.json and settings.local.json)
    # ------------------------------------------------------------------

    def _extract_project_settings(self) -> List[Dict]:
        settings_list: List[Dict] = []

        # Collect global .claude dirs to skip (avoid duplicating user settings)
        global_claude_dirs: set = set()
        for user_home in get_linux_user_homes():
            g = user_home / ".claude"
            if g.exists():
                global_claude_dirs.add(g)

        dummy: Dict = {}

        def extract_from_claude_dir(claude_dir: Path, _projects_by_root: Dict) -> None:
            if claude_dir in global_claude_dirs:
                return
            self._extract_settings_from_claude_directory(claude_dir, settings_list)

        for user_home in get_linux_user_homes():
            try:
                walk_for_tool_directories(
                    user_home, user_home, ".claude",
                    extract_from_claude_dir, dummy, current_depth=0,
                )
            except (PermissionError, OSError) as e:
                logger.debug(f"Skipping {user_home}: {e}")

        return settings_list

    def _extract_settings_from_claude_directory(
        self, claude_dir: Path, settings_list: List[Dict]
    ) -> None:
        for filename, scope in [("settings.json", "project"), ("settings.local.json", "local")]:
            f = claude_dir / filename
            if f.exists() and f.is_file():
                try:
                    if should_process_file(f, claude_dir.parent):
                        s = self._parse_settings_file(f, scope)
                        if s:
                            settings_list.append(s)
                except Exception as e:
                    logger.debug(f"Error extracting settings from {f}: {e}")

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse_settings_file(self, settings_path: Path, scope: str) -> Optional[Dict]:
        try:
            if not settings_path.exists() or not settings_path.is_file():
                return None

            file_size = settings_path.stat().st_size
            content, truncated = read_file_content(settings_path, file_size)
            if truncated:
                logger.warning(f"Settings file {settings_path} truncated due to size limit")

            try:
                data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in {settings_path}: {e}")
                return None

            permissions = data.get("permissions", {})
            sandbox = data.get("sandbox", {})
            mcp_servers = data.get("mcpServers", {})
            mcp_server_names = list(mcp_servers.keys()) if isinstance(mcp_servers, dict) else []

            return {
                "tool_name": "Claude Code",
                "scope": scope,
                "settings_path": str(settings_path),
                "raw_settings": data,
                "permissions": {
                    "defaultMode": permissions.get("defaultMode"),
                    "allow": permissions.get("allow", []),
                    "deny": permissions.get("deny", []),
                    "ask": permissions.get("ask", []),
                    "additionalDirectories": permissions.get("additionalDirectories", []),
                },
                "mcp_servers": mcp_server_names,
                "mcp_policies": {
                    "allowedMcpServers": data.get("allowedMcpServers", []),
                    "deniedMcpServers": data.get("deniedMcpServers", []),
                },
                "sandbox": {
                    "enabled": sandbox.get("enabled"),
                },
            }
        except PermissionError as e:
            logger.warning(f"Permission denied reading {settings_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading {settings_path}: {e}")
            return None
