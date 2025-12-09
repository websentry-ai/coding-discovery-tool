"""
Claude Code settings extraction for Windows systems.

Extracts permission settings from:
- User Settings (global): %USERPROFILE%\.claude\settings.json
- Project Settings: **\.claude\settings.json and **\.claude\settings.local.json
- Enterprise Managed: C:\Program Files\ClaudeCode\managed-settings.json
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict

from ...coding_tool_base import BaseClaudeSettingsExtractor
from ...constants import MAX_SEARCH_DEPTH
from ...windows_extraction_helpers import (
    should_skip_path,
    read_file_content,
    get_windows_system_directories,
    is_running_as_admin,
)

logger = logging.getLogger(__name__)


class WindowsClaudeSettingsExtractor(BaseClaudeSettingsExtractor):
    """Extractor for Claude Code settings on Windows systems."""

    # User settings path
    USER_SETTINGS_PATH = Path.home() / ".claude" / "settings.json"
    
    # Managed settings path
    MANAGED_SETTINGS_PATH = Path("C:\\Program Files\\ClaudeCode\\managed-settings.json")

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
        Extract user settings from %USERPROFILE%\.claude\settings.json.
        
        When running as admin, scans all user directories.
        
        Returns:
            List of settings dicts (usually one, but can be multiple if running as admin)
        """
        settings_list = []
        
        def extract_for_user(user_home: Path) -> None:
            """Extract user settings for a specific user."""
            user_settings_path = user_home / ".claude" / "settings.json"
            
            if user_settings_path.exists() and user_settings_path.is_file():
                try:
                    settings_dict = self._parse_settings_file(
                        user_settings_path,
                        "user"
                    )
                    if settings_dict:
                        settings_list.append(settings_dict)
                except Exception as e:
                    logger.debug(f"Error extracting user settings from {user_settings_path}: {e}")
        
        # When running as admin, scan all user directories
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            extract_for_user(user_dir)
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        else:
            # Check current user
            extract_for_user(Path.home())
        
        return settings_list

    def _extract_project_settings(self) -> List[Dict]:
        """
        Extract project settings from **\.claude\settings.json and **\.claude\settings.local.json.
        
        Skips the global user .claude directory (%USERPROFILE%\.claude) to avoid duplicates.
        
        Returns:
            List of settings dicts
        """
        settings_list = []
        
        # Get global user .claude directories to skip (when running as admin, check all users)
        global_claude_dirs = set()
        if is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        global_claude = user_dir / ".claude"
                        if global_claude.exists():
                            global_claude_dirs.add(global_claude)
        else:
            # Add current user's global .claude directory
            global_claude = Path.home() / ".claude"
            if global_claude.exists():
                global_claude_dirs.add(global_claude)
        
        # Scan entire filesystem from root drive
        root_drive = Path.home().anchor  # Gets the root drive like "C:\"
        root_path = Path(root_drive)
        
        try:
            system_dirs = get_windows_system_directories()
            top_level_dirs = [item for item in root_path.iterdir() 
                            if item.is_dir() and not should_skip_path(item, system_dirs)]
            
            # Scan each top-level directory
            for top_dir in top_level_dirs:
                try:
                    self._walk_for_settings_files(
                        root_path, top_dir, settings_list, system_dirs, global_claude_dirs, current_depth=1
                    )
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping {top_dir}: {e}")
                    continue
        except (PermissionError, OSError) as e:
            logger.warning(f"Error accessing root directory: {e}")
            # Fallback to current user's home directory
            logger.info("Falling back to home directory search for project settings")
            home_path = Path.home()
            system_dirs = get_windows_system_directories()
            self._walk_for_settings_files(
                home_path, home_path, settings_list, system_dirs, global_claude_dirs, current_depth=0
            )
        
        return settings_list

    def _walk_for_settings_files(
        self,
        root_path: Path,
        current_dir: Path,
        settings_list: List[Dict],
        system_dirs: set,
        global_claude_dirs: set,
        current_depth: int = 0
    ) -> None:
        """
        Recursively walk directory tree looking for .claude directories with settings files.
        
        Args:
            root_path: Root search path (for depth calculation)
            current_dir: Current directory being processed
            settings_list: List to populate with settings
            system_dirs: Set of system directory names to skip
            global_claude_dirs: Set of global user .claude directories to skip
            current_depth: Current recursion depth
        """
        # Check depth limit
        if current_depth > MAX_SEARCH_DEPTH:
            return

        try:
            for item in current_dir.iterdir():
                try:
                    # Check if we should skip this path
                    if should_skip_path(item, system_dirs):
                        continue
                    
                    # Check depth for this item
                    try:
                        depth = len(item.relative_to(root_path).parts)
                        if depth > MAX_SEARCH_DEPTH:
                            continue
                    except ValueError:
                        continue
                    
                    if item.is_dir():
                        # Found a .claude directory!
                        if item.name == ".claude":
                            # Skip if this is a global user .claude directory
                            if item in global_claude_dirs:
                                continue
                            # Extract settings from this .claude directory
                            self._extract_settings_from_claude_directory(item, settings_list)
                            # Don't recurse into .claude directory
                            continue
                        
                        # Recurse into subdirectories
                        self._walk_for_settings_files(
                            root_path, item, settings_list, system_dirs, global_claude_dirs, current_depth + 1
                        )
                    
                except (PermissionError, OSError):
                    continue
                except Exception as e:
                    logger.debug(f"Error processing {item}: {e}")
                    continue
                    
        except (PermissionError, OSError):
            pass
        except Exception as e:
            logger.debug(f"Error walking {current_dir}: {e}")

    def _extract_settings_from_claude_directory(
        self, claude_dir: Path, settings_list: List[Dict]
    ) -> None:
        """
        Extract settings files from a .claude directory.
        
        Args:
            claude_dir: Path to .claude directory
            settings_list: List to populate with settings
        """
        # Check for settings.json
        settings_file = claude_dir / "settings.json"
        if settings_file.exists() and settings_file.is_file():
            try:
                settings_dict = self._parse_settings_file(settings_file, "project")
                if settings_dict:
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting settings from {settings_file}: {e}")
        
        # Check for settings.local.json
        settings_local_file = claude_dir / "settings.local.json"
        if settings_local_file.exists() and settings_local_file.is_file():
            try:
                settings_dict = self._parse_settings_file(settings_local_file, "project")
                if settings_dict:
                    settings_list.append(settings_dict)
            except Exception as e:
                logger.debug(f"Error extracting settings from {settings_local_file}: {e}")

    def _extract_managed_settings(self) -> List[Dict]:
        """
        Extract managed settings from C:\Program Files\ClaudeCode\managed-settings.json.
        
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

    def _parse_settings_file(self, settings_path: Path, settings_source: str) -> Optional[Dict]:
        """
        Parse a settings.json file and extract permission settings.
        
        Args:
            settings_path: Path to the settings.json file
            settings_source: Source type ("user", "project", or "managed")
            
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
            
            # Build settings dict
            settings_dict = {
                "tool_name": "Claude Code",
                "settings_source": settings_source,
                "settings_path": str(settings_path),
                "raw_settings": settings_data,  #full settings JSON for backend
                "permissions": {
                    "defaultMode": permissions.get("defaultMode"),
                    "allow": permissions.get("allow", []),
                    "deny": permissions.get("deny", []),
                    "additionalDirectories": permissions.get("additionalDirectories", [])
                },
                "sandbox": {
                    "enabled": sandbox.get("enabled")
                }
            }
            
            return settings_dict
            
        except PermissionError as e:
            logger.warning(f"Permission denied reading settings file {settings_path}: {e}")
            return None
        except Exception as e:
            logger.warning(f"Error reading settings file {settings_path}: {e}")
            return None

