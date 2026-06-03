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
import stat
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

last_lock_error: Optional[str] = None


def _state_dir_candidates():
    """Ordered (path, is_private_temp) candidates for the state dir.
    Home first (preserves existing behavior); deterministic uid-namespaced
    temp dir as fallback. Split out as a function so tests can inject candidates."""
    candidates = [(UNBOUND_DIR, False)]
    uid = os.getuid() if hasattr(os, "getuid") else "u"
    candidates.append((Path(tempfile.gettempdir()) / f"unbound-{uid}", True))
    return candidates


def _is_unsafe_existing(path):
    """True if `path` already exists as a symlink, a non-dir, or a dir we don't
    own — i.e. a path we must NOT trust for a fixed-name dir in a shared temp."""
    try:
        st = os.lstat(str(path))
    except OSError:
        return False  # doesn't exist yet — safe to create
    if stat.S_ISLNK(st.st_mode):
        return True
    if not stat.S_ISDIR(st.st_mode):
        return True
    if hasattr(os, "getuid") and st.st_uid != os.getuid():
        return True
    return False


def _try_state_dir(path, is_private):
    """Make `path` usable. Returns True if it is a writable dir we can use.
    mkdir-only probe (no file-write probe — see module note). On the private
    temp candidate, refuse hostile pre-existing entries and lock perms to 0700."""
    global last_lock_error
    try:
        if is_private and _is_unsafe_existing(path):
            last_lock_error = f"unsafe pre-existing state dir: {path}"
            return False
        path.mkdir(parents=True, exist_ok=True)
        # mkdir with exist_ok=True is a no-op success on a pre-existing dir, so it
        # does not prove we can create entries inside it. Probe writability so an
        # existing-but-unwritable dir falls through to the next candidate instead
        # of failing later at lock creation (which would skip the fallback).
        if not os.access(str(path), os.W_OK | os.X_OK):
            last_lock_error = f"state dir not writable: {path}"
            return False
        if is_private:
            try:
                os.chmod(str(path), 0o700)
            except OSError:
                pass
            # Re-check after creation in case of a race that swapped it, and
            # confirm chmod actually took (it is best-effort above) so we never
            # trust a private dir left group/other-accessible.
            if _is_unsafe_existing(path):
                last_lock_error = f"unsafe state dir after create: {path}"
                return False
            if hasattr(os, "getuid") and stat.S_IMODE(os.lstat(str(path)).st_mode) != 0o700:
                last_lock_error = f"state dir perms not 0700: {path}"
                return False
        return True
    except OSError as e:
        last_lock_error = str(e)
        return False


def _ensure_state_dir():
    """Resolve UNBOUND_DIR/CACHE_PATH/LOCK_PATH to the first usable candidate,
    reassigning the module globals when falling back. Returns True if a usable
    dir was found, False otherwise (caller returns 'setup_failed')."""
    global UNBOUND_DIR, CACHE_PATH, LOCK_PATH
    for path, is_private in _state_dir_candidates():
        if _try_state_dir(path, is_private):
            if path != UNBOUND_DIR:
                logger.warning(f"home state dir unusable; using fallback state dir {path}")
                UNBOUND_DIR = path
                CACHE_PATH = path / "discovery-cache.json"
                LOCK_PATH = path / "discovery.lock"
            return True
    return False


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
        # Refuse to write the cache (which can contain MCP configs / tool
        # inventory / paths) through a symlinked state dir — defends the temp
        # fallback against a post-resolution dir swap in a shared /tmp.
        if UNBOUND_DIR.is_symlink():
            logger.warning(f"discovery-cache write skipped: state dir is a symlink: {UNBOUND_DIR}")
            return
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


def acquire_lock() -> str:
    """Best-effort exclusive lock. Returns "acquired", "contended" (held by a live process), or "setup_failed"."""
    global last_lock_error
    last_lock_error = None
    if not _ensure_state_dir():
        return "setup_failed"
    try:
        UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        last_lock_error = str(e)
        logger.warning(f"could not create {UNBOUND_DIR}: {e}")
        return "setup_failed"

    if LOCK_PATH.exists() and _lock_is_live():
        return "contended"

    if LOCK_PATH.exists():
        try:
            LOCK_PATH.unlink()
        except OSError as e:
            last_lock_error = str(e)
            logger.warning(f"could not steal stale lock: {e}")
            return "setup_failed"

    _lock_flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        _lock_flags |= os.O_NOFOLLOW  # never write the lock through a swapped symlink
    try:
        fd = os.open(str(LOCK_PATH), _lock_flags, 0o600)
    except FileExistsError:
        return "contended"
    except OSError as e:
        last_lock_error = str(e)
        logger.warning(f"could not create lock: {e}")
        return "setup_failed"

    try:
        try:
            os.write(fd, f"{os.getpid()} {_now_iso()}\n".encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        last_lock_error = str(e)
        logger.warning(f"could not write lock: {e}")
        # Remove the lock file we just created so a write failure can't leave a
        # fresh ghost lock that makes the next run see false contention.
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return "setup_failed"
    return "acquired"


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
