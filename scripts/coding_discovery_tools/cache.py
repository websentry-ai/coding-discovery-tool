"""
Local cache + lock + heartbeat for SessionStart-triggered discovery.

Cache file (``~/.unbound/discovery-cache.json``) holds:
  - ``last_run_at``: global timestamp gating the debounce window
  - ``tools[name]``: per-tool ``payload_hash`` + ``last_uploaded_at``

Lock file (``~/.unbound/discovery.lock``) is held by the running discovery
process. A heartbeat thread bumps its mtime every 60s; hooks treat a lock
whose mtime is older than ``STALE_LOCK_SECONDS`` as a zombie and steal it.
"""
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

UNBOUND_DIR = Path.home() / ".unbound"
CACHE_PATH = UNBOUND_DIR / "discovery-cache.json"
LOCK_PATH = UNBOUND_DIR / "discovery.lock"

STALE_LOCK_SECONDS = 15 * 60
HEARTBEAT_INTERVAL_SECONDS = 60


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_cache() -> dict:
    try:
        if not CACHE_PATH.exists():
            return {}
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"discovery-cache read failed, treating as empty: {e}")
        return {}


def atomic_write_cache(data: dict) -> None:
    try:
        UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".discovery-cache.", suffix=".tmp", dir=str(UNBOUND_DIR))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp, CACHE_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except OSError as e:
        logger.warning(f"discovery-cache write failed: {e}")


def update_tool(tool_name: str, home_user: str, payload_hash: str) -> None:
    cache = read_cache()
    tools = cache.setdefault("tools", {})
    if not isinstance(tools, dict):
        tools = {}
        cache["tools"] = tools
    by_user = tools.setdefault(tool_name, {})
    if not isinstance(by_user, dict):
        by_user = {}
        tools[tool_name] = by_user
    by_user[home_user] = {
        "payload_hash": payload_hash,
        "last_uploaded_at": _now_iso(),
    }
    atomic_write_cache(cache)


def get_cached_hash(tool_name: str, home_user: str, cache: Optional[dict] = None) -> Optional[str]:
    cache = cache if cache is not None else read_cache()
    tools = cache.get("tools") or {}
    if not isinstance(tools, dict):
        return None
    by_user = tools.get(tool_name) or {}
    if not isinstance(by_user, dict):
        return None
    entry = by_user.get(home_user)
    if isinstance(entry, dict):
        h = entry.get("payload_hash")
        return h if isinstance(h, str) else None
    return None


def _lock_is_live() -> bool:
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        return False
    return age < STALE_LOCK_SECONDS


def acquire_lock() -> bool:
    """Best-effort exclusive lock. Returns True on success, False if held by a live process."""
    try:
        UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logger.warning(f"could not create {UNBOUND_DIR}: {e}")
        return False

    if LOCK_PATH.exists() and _lock_is_live():
        return False

    if LOCK_PATH.exists():
        try:
            LOCK_PATH.unlink()
        except OSError as e:
            logger.warning(f"could not steal stale lock: {e}")
            return False

    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False
    except OSError as e:
        logger.warning(f"could not create lock: {e}")
        return False

    try:
        os.write(fd, f"{os.getpid()} {_now_iso()}\n".encode("utf-8"))
    finally:
        os.close(fd)
    return True


def release_lock() -> None:
    try:
        LOCK_PATH.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"could not release lock: {e}")


def heartbeat_start() -> threading.Event:
    """Start a daemon thread that bumps the lock file mtime every minute.
    Returns the stop Event; call ``.set()`` from a finally block."""
    stop = threading.Event()

    def _tick():
        while not stop.wait(HEARTBEAT_INTERVAL_SECONDS):
            try:
                os.utime(LOCK_PATH, None)
            except OSError:
                return

    threading.Thread(target=_tick, daemon=True, name="discovery-heartbeat").start()
    return stop
