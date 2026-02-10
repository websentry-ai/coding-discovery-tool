"""
Claude Code settings extraction for macOS systems.

Extracts permission settings from 4 scopes (in priority order, highest to lowest):
1. Managed Scope: /Library/Application Support/ClaudeCode/managed-settings.json
2. Local Scope: **/.claude/settings.local.json (project-specific, not committed)
3. Project Scope: **/.claude/settings.json (project-specific, committed)
4. User Scope: ~/.claude/settings.json (global user settings)

For each settings file found, extracts:
- Permissions: allow, deny, ask lists
- MCP Servers: keys inside mcpServers
- Policies: allowedMcpServers, deniedMcpServers
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict

from ...coding_tool_base import BaseClaudeSettingsExtractor
from ...constants import MAX_SEARCH_DEPTH, MAX_CONFIG_FILE_SIZE
from ...macos_extraction_helpers import (
    is_running_as_root,
    scan_user_directories,
    should_process_file,
    walk_for_tool_directories,
    read_file_content,
)

logger = logging.getLogger(__name__)


class MacOSClaudeSettingsExtractor(BaseClaudeSettingsExtractor):
    """Extractor for Claude Code settings on macOS systems."""

    # User settings path
    USER_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
    
    # Managed settings path
    MANAGED_SETTINGS_PATH = Path("/Library/Application Support/ClaudeCode/managed-settings.json")

    def extract_settings(self) -> Optional[List[Dict]]:
        """
        Extract Claude Code permission settings from all sources.
        
        Returns:
            List of settings dicts or None if no settings found
        """
        all_settings = []
        
        # Extract user settings (global)
        user_settings = self._extract_user_settings()
        if user_settings:
            all_settings.extend(user_settings)
        
        # Extract project settings
        project_settings = self._extract_project_settings()
        if project_settings:
            all_settings.extend(project_settings)
        
        # Extract managed settings (enterprise)
        managed_settings = self._extract_managed_settings()
        if managed_settings:
            all_settings.extend(managed_settings)
        
        return all_settings if all_settings else None

    def _extract_user_settings(self) -> List[Dict]:
        """
        Extract user settings from ~/.claude/settings.json.
        
        When running as root, scans all user directories.
        
        Returns:
            List of settings dicts (usually one, but can be multiple if running as root)
        """
        settings_list = []
        
        def extract_for_user(user_home: Path) -> None:
            """Extract user settings for a specific user."""
            user_settings_path = user_home / ".claude" / "settings.json"
            
            logger.debug(f"Checking for user settings at: {user_settings_path}")
            logger.debug(f"File exists: {user_settings_path.exists()}, is_file: {user_settings_path.is_file() if user_settings_path.exists() else False}")
            
            if user_settings_path.exists() and user_settings_path.is_file():
                try:
                    if should_process_file(user_settings_path, user_home):
                        logger.debug(f"Processing user settings file: {user_settings_path}")
                        settings_dict = self._parse_settings_file(
                            user_settings_path,
                            "user"
                        )
                        if settings_dict:
                            logger.info(f"  ✓ Successfully extracted user settings from {user_settings_path}")
                            settings_list.append(settings_dict)
                        else:
                            logger.warning(f"  ⚠ Settings dict is None after parsing {user_settings_path}")
                    else:
                        logger.debug(f"Skipping file (should_process_file returned False): {user_settings_path}")
                except Exception as e:
                    logger.error(f"Error extracting user settings from {user_settings_path}: {e}", exc_info=True)
            else:
                logger.debug(f"User settings file not found at: {user_settings_path}")
        
        # When running as root, scan all user directories
        if is_running_as_root():
            scan_user_directories(extract_for_user)
        else:
            # Check current user
            extract_for_user(Path.home())
        
        return settings_list

    def _extract_project_settings(self) -> List[Dict]:
        """
        Extract project settings from **/.claude/settings.json and **/.claude/settings.local.json.
        
        Skips the global user .claude directory (~/.claude) to avoid duplicates.
        
        Returns:
            List of settings dicts
        """
        settings_list = []
        root_path = Path("/")
        
        # Get global user .claude directories to skip (when running as root, check all users)
        global_claude_dirs = set()
        if is_running_as_root():
            def collect_global_dirs(user_home: Path) -> None:
                global_claude = user_home / ".claude"
                if global_claude.exists():
                    global_claude_dirs.add(global_claude)
            scan_user_directories(collect_global_dirs)
        else:
            # Add current user's global .claude directory
            global_claude = Path.home() / ".claude"
            if global_claude.exists():
                global_claude_dirs.add(global_claude)
        
        # Use the generic walk_for_tool_directories helper
        # Note: walk_for_tool_directories expects projects_by_root dict, but we use a list
        # So we create a dummy dict and extract from it
        projects_by_root = {}
        
        def extract_from_claude_dir(claude_dir: Path, projects_by_root: Dict) -> None:
            """Extract settings from a .claude directory, skipping global user directories."""
            # Skip if this is a global user .claude directory
            if claude_dir in global_claude_dirs:
                return
            self._extract_settings_from_claude_directory(claude_dir, settings_list)
        
        # Use generic helper for walking directories
        try:
            if root_path == Path("/"):
                # Get top-level directories and walk each
                from ...macos_extraction_helpers import get_top_level_directories
                top_level_dirs = get_top_level_directories(root_path)
                for top_dir in top_level_dirs:
                    try:
                        walk_for_tool_directories(
                            root_path, top_dir, ".claude", extract_from_claude_dir,
                            projects_by_root, current_depth=1
                        )
                    except (PermissionError, OSError) as e:
                        logger.debug(f"Skipping {top_dir}: {e}")
                        continue
            else:
                walk_for_tool_directories(
                    root_path, root_path, ".claude", extract_from_claude_dir,
                    projects_by_root, current_depth=0
                )
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to home directory
            logger.info("Falling back to home directory search for project settings")
            home_path = Path.home()
            walk_for_tool_directories(
                home_path, home_path, ".claude", extract_from_claude_dir,
                projects_by_root, current_depth=0
            )
        
        return settings_list

    def _extract_settings_from_claude_directory(
        self, claude_dir: Path, settings_list: List[Dict]
    ) -> None:
        """
        Extract settings files from a .claude directory.

        Extracts two types of project-level settings:
        - settings.json: Project scope (committed to version control)
        - settings.local.json: Local scope (not committed, user-specific overrides)

        Args:
            claude_dir: Path to .claude directory
            settings_list: List to populate with settings
        """
        # Check for settings.json (Project scope)
        settings_file = claude_dir / "settings.json"
        if settings_file.exists() and settings_file.is_file():
            try:
                if should_process_file(settings_file, claude_dir.parent):
                    settings_dict = self._parse_settings_file(settings_file, "project")
                    if settings_dict:
                        settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting settings from {settings_file}: {e}")

        # Check for settings.local.json (Local scope - higher priority than project)
        settings_local_file = claude_dir / "settings.local.json"
        if settings_local_file.exists() and settings_local_file.is_file():
            try:
                if should_process_file(settings_local_file, claude_dir.parent):
                    settings_dict = self._parse_settings_file(settings_local_file, "local")
                    if settings_dict:
                        settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting settings from {settings_local_file}: {e}")

    def _extract_managed_settings(self) -> List[Dict]:
        """
        Extract managed settings from /Library/Application Support/ClaudeCode/managed-settings.json.
        
        Returns:
            List of settings dicts (usually one or zero)
        """
        settings_list = []
        
        if self.MANAGED_SETTINGS_PATH.exists() and self.MANAGED_SETTINGS_PATH.is_file():
            try:
                settings_dict = self._parse_settings_file(
                    self.MANAGED_SETTINGS_PATH,
                    "managed"
                )
                if settings_dict:
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting managed settings from {self.MANAGED_SETTINGS_PATH}: {e}")
        
        return settings_list

    def _parse_settings_file(self, settings_path: Path, scope: str) -> Optional[Dict]:
        """
        Parse a settings.json file and extract permission settings.

        Args:
            settings_path: Path to the settings.json file
            scope: Scope type - one of:
                   - "managed": Enterprise managed settings (highest priority)
                   - "local": Project-local settings (.claude/settings.local.json)
                   - "project": Project settings (.claude/settings.json)
                   - "user": User global settings (~/.claude/settings.json)

        Returns:
            Settings dict or None if parsing fails
        """
        try:
            if not settings_path.exists() or not settings_path.is_file():
                return None

            # Use helper function to read file content (handles size limits)
            file_size = settings_path.stat().st_size
            content, truncated = read_file_content(settings_path, file_size)

            # If file was truncated, we still try to parse what we have
            if truncated:
                logger.warning(
                    f"Settings file {settings_path} was truncated due to size limit"
                )

            # Parse JSON
            try:
                settings_data = json.loads(content)
            except json.JSONDecodeError as e:
                logger.warning(f"Invalid JSON in settings file {settings_path}: {e}")
                return None

            # Extract permissions and sandbox settings
            permissions = settings_data.get("permissions", {})
            sandbox = settings_data.get("sandbox", {})

            # Extract MCP servers (get the keys/names of configured servers)
            mcp_servers = settings_data.get("mcpServers", {})
            mcp_server_names = list(mcp_servers.keys()) if isinstance(mcp_servers, dict) else []

            # Extract MCP policies
            allowed_mcp_servers = settings_data.get("allowedMcpServers", [])
            denied_mcp_servers = settings_data.get("deniedMcpServers", [])

            logger.debug(f"Parsed settings from {settings_path}:")
            logger.debug(f"  - Scope: {scope}")
            logger.debug(f"  - Top-level keys in settings_data: {list(settings_data.keys())}")
            logger.debug(f"  - Permissions keys: {list(permissions.keys())}")
            logger.debug(f"  - Allow rules count: {len(permissions.get('allow', []))}")
            logger.debug(f"  - Deny rules count: {len(permissions.get('deny', []))}")
            logger.debug(f"  - Ask rules count: {len(permissions.get('ask', []))}")
            logger.debug(f"  - MCP servers: {mcp_server_names}")

            # Build settings dict with scope information
            settings_dict = {
                "tool_name": "Claude Code",
                "scope": scope,
                "settings_path": str(settings_path),
                "raw_settings": settings_data,  # full settings JSON for backend
                "permissions": {
                    "defaultMode": permissions.get("defaultMode"),
                    "allow": permissions.get("allow", []),
                    "deny": permissions.get("deny", []),
                    "ask": permissions.get("ask", []),
                    "additionalDirectories": permissions.get("additionalDirectories", [])
                },
                "mcp_servers": mcp_server_names,
                "mcp_policies": {
                    "allowedMcpServers": allowed_mcp_servers,
                    "deniedMcpServers": denied_mcp_servers
                },
                "sandbox": {
                    "enabled": sandbox.get("enabled")
                }
            }

            logger.debug(f"Built settings dict with scope={scope}, {len(settings_dict.get('permissions', {}).get('allow', []))} allow rules, {len(settings_dict.get('permissions', {}).get('deny', []))} deny rules, {len(settings_dict.get('permissions', {}).get('ask', []))} ask rules")

            return settings_dict

        except PermissionError as e:
            logger.warning(f"Permission denied reading settings file {settings_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading settings file {settings_path}: {e}")
            return None

