"""
Device ID extraction for Linux
"""

import logging
from pathlib import Path
from typing import Optional
from ..coding_tool_base import BaseDeviceIdExtractor
from ..constants import COMMAND_TIMEOUT
from ..utils import run_command

logger = logging.getLogger(__name__)


class LinuxDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for Linux systems."""

    def extract_device_id(self) -> str:
        """
        Extract Linux device serial number.

        Returns:
            Serial number or empty string if not found
        """
        # Try to get DMI serial number
        serial = self._get_dmi_serial()
        if serial:
            return serial

        # Return empty string if serial number cannot be found
        return ""

    def _get_dmi_serial(self) -> Optional[str]:
        """Get serial number from DMI (may require sudo)."""
        try:
            # Try dmidecode first (more common)
            output = run_command(
                ["sudo", "-n", "dmidecode", "-s", "system-serial-number"],
                COMMAND_TIMEOUT
            )
            if output and output.strip() and output.strip() != "Not Specified":
                return output.strip()
        except Exception:
            pass

        # Try reading directly from sysfs (doesn't require sudo on some systems)
        try:
            serial_path = Path("/sys/class/dmi/id/product_serial")
            if serial_path.exists():
                with open(serial_path, 'r') as f:
                    serial = f.read().strip()
                    if serial and serial != "Not Specified":
                        return serial
        except Exception:
            pass

        return None