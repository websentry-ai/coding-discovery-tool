"""
Kilo Code detection for Windows.

Kilo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Kilo Code installations by checking for:
1. IDE installations (VS Code, Cursor)
2. Kilo Code extension settings in IDE global storage directories
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


class WindowsKiloCodeDetector(BaseToolDetector):
    """
    Detector for Kilo Code installations on Windows systems.
    
    Kilo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor)
    - Verifying Kilo Code extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Kilo Code extension
    SUPPORTED_IDES = ['Code', 'Cursor']
    
    # Kilo Code extension identifier
    KILOCODE_EXTENSION_ID = "kilocode.Kilo-Code"
    
    # Application names for each IDE
    IDE_APP_NAMES = {
        "Code": ["Code.exe"],
        "Cursor": ["Cursor.exe"],
    }

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Kilo Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Kilo Code installation on Windows.
        
        When running as administrator, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as administrator, scan all user directories first
        if self._is_running_as_admin():
            user_kilocode_info = self._scan_user_directories()
            if user_kilocode_info:
                return user_kilocode_info
        
        # Check current user (works for both admin and regular users)
        return self._check_user_for_kilocode(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Kilo Code version.
        
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
        Scan all user directories for Kilo Code installations when running as admin.
        
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        users_dir = Path("C:\\Users")
        if not users_dir.exists():
            return None
        
        for user_dir in users_dir.iterdir():
            if user_dir.is_dir() and not user_dir.name.startswith('.'):
                try:
                    result = self._check_user_for_kilocode(user_dir)
                    if result:
                        return result
                except (PermissionError, OSError) as e:
                    logger.debug(f"Skipping user directory {user_dir}: {e}")
                    continue
        
        return None

    def _check_user_for_kilocode(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Kilo Code is installed for a specific user.
        
        Since Kilo Code is an extension, we first check if the extension exists
        in any IDE. Only if the extension is found, we proceed with detection.
        
        This method:
        1. First checks for Kilo Code extension in any supported IDE
        2. If extension found, verifies the IDE is installed
        3. Only returns detection result if extension is present
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        # First, check if Kilo Code extension exists in any IDE
        extension_path = None
        ide_with_extension = None
        
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_kilocode_extension(user_home, ide_name)
            if extension_path:
                ide_with_extension = ide_name
                logger.debug(f"Found Kilo Code extension in {ide_name} at: {extension_path}")
                break
        
        # If no extension found, return None immediately
        if not extension_path:
            logger.debug("Kilo Code extension not found in any IDE")
            return None
        
        # Extension found - verify IDE is installed (for validation)
        ide_installed = False
        if ide_with_extension:
            ide_installed, _ = self._check_ide_installation(ide_with_extension)
        
        # If IDE not found, check other IDEs
        if not ide_installed:
            for ide_name in self.SUPPORTED_IDES:
                ide_installed, _ = self._check_ide_installation(ide_name)
                if ide_installed:
                    break
        
        # Return None if IDE is not installed (extension exists but IDE missing)
        if not ide_installed:
            logger.debug("Kilo Code extension found but no IDE installation detected")
            return None
        
        # Use the extension path as install_path
        return {
            "name": self.tool_name,
            "version": "Unknown",
            "install_path": str(extension_path)
        }

    def _check_ide_installation(self, ide_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a specific IDE is installed on Windows.
        
        First checks if the IDE installation path exists before proceeding.
        
        Args:
            ide_name: Name of the IDE to check (Code or Cursor)
            
        Returns:
            Tuple of (is_installed: bool, install_path: Optional[str])
        """
        user_home = Path.home()
        search_paths = [
            user_home / "AppData" / "Local" / "Programs" / ide_name,
            Path("C:\\Program Files") / ide_name,
            Path("C:\\Program Files (x86)") / ide_name,
        ]
        
        # Add Cursor-specific paths
        if ide_name == "Cursor":
            search_paths.extend([
                user_home / "AppData" / "Local" / "Programs" / "cursor",
                Path("C:\\Program Files") / "cursor",
                Path("C:\\Program Files (x86)") / "cursor",
            ])
        
        app_names = self.IDE_APP_NAMES.get(ide_name, [])
        
        for search_path in search_paths:
            for app_name in app_names:
                ide_exe = search_path / app_name
                
                try:
                    if ide_exe.exists() and ide_exe.is_file():
                        logger.debug(f"Found {ide_name} installation at: {search_path}")
                        return True, str(search_path)
                except (PermissionError, OSError) as e:
                    logger.debug(f"Could not check IDE path {ide_exe}: {e}")
                    continue
        
        return False, None

    def _check_kilocode_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Kilo Code extension directory exists for a specific IDE.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension directory if found, None otherwise
        """
        # Windows VS Code/Cursor global storage path
        extension_dir = (
            user_home / "AppData" / "Roaming" / ide_name / "User" / "globalStorage" / self.KILOCODE_EXTENSION_ID
        )
        
        try:
            # Check if extension directory exists
            if extension_dir.exists() and extension_dir.is_dir():
                logger.debug(
                    f"Found Kilo Code extension directory for {ide_name} at: {extension_dir}"
                )
                return extension_dir
                
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not check Kilo Code extension path for {ide_name}: {e}")
        
        return None

