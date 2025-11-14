"""
Device ID extraction for Windows
"""

import logging
from typing import Optional

from ..coding_tool_base import BaseDeviceIdExtractor
from ..constants import COMMAND_TIMEOUT
from ..utils import is_valid_serial, run_command, get_hostname

logger = logging.getLogger(__name__)


class WindowsDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for Windows systems."""

    def extract_device_id(self) -> str:
        """
        Extract Windows device serial number using multiple methods.
        
        Returns:
            Serial number or hostname as fallback
        """
        # Try PowerShell first
        serial = self._get_serial_via_powershell()
        if serial:
            return serial

        # Try wmic with list format
        serial = self._get_serial_via_wmic_list()
        if serial:
            return serial

        # Try standard wmic format
        serial = self._get_serial_via_wmic_standard()
        if serial:
            return serial

        # Fallback to hostname
        return get_hostname()

    def _get_serial_via_powershell(self) -> Optional[str]:
        """Get serial number using PowerShell."""
        ps_command = "(Get-WmiObject Win32_BIOS).SerialNumber"
        output = run_command(["powershell", "-Command", ps_command], COMMAND_TIMEOUT)
        if output and is_valid_serial(output):
            return output
        return None

    def _get_serial_via_wmic_list(self) -> Optional[str]:
        """Get serial number using wmic with list format."""
        output = run_command(
            ["wmic", "bios", "get", "serialnumber", "/format:list"],
            COMMAND_TIMEOUT
        )
        if output:
            for line in output.split('\n'):
                if line.startswith('SerialNumber='):
                    serial = line.split('=', 1)[1].strip()
                    if is_valid_serial(serial):
                        return serial
        return None

    def _get_serial_via_wmic_standard(self) -> Optional[str]:
        """Get serial number using standard wmic format."""
        output = run_command(["wmic", "bios", "get", "serialnumber"], COMMAND_TIMEOUT)
        if output:
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            for line in lines[1:]:  # Skip header
                if is_valid_serial(line):
                    return line
        return None

