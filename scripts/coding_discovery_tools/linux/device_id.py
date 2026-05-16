"""Device ID extraction for Linux."""

import logging
from pathlib import Path

from ..coding_tool_base import BaseDeviceIdExtractor
from ..utils import get_hostname

logger = logging.getLogger(__name__)

_MACHINE_ID_PATHS = [
    Path("/etc/machine-id"),   # systemd (most modern distros)
    Path("/var/lib/dbus/machine-id"),  # older dbus fallback
]


class LinuxDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for Linux systems."""

    def extract_device_id(self) -> str:
        """
        Return the machine-id from /etc/machine-id (systemd standard).
        Falls back to hostname if the file is absent or unreadable.
        """
        for path in _MACHINE_ID_PATHS:
            try:
                if path.exists() and path.is_file():
                    machine_id = path.read_text(encoding="utf-8").strip()
                    if machine_id:
                        return machine_id
            except Exception as e:
                logger.debug(f"Could not read {path}: {e}")

        return get_hostname()
