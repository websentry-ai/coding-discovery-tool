"""
Cline detection for Windows.

Cline is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Cline installations by checking for:
1. IDE installations (VS Code, Cursor, Windsurf)
2. Cline extension settings in IDE global storage directories
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


class WindowsClineDetector(BaseToolDetector):
    """
    Detector for Cline installations on Windows systems.
    
    Cline operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf)
    - Verifying Cline extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Cline extension
    SUPPORTED_IDES = ['Code', 'Cursor', 'Windsurf']
    
    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cline"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cline installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as administrator, scan all user directories first
        if self._is_running_as_admin():
            user_cline_info = self._scan_user_directories()
            if user_cline_info:
                return user_cline_info
        
        # Check current user (works for both admin and regular users)
        return self._check_user_for_cline(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Cline version.
        
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
        Scan all user directories for Cline installations when running as admin.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        users_dir = Path("C:\\Users")
        if not users_dir.exists():
            return None
        
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    result = self._check_user_for_cline(user_dir)
                    if result:
                        return result
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")
                    continue
        
        return None

    def _check_user_for_cline(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Cline is installed for a specific user.
        
        Since Cline is an extension, we first check if the extension exists
        in any IDE. Only if the extension is found, we proceed with detection.
        
        This method:
        1. First checks for Cline extension in any supported IDE
        2. If extension found, returns detection result (extension can only exist if IDE is installed)
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        # First, check if Cline extension exists in any IDE
        extension_path = None
        
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_cline_extension(user_home, ide_name)
            if extension_path:
                logger.debug(f"Found Cline extension in {ide_name} at: {extension_path}")
                break
        
        # If no extension found, return None immediately
        if not extension_path:
            logger.debug("Cline extension not found in any IDE")
            return None
        
        # If extension found, IDE is installed (extension can only exist if IDE is installed)
        # Use the extension path as install_path
        return {
            "name": self.tool_name,
            "version": "Unknown",
            "install_path": str(extension_path)
        }

    def _check_cline_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Cline extension directory exists for a specific IDE.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension directory if found, None otherwise
        """
        # Windows VS Code/Cursor/Windsurf global storage path
        extension_dir = (
            user_home / "AppData" / "Roaming" / ide_name / "User" / "globalStorage" / self.CLINE_EXTENSION_ID
        )
        
        try:
            # Check if extension directory exists
            if extension_dir.exists() and extension_dir.is_dir():
                logger.debug(
                    f"Found Cline extension directory for {ide_name} at: {extension_dir}"
                )
                return extension_dir
                
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Cline extension path for {ide_name}: {e}")
        
        return None

