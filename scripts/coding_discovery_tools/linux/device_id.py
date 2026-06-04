"""Device ID extraction for Linux."""

import logging
import os
import tempfile
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
                logger.warning("No usable state dir for device-id; using ephemeral uuid")
                return str(uuid.uuid4())

            device_id_path = cache.UNBOUND_DIR / _DEVICE_ID_FILENAME

            try:
                if device_id_path.exists() and device_id_path.is_file():
                    existing = device_id_path.read_text(encoding="utf-8").strip()
                    if existing:
                        # Validate the persisted value is a well-formed UUID. A
                        # truncated/partial write (pre-atomic-write), a manual edit,
                        # or another tool clobbering the file can leave a non-UUID
                        # string here; returning it would create a backend device
                        # row that no valid UUID can ever match. Treat corrupt as
                        # absent and regenerate.
                        try:
                            uuid.UUID(existing)
                            return existing
                        except ValueError:
                            logger.warning(
                                f"Corrupt (non-UUID) persisted device-id at "
                                f"{device_id_path!s}; regenerating"
                            )
            except Exception as e:
                logger.warning(f"Could not read persisted device-id at {device_id_path!s}: {e}")

            new_id = str(uuid.uuid4())
            try:
                # Atomic write: a SIGKILL/OOM/power-loss mid-write must never leave
                # a partial UUID on disk. Write to a temp file in the same dir then
                # os.replace() (atomic rename on the same filesystem), mirroring
                # cache.atomic_write_cache().
                cache.UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
                fd, tmp = tempfile.mkstemp(
                    prefix=".device-id.", suffix=".tmp", dir=str(cache.UNBOUND_DIR)
                )
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as f:
                        f.write(new_id)
                    os.replace(tmp, str(device_id_path))
                finally:
                    if os.path.exists(tmp):
                        try:
                            os.unlink(tmp)
                        except OSError:
                            pass
            except Exception as e:
                logger.warning(f"Could not persist device-id at {device_id_path!s}: {e}")
            return new_id
        except Exception as e:
            logger.warning(f"device-id fallback failed: {e}")
            return str(uuid.uuid4())
