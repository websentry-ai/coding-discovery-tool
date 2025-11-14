"""
Device ID extraction for macOS
"""

import logging
from ..coding_tool_base import BaseDeviceIdExtractor
from ..constants import COMMAND_TIMEOUT
from ..utils import run_command, get_hostname

logger = logging.getLogger(__name__)


class MacOSDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for macOS systems."""

    def extract_device_id(self) -> str:
        """
        Extract macOS device serial number.
        
        Returns:
            Serial number or hostname as fallback
        """
        try:
            output = run_command(
                ["system_profiler", "SPHardwareDataType"],
                COMMAND_TIMEOUT
            )
            if output:
                for line in output.split('\n'):
                    if 'Serial Number' in line and ':' in line:
                        parts = line.split(':', 1)
                        if len(parts) == 2:
                            return parts[1].strip()
        except Exception as e:
            logger.warning(f"Could not extract macOS serial number: {e}")
        
        return get_hostname()

