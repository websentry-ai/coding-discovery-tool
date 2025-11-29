"""
Cline detection for macOS.

Cline is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Cline installations by checking for:
1. IDE installations (VS Code, Cursor, Windsurf)
2. Cline extension settings in IDE global storage directories
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


class MacOSClineDetector(BaseToolDetector):
    """
    Detector for Cline installations on macOS systems.
    
    Cline operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf)
    - Verifying Cline extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Cline extension
    SUPPORTED_IDES = ['Code', 'Cursor', 'Windsurf']
    
    # Cline extension identifier
    CLINE_EXTENSION_ID = "saoudrizwan.claude-dev"
    
    # Application names for each IDE
    IDE_APP_NAMES = {
        "Code": ["Visual Studio Code.app"],
        "Cursor": ["Cursor.app"],
        "Windsurf": ["Windsurf.app"],
    }
    
    # Standard macOS applications directory
    APPLICATIONS_DIR = Path("/Applications")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cline"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cline installation on macOS.
        
        When running as root, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as root, scan all user directories first
        if is_running_as_root():
            user_cline_info = scan_user_directories(
                lambda user_dir: self._check_user_for_cline(user_dir)
            )
            if user_cline_info:
                return user_cline_info
        
        # Check current user (works for both root and regular users)
        return self._check_user_for_cline(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Cline version.
        
        Note: Version extraction is currently not implemented.
        
        Returns:
            None (version extraction removed per requirements)
        """
        return None

    def _check_user_for_cline(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Cline is installed for a specific user.
        
        Since Cline is an extension, we first check if the extension exists
        in any IDE. Only if the extension is found, we proceed with detection.
        
        This method:
        1. First checks for Cline extension in any supported IDE
        2. If extension found, verifies the IDE is installed
        3. Only returns detection result if extension is present
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        # First, check if Cline extension exists in any IDE
        extension_path = None
        ide_with_extension = None
        
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_cline_extension(user_home, ide_name)
            if extension_path:
                ide_with_extension = ide_name
                logger.debug(f"Found Cline extension in {ide_name} at: {extension_path}")
                break
        
        # If no extension found, return None immediately
        if not extension_path:
            logger.debug("Cline extension not found in any IDE")
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
            logger.debug("Cline extension found but no IDE installation detected")
            return None
        
        # Use the extension path as install_path
        return {
            "name": self.tool_name,
            "version": "Unknown",
            "install_path": str(extension_path)
        }

    def _check_ide_installation(self, ide_name: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a specific IDE is installed in /Applications.
        
        First checks if the IDE installation path exists before proceeding.
        
        Args:
            ide_name: Name of the IDE to check (Code, Cursor, or Windsurf)
            
        Returns:
            Tuple of (is_installed: bool, install_path: Optional[str])
        """
        app_names = self.IDE_APP_NAMES.get(ide_name, [])
        
        for app_name in app_names:
            ide_path = self.APPLICATIONS_DIR / app_name
            
            # First check if the path exists
            try:
                if not ide_path.exists():
                    logger.debug(f"IDE path does not exist: {ide_path}")
                    continue
                
                # Verify it's a directory
                if ide_path.is_dir():
                    logger.debug(f"Found {ide_name} installation at: {ide_path}")
                    return True, str(ide_path)
            except (PermissionError, OSError) as e:
                logger.debug(f"Could not check IDE path {ide_path}: {e}")
                continue
        
        return False, None

    def _check_cline_extension(self, user_home: Path, ide_name: str) -> Optional[Path]:
        """
        Check if Cline extension settings exist for a specific IDE.
        
        Checks both the settings file and the extension directory to handle
        cases where settings file might not exist yet but extension is installed.
        
        Args:
            user_home: User's home directory path
            ide_name: Name of the IDE to check
            
        Returns:
            Path to extension settings or extension directory if found, None otherwise
        """
        code_base = user_home / "Library" / "Application Support"
        extension_dir = (
            code_base / ide_name / "User" / "globalStorage" / self.CLINE_EXTENSION_ID
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

