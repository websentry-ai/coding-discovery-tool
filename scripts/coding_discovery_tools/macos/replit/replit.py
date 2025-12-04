"""
Replit detection for macOS.

Replit is an online IDE and coding platform.
This module detects Replit installations by checking for:
1. Application installation in /Applications/Replit.app
2. User data directory in ~/Library/Application Support/Replit/
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


class MacOSReplitDetector(BaseToolDetector):
    """
    Detector for Replit installations on macOS systems.
    
    Detection involves:
    - Checking if Replit.app exists in /Applications
    - Checking if user data directory exists in ~/Library/Application Support/Replit/
    """

    # Application installation path
    APPLICATION_PATH = Path("/Applications/Replit.app")
    
    # User data directory name
    USER_DATA_DIR_NAME = "Replit"

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Replit"

    def detect(self) -> Optional[Dict]:
        """
        Detect Replit installation on macOS.
        
        When running as root, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # Check application installation first
        app_path = self._check_application_installation()
        
        # Check user data directory
        user_data_path = self._check_user_data_directory()
        
        # Return detection result if either app or user data is found
        if app_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(),
                "install_path": str(app_path)
            }
        
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

    def _check_application_installation(self) -> Optional[Path]:
        """
        Check if Replit application is installed in /Applications.
        
        Returns:
            Path to Replit.app if found, None otherwise
        """
        try:
            if self.APPLICATION_PATH.exists() and self.APPLICATION_PATH.is_dir():
                logger.debug(f"Found Replit application at: {self.APPLICATION_PATH}")
                return self.APPLICATION_PATH
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Replit application path: {e}")
        
        return None

    def _check_user_data_directory(self) -> Optional[Path]:
        """
        Check if Replit user data directory exists.
        
        When running as root, scans all user directories.
        Otherwise, checks current user's directory.
        
        Returns:
            Path to user data directory if found, None otherwise
        """
        # When running as root, scan all user directories
        if is_running_as_root():
            user_data_path = scan_user_directories(
                lambda user_dir: self._get_user_data_path(user_dir)
            )
            if user_data_path:
                return user_data_path
        
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
        user_data_path = (
            user_home / "Library" / "Application Support" / self.USER_DATA_DIR_NAME
        )
        
        try:
            if user_data_path.exists() and user_data_path.is_dir():
                logger.debug(f"Found Replit user data at: {user_data_path}")
                return user_data_path
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Replit user data path {user_data_path}: {e}")
        
        return None

