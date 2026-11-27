"""
Device ID extraction for macOS
"""

import logging

from ..coding_tool_base import BaseDeviceIdExtractor
from ..constants import COMMAND_TIMEOUT
from ..utils import run_command

logger = logging.getLogger(__name__)


class MacOSDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for macOS systems."""

    def extract_device_id(self) -> str:
        """
        Extract the hardware serial number via ioreg (locale-stable).

        Raises:
            RuntimeError: if the serial cannot be read
        """
        output = run_command(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], COMMAND_TIMEOUT)
        for line in (output or "").split("\n"):
            if "IOPlatformSerialNumber" in line and "=" in line:
                serial = line.split("=", 1)[1].strip().strip('"').strip()
                if serial:
                    return serial
        raise RuntimeError("Could not read the hardware serial number on macOS (IOPlatformSerialNumber)")
