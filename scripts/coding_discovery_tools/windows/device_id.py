"""
Device ID extraction for Windows
"""

import logging

from ..coding_tool_base import BaseDeviceIdExtractor
from ..constants import COMMAND_TIMEOUT
from ..utils import is_valid_serial, run_command

logger = logging.getLogger(__name__)


class WindowsDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for Windows systems."""

    def extract_device_id(self) -> str:
        """
        Extract the BIOS serial number.

        Raises:
            RuntimeError: if the serial cannot be read or is a placeholder value
        """
        output = run_command(
            ["powershell", "-NoProfile", "-Command",
             "(Get-CimInstance -ClassName Win32_BIOS).SerialNumber"],
            COMMAND_TIMEOUT,
        )
        if output and is_valid_serial(output.strip()):
            return output.strip()
        raise RuntimeError("Could not read a valid hardware serial number on Windows (Win32_BIOS)")
