"""
Roo Code detection for Windows.

Roo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Roo Code installations by checking for:
1. IDE installations (VS Code, Cursor, Windsurf)
2. Roo extension settings in IDE global storage directories
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


class WindowsRooDetector(BaseToolDetector):
    """
    Detector for Roo Code installations on Windows systems.
    
    Roo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf)
    - Verifying Roo extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Roo Code extension
    SUPPORTED_IDES = ['Code', 'Cursor', 'Windsurf']
    
    # Roo Code extension identifier
    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Roo Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Roo Code installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as administrator, scan all user directories first
        if self._is_running_as_admin():
            user_roo_info = self._scan_user_directories()
            if user_roo_info:
                return user_roo_info
        
        # Check current user (works for both admin and regular users)
        return self._check_user_for_roo(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Roo Code version.
        
        Note: Version extraction is currently not implemented.
        
        Returns:
            None (version extraction removed per requirements)
        """
        return None

    def _is_running_as_admin(self) -> bool:
        """
        Check if the current process is running as administrator.
        
        Returns:
            True if running as administrator, False otherwise
        """
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except Exception:
            # Fallback: check if current user is Administrator or SYSTEM
            try:
                import getpass
                current_user = getpass.getuser().lower()
                return current_user in ["administrator", "system"]
            except Exception:
                return False

    def _scan_user_directories(self) -> Optional[Dict]:
        """
        Scan all user directories for Roo Code installations when running as admin.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        users_dir = Path("C:\\Users")
        if not users_dir.exists():
            return None
        
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    result = self._check_user_for_roo(user_dir)
                    if result:
                        return result
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")
                    continue
        
        return None

    def _check_user_for_roo(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Roo Code is installed for a specific user.
        
        Since Roo Code is an extension, we first check if the extension exists
        in any IDE. Only if the extension is found, we proceed with detection.
        
        This method:
        1. First checks for Roo extension in any supported IDE
        2. If extension found, returns detection result (extension can only exist if IDE is installed)
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        # First, check if Roo extension exists in any IDE
        extension_path = None
        
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_roo_extension(user_home, ide_name)
            if extension_path:
                logger.debug(f"Found Roo Code extension in {ide_name} at: {extension_path}")
                break
        
        # If no extension found, return None immediately
        if not extension_path:
            logger.debug("Roo Code extension not found in any IDE")
            return None
        
        # If extension found, IDE is installed (extension can only exist if IDE is installed)
        # Use the extension path as install_path
        return {
            "name": self.tool_name,
            "version": "Unknown",
            "install_path": str(extension_path)
        }

    def _check_roo_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Roo extension settings exist for a specific IDE.
        
        Checks both the settings file and the extension directory to handle
        cases where settings file might not exist yet but extension is installed.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension settings or extension directory if found, None otherwise
        """
        # Windows VS Code/Cursor/Windsurf global storage path
        code_base = user_home / "AppData" / "Roaming"
        settings_path = (
            code_base / ide_name / "User" / "globalStorage" /
            self.ROO_EXTENSION_ID / "settings" / "mcp_settings.json"
        )
        
        try:
            # Check if settings file exists
            if settings_path.exists():
                logger.debug(
                    f"Found Roo extension settings for {ide_name} at: {settings_path}"
                )
                # Return the settings directory path (parent of mcp_settings.json)
                return settings_path.parent
            
            # Also check if extension directory exists (extension installed but no settings yet)
            extension_dir = settings_path.parent.parent
            if extension_dir.exists():
                logger.debug(
                    f"Found Roo extension directory for {ide_name} at: {extension_dir}"
                )
                return extension_dir
                
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Roo extension path for {ide_name}: {e}")
        
        return None

