"""Device ID extraction for Linux."""

import logging
import uuid
from pathlib import Path

from .. import cache
from ..coding_tool_base import BaseDeviceIdExtractor

logger = logging.getLogger(__name__)

_MACHINE_ID_PATHS = [
    Path("/etc/machine-id"),   # systemd (most modern distros)
    Path("/var/lib/dbus/machine-id"),  # older dbus fallback
]

_DEVICE_ID_FILENAME = "device-id"


class LinuxDeviceIdExtractor(BaseDeviceIdExtractor):
    """Device ID extractor for Linux systems."""

    def extract_device_id(self) -> str:
        """
        Return the machine-id from /etc/machine-id (systemd standard).

        When no machine-id is available (common in containers, which often have
        an empty/absent /etc/machine-id), fall back to a UUID persisted in the
        home-user's ``~/.unbound/`` directory. A restarted container that mounts
        a persistent ``~/.unbound`` (the primary ``unbound login`` flow) then
        keeps a single stable device row instead of exploding into one row per
        launch — which an ephemeral hostname fallback would produce.
        """
        for path in _MACHINE_ID_PATHS:
            try:
                if path.exists() and path.is_file():
                    machine_id = path.read_text(encoding="utf-8").strip()
                    if machine_id:
                        return machine_id
            except Exception as e:
                logger.debug(f"Could not read {path}: {e}")

        return self._persisted_device_id()

    @staticmethod
    def _persisted_device_id() -> str:
        """Read (or generate-and-persist) a stable UUID under ``~/.unbound/``.

        Reuses the repo's canonical state-dir resolver (``cache._ensure_state_dir``)
        rather than a bare ``Path.home()`` so we honour the writable-fallback chain
        and land next to the API key written by ``unbound login``. If the state dir
        cannot be resolved or the write fails, return an unpersisted uuid4 — no
        worse than the previous ephemeral hostname behaviour, and never raises.
        """
        try:
            if not cache._ensure_state_dir():
                logger.debug("No usable state dir for device-id; using ephemeral uuid")
                return str(uuid.uuid4())

            device_id_path = cache.UNBOUND_DIR / _DEVICE_ID_FILENAME

            try:
                if device_id_path.exists() and device_id_path.is_file():
                    existing = device_id_path.read_text(encoding="utf-8").strip()
                    if existing:
                        return existing
            except Exception as e:
                logger.debug(f"Could not read persisted device-id at {device_id_path}: {e}")

            new_id = str(uuid.uuid4())
            try:
                cache.UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
                device_id_path.write_text(new_id, encoding="utf-8")
            except Exception as e:
                logger.debug(f"Could not persist device-id at {device_id_path}: {e}")
            return new_id
        except Exception as e:
            logger.debug(f"device-id fallback failed: {e}")
            return str(uuid.uuid4())
