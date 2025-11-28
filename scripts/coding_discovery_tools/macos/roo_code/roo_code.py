"""
Roo Code detection for macOS.

Roo Code is an AI-powered coding assistant that operates as a VS Code extension.
This module detects Roo Code installations by checking for:
1. IDE installations (VS Code, Cursor, Windsurf)
2. Roo extension settings in IDE global storage directories
"""

import logging
from pathlib import Path
from typing import Optional, Dict, Tuple

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import is_running_as_root, scan_user_directories

logger = logging.getLogger(__name__)


class MacOSRooDetector(BaseToolDetector):
    """
    Detector for Roo Code installations on macOS systems.
    
    Roo Code operates as a VS Code extension, so detection involves:
    - Checking for compatible IDE installations (VS Code, Cursor, Windsurf)
    - Verifying Roo extension settings exist in IDE global storage
    """

    # Supported IDEs that can host the Roo Code extension
    SUPPORTED_IDES = ['Code', 'Cursor', 'Windsurf']
    
    # Roo Code extension identifier
    ROO_EXTENSION_ID = "rooveterinaryinc.roo-cline"
    
    # Application names for each IDE (Code has multiple possible names)
    IDE_APP_NAMES = {
        "Code": ["Code.app"],
        "Cursor": ["Cursor.app"],
        "Windsurf": ["Windsurf.app"],
    }
    
    # Standard macOS applications directory
    APPLICATIONS_DIR = Path("/Applications")

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Roo Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Roo Code installation on macOS.
        
        When running as root, scans all user directories to find installations
        across multiple user accounts.
        
        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        # When running as root, scan all user directories first
        if is_running_as_root():
            user_roo_info = scan_user_directories(
                lambda user_dir: self._check_user_for_roo(user_dir)
            )
            if user_roo_info:
                return user_roo_info
        
        # Check current user (works for both root and regular users)
        return self._check_user_for_roo(Path.home())

    def get_version(self) -> Optional[str]:
        """
        Extract Roo Code version.
        
        Note: Version extraction is currently not implemented.
        
        Returns:
            None (version extraction removed per requirements)
        """
        return None

    def _check_user_for_roo(self, user_home: Path) -> Optional[Dict]:
        """
        Check if Roo Code is installed for a specific user.
        
        Since Roo Code is an extension, we first check if the extension exists
        in any IDE. Only if the extension is found, we proceed with detection.
        
        This method:
        1. First checks for Roo extension in any supported IDE
        2. If extension found, verifies the IDE is installed
        3. Only returns detection result if extension is present
        
        Args:
            user_home: User's home directory path
            
        Returns:
            Dict with tool info (name, version, install_path) or None if not found
        """
        # First, check if Roo extension exists in any IDE
        extension_path = None
        ide_with_extension = None
        
        for ide_name in self.SUPPORTED_IDES:
            extension_path = self._check_roo_extension(user_home, ide_name)
            if extension_path:
                ide_with_extension = ide_name
                logger.debug(f"Found Roo Code extension in {ide_name} at: {extension_path}")
                break
        
        # If no extension found, return None immediately
        if not extension_path:
            logger.debug("Roo Code extension not found in any IDE")
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
            logger.debug("Roo Code extension found but no IDE installation detected")
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
        code_base = user_home / "Library" / "Application Support"
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

