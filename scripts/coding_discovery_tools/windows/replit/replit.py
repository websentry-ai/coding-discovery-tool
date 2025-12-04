"""
Replit detection for Windows.

Replit is an online IDE and coding platform.
This module detects Replit installations by checking for:
User data directory in %APPDATA%\Replit\ (AppData\Roaming\Replit)
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


class WindowsReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on Windows systems.
    
    Detection involves:
    - Checking if user data directory exists in AppData\Roaming\Replit
    """

    # User data directory name
    USER_DATA_DIR_NAME = "Replit"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        user_data_path = self._check_user_data_directory()
        if user_data_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(user_data_path)
            }
        
        return None

    def get_version(self) -> Optional[str]:
        """
        Extract Replit version.
        
        Note: Version extraction is not implemented as Replit doesn't expose
        version information in a standard way.
        
        Returns:
            None (version extraction not available)
        """
        return None

    def _check_user_data_directory(self) -> Optional[Path]:
        """
        Check if Replit user data directory exists.
        
        When running as administrator, scans all user directories.
        Otherwise, checks current user's directory.
        
        Returns:
            Path to user data directory if found, None otherwise
        """
        # When running as administrator, scan all user directories
        if self._is_running_as_admin():
            users_dir = Path("C:\\Users")
            if users_dir.exists():
                for user_dir in users_dir.iterdir():
                    if user_dir.is_dir() and not user_dir.name.startswith('.'):
                        try:
                            user_data_path = self._get_user_data_path(user_dir)
                            if user_data_path:
                                return user_data_path
                        except (PermissionError, OSError) as e:
                            logger.debug(f"Skipping user directory {user_dir}: {e}")
                            continue
        
        # Check current user's directory
        user_data_path = self._get_user_data_path(Path.home())
        if user_data_path:
            return user_data_path
        
        return None

    def _get_user_data_path(self, user_home: Path) -> Optional[Path]:
        """
        Get Replit user data directory path for a specific user.
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Path to user data directory if it exists, None otherwise
        """
        user_data_path = user_home / "AppData" / "Roaming" / self.USER_DATA_DIR_NAME
        
        try:
            if user_data_path.exists() and user_data_path.is_dir():
                logger.debug(f"Found Replit user data at: {user_data_path}")
                return user_data_path
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Replit user data path {user_data_path}: {e}")
        
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
