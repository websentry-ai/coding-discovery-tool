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
import uuid
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .utils import report_to_sentry

logger = logging.getLogger(__name__)

# Immutable home anchor. _ensure_state_dir() may reassign the *active* paths
# below to a temp fallback, but the home candidate is always derived from this
# so a second acquire_lock() in the same process re-evaluates home first instead
# of treating an already-resolved temp dir as a trusted non-private candidate.
_CACHE_FILENAME = "discovery-cache.json"
_LOCK_FILENAME = "discovery.lock"
_HOME_STATE_DIR = Path.home() / ".unbound"
UNBOUND_DIR = _HOME_STATE_DIR
CACHE_PATH = UNBOUND_DIR / _CACHE_FILENAME
LOCK_PATH = UNBOUND_DIR / _LOCK_FILENAME

STALE_LOCK_SECONDS = 15 * 60
HEARTBEAT_INTERVAL_SECONDS = 60

# Auto-resume window: a run left "in_progress" (interrupted without a clean
# completion) is resumable only if its checkpoint was touched within this many
# seconds. Kept well below the backend's 10-min stale-scan sweep so a resumed
# run finishes before the interrupted run would be marked failed, and short
# enough that a tool changing between the interruption and the retry is
# implausible — so already-reported tools can be skipped without a freshness
# re-check. Detection still runs every time, so newly-installed tools are never
# missed; only re-PROCESSING of already-reported tools is skipped.
RESUME_WINDOW_SECONDS = 150

# Never open the lock through a swapped symlink; 0 on platforms lacking it.
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

last_lock_error: Optional[str] = None
# Observability: outcome of the most recent acquire_lock() so a run can report
# whether it stole a dead/stale predecessor's lock. One of: "acquired",
# "stolen_dead_pid", "stolen_stale", "contended", "setup_failed", or None.
last_lock_outcome: Optional[str] = None


def _state_dir_candidates() -> list:
    """Ordered (path, is_private_temp) candidates for the state dir.
    Home first (preserves existing behavior); deterministic uid-namespaced
    temp dir as fallback. Split out as a function so tests can inject candidates.

    The fixed name is a deliberate trade-off: it must be deterministic so a
    daemon and a login session of the same uid resolve to the SAME dir (shared
    cross-process single-flight). A hostile pre-existing entry at that fixed name
    is refused below (-> setup_failed, surfaced to Sentry by the caller) rather
    than silently working around it."""
    candidates = [(_HOME_STATE_DIR, False)]
    if hasattr(os, "getuid"):
        # POSIX: /var/tmp is cross-session AND reboot-stable (unlike per-session
        # launchd $TMPDIR via tempfile.gettempdir() on macOS, which would split
        # the lock/cache between a daemon and a login session of the same uid).
        # Matches utils._get_queue_file_path()'s /var/tmp/...-{uid} idiom.
        candidates.append((Path(f"/var/tmp/unbound-{os.getuid()}"), True))
    else:
        # Windows: no uid; gettempdir() is already per-user there.
        candidates.append((Path(tempfile.gettempdir()) / "unbound", True))
    return candidates


def _is_unsafe_existing(path: Path) -> bool:
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


def _parent_is_unsafe(path: Path) -> bool:
    """True if `path`'s parent is world-writable but NOT sticky. Our symlink/
    ownership hardening only holds if the parent (e.g. /var/tmp) is sticky
    (mode 1777) so a non-owner can't remove/rename our fixed-name entry."""
    if not hasattr(os, "getuid"):
        # Windows: st_mode reports 0o777 with no sticky bit for normal dirs, so
        # this POSIX world-writable/sticky check is meaningless (and would reject
        # every candidate). gettempdir() is already per-user there.
        return False
    try:
        pst = os.lstat(str(path.parent))
    except OSError:
        return False  # parent missing; mkdir(parents=True) will handle/fail
    # world-writable but NOT sticky = anyone can swap our fixed-name entry
    if (pst.st_mode & stat.S_IWOTH) and not (pst.st_mode & stat.S_ISVTX):
        return True
    return False


def _try_state_dir(path: Path, is_private: bool) -> bool:
    """Make `path` usable. Returns True if it is a writable dir we can use.
    mkdir-only probe (no file-write probe — see module note). On the private
    temp candidate, refuse hostile pre-existing entries and lock perms to 0700."""
    global last_lock_error
    try:
        if is_private and _parent_is_unsafe(path):
            last_lock_error = f"unsafe (non-sticky world-writable) parent for {path}"
            return False
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
            # chmod above is best-effort; a pre-existing 0755 dir or a failed
            # chmod must NOT be trusted — any group/other bit leaks discovery
            # state (tool inventory, home_user) to other local users. POSIX only:
            # on Windows st_mode is synthetic (0o777 for dirs, no real perm bits)
            # so this check is meaningless there, and %TEMP% is per-user anyway.
            if hasattr(os, "getuid"):
                st = os.lstat(str(path))
                if st.st_mode & 0o077:
                    last_lock_error = f"state dir not private (mode {oct(stat.S_IMODE(st.st_mode))}): {path}"
                    return False
        # Reject a candidate whose existing cache file this uid can't read (foreign-owned 0600 in a shared HOME).
        cache_file = path / _CACHE_FILENAME
        if cache_file.exists() and not os.access(str(cache_file), os.R_OK):
            last_lock_error = f"state dir holds unreadable cache file (foreign-owned?): {cache_file}"
            return False
        return True
    except OSError as e:
        last_lock_error = str(e)
        return False


def _ensure_state_dir() -> bool:
    """Resolve UNBOUND_DIR/CACHE_PATH/LOCK_PATH to the first usable candidate,
    reassigning the module globals when falling back. Returns True if a usable
    dir was found, False otherwise (caller returns 'setup_failed')."""
    global UNBOUND_DIR, CACHE_PATH, LOCK_PATH, last_lock_error
    for path, is_private in _state_dir_candidates():
        if _try_state_dir(path, is_private):
            if path != UNBOUND_DIR:
                logger.warning(
                    f"home state dir unusable ({last_lock_error or 'unknown'}); "
                    f"using fallback state dir {path}"
                )
                UNBOUND_DIR = path
                CACHE_PATH = path / _CACHE_FILENAME
                LOCK_PATH = path / _LOCK_FILENAME
            # An earlier candidate (e.g. an unwritable home) may have set
            # last_lock_error; clear it now that we have a usable dir so a
            # successful (possibly fallen-back) acquire never reports a stale
            # error to any future reader.
            last_lock_error = None
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
        report_to_sentry(e, {"phase": "cache"}, level="warning")
        return {}


def atomic_write_cache(data: dict) -> None:
    try:
        # Refuse to write the cache (which can contain MCP configs / tool
        # inventory / paths) through a symlinked state dir — but ONLY for the
        # shared-temp fallback, where a post-resolution dir swap is the threat.
        # The home dir (~/.unbound) is trusted by design and MAY legitimately be
        # a user-created symlink; guarding it there would silently skip every
        # cache write and force a cold re-upload each run. Check BEFORE mkdir,
        # since mkdir(parents=True) would follow a symlink and create its target.
        if UNBOUND_DIR != _HOME_STATE_DIR and UNBOUND_DIR.is_symlink():
            logger.warning(f"discovery-cache write skipped: fallback state dir is a symlink: {UNBOUND_DIR}")
            return
        UNBOUND_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".discovery-cache.", suffix=".tmp", dir=str(UNBOUND_DIR))
        try:
            # Owner-only: the cache holds the resume checkpoint + tool inventory.
            # mkstemp is already 0600 and os.replace preserves it, but chmod
            # explicitly so a cross-user process can never read/forge it (the
            # same-user boundary is out of scope — see resumable_done).
            try:
                os.fchmod(fd, 0o600)
            except (OSError, AttributeError):
                pass
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
        report_to_sentry(e, {"phase": "cache"}, level="warning")


def _set_tool_hash(cache: dict, tool_name: str, home_user: str, payload_hash: str) -> None:
    """Mutate `cache` in place to record the per-tool payload hash (no write)."""
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


def update_tool(tool_name: str, home_user: str, payload_hash: str) -> None:
    cache = read_cache()
    _set_tool_hash(cache, tool_name, home_user, payload_hash)
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


def _age_seconds(iso_ts: Optional[str]) -> Optional[float]:
    """Seconds since an ISO-8601 (``_now_iso``) timestamp, or None if unparseable."""
    if not isinstance(iso_ts, str):
        return None
    try:
        t = datetime.strptime(iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - t).total_seconds()


def read_run() -> dict:
    """The current run checkpoint (``{run_id, status, updated_at, done}``) or {}."""
    run = read_cache().get("run")
    return run if isinstance(run, dict) else {}


def start_run(run_id: str, done=None) -> None:
    """Begin the run checkpoint as ``in_progress``. ``done`` (an iterable of
    ``(tool_key, home_user)``) is carried forward from a resumed run so repeated
    interruptions still make monotonic progress."""
    cache = read_cache()
    seed = sorted({(str(t), str(u)) for t, u in (done or [])})
    cache["run"] = {
        "run_id": run_id,
        "status": "in_progress",
        "updated_at": _now_iso(),
        "done": [[t, u] for t, u in seed],
    }
    atomic_write_cache(cache)


def _record_run_done(cache: dict, tool_key: str, home_user: str) -> None:
    """Mutate `cache` in place to append ``(tool_key, home_user)`` to the run
    checkpoint's done-set and refresh its freshness. No-op if there's no run."""
    run = cache.get("run")
    if not isinstance(run, dict):
        return
    done = run.get("done")
    if not isinstance(done, list):
        done = []
    entry = [tool_key, home_user]
    if entry not in done:
        done.append(entry)
    run["done"] = done
    run["updated_at"] = _now_iso()
    cache["run"] = run


def mark_run_uploaded(tool_key: str, home_user: str) -> None:
    """Record that ``(tool_key, home_user)`` is now current on the backend and
    refresh the checkpoint's freshness. No-op if there's no active run."""
    cache = read_cache()
    if not isinstance(cache.get("run"), dict):
        return
    _record_run_done(cache, tool_key, home_user)
    atomic_write_cache(cache)


def record_report(tool_name: str, home_user: str, tool_key: str,
                  payload_hash: Optional[str] = None) -> None:
    """One atomic cache write recording that a (tool, user) report is current on
    the backend: persist the payload hash when ``payload_hash`` is given (an
    actual upload), and always append ``(tool_key, home_user)`` to the run
    checkpoint's done-set. Folds the former update_tool + mark_run_uploaded pair
    into a single write on the hot per-report path."""
    cache = read_cache()
    if payload_hash is not None:
        _set_tool_hash(cache, tool_name, home_user, payload_hash)
    _record_run_done(cache, tool_key, home_user)
    atomic_write_cache(cache)


def mark_run_completed() -> None:
    """Flip the checkpoint to ``completed`` so the next run starts fresh."""
    cache = read_cache()
    run = cache.get("run")
    if isinstance(run, dict):
        run["status"] = "completed"
        run["updated_at"] = _now_iso()
        cache["run"] = run
        atomic_write_cache(cache)


def resumable_done() -> set:
    """The ``{(tool_key, home_user)}`` already reported by a recent *interrupted*
    run (status still ``in_progress`` and touched within RESUME_WINDOW_SECONDS),
    or an empty set when there's nothing safe to resume.

    Fail-safe by construction: ANY malformed/anomalous checkpoint returns the
    empty set, i.e. a full scan. Cross-user forging is blocked upstream (the
    cache file is 0600 and the state dir is ownership-hardened). A same-user
    process CAN still plant a checkpoint — but that is out of scope: the same
    user already controls what discovery reads and can suppress the pre-existing
    hash cache too, and the blast radius is bounded to a single 150s window
    because the next non-resumed scan re-processes and re-reports everything."""
    run = read_run()
    if run.get("status") != "in_progress":
        return set()
    # A genuine interrupted run always carries a client-generated UUID run_id;
    # reject anything that doesn't look like one rather than trust arbitrary state.
    rid = run.get("run_id")
    if not isinstance(rid, str):
        return set()
    try:
        uuid.UUID(rid)
    except (ValueError, TypeError, AttributeError):
        return set()
    age = _age_seconds(run.get("updated_at"))
    if age is None or age < 0 or age > RESUME_WINDOW_SECONDS:
        return set()
    done = run.get("done")
    if not isinstance(done, list):
        return set()
    out = set()
    for d in done:
        if isinstance(d, (list, tuple)) and len(d) == 2:
            out.add((str(d[0]), str(d[1])))
    return out


def _read_lock_pid() -> Optional[int]:
    """The lock file's first whitespace-delimited token is the owner PID
    (written as ``"{pid} {iso}\\n"``). Returns None if it can't be parsed."""
    try:
        with LOCK_PATH.open("r", encoding="utf-8") as f:
            tokens = f.readline().split()
        return int(tokens[0]) if tokens else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    """POSIX liveness probe via signal 0. Conservative: on any error other than
    'no such process' (e.g. EPERM = exists but other-owned) treat as alive."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness probe. Conservative: returns False ONLY when the PID
    provably does not exist (OpenProcess fails with ERROR_INVALID_PARAMETER).
    ERROR_ACCESS_DENIED means the process exists but isn't openable (e.g. owned
    by another/elevated user) -> treated as alive. Any ctypes failure -> alive,
    so we never steal a lock from a still-running process."""
    if pid <= 0:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        # use_last_error=True makes ctypes save the thread error code immediately
        # after each call and expose it via ctypes.get_last_error(). A bare
        # kernel32.GetLastError() is unreliable: ctypes' own bookkeeping (or CPython
        # GC/refcount work) can issue Win32 calls between OpenProcess returning NULL
        # and the read, clobbering the error — which could misread a dead PID as
        # alive (steal never happens) or, worse, a live PID as dead (steal a lock
        # from a running scan). See the ctypes docs' use_last_error note.
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        ERROR_INVALID_PARAMETER = 87
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return ctypes.get_last_error() != ERROR_INVALID_PARAMETER
    except Exception:
        return True


def _owner_alive(pid: Optional[int]) -> Optional[bool]:
    """Cross-platform liveness for the lock's recorded PID. Returns True (alive),
    False (provably dead), or None (platform can't probe -> fall back to mtime).

    POSIX uses signal 0; Windows uses OpenProcess. Keeping the two probes behind
    one dispatcher lets acquire_lock()/_lock_is_live() steal a dead owner's lock
    on BOTH platforms instead of waiting out the full STALE_LOCK_SECONDS window."""
    if pid is None:
        return None
    if os.name == "posix":
        return _pid_alive(pid)
    if os.name == "nt":
        return _pid_alive_windows(pid)
    return None


def _lock_is_live() -> bool:
    """True if the lock is held by a still-running process.

    A lock whose recorded PID is dead is NOT live, so a discovery run killed
    without a chance to clean up (e.g. SIGKILL when the parent onboard/setup
    subprocess timeout fires) does not block the next run for the full
    STALE_LOCK_SECONDS window. A live owner still needs a fresh heartbeat to
    count as live, matching the original zombie tolerance and guarding against
    PID reuse. Falls back to mtime freshness only when the PID can't be read or
    the platform can't probe process liveness."""
    try:
        age = time.time() - LOCK_PATH.stat().st_mtime
    except OSError:
        return False
    fresh = age < STALE_LOCK_SECONDS
    alive = _owner_alive(_read_lock_pid())
    if alive is not None:
        return alive and fresh
    return fresh


def acquire_lock() -> str:
    """Best-effort exclusive lock. Returns "acquired", "contended" (held by a live process), or "setup_failed"."""
    global last_lock_error, last_lock_outcome
    last_lock_error = None
    last_lock_outcome = None
    if not _ensure_state_dir():
        # _ensure_state_dir() already created+verified the dir (or returned
        # False -> setup_failed); no redundant blind mkdir here (TOCTOU).
        last_lock_outcome = "setup_failed"
        return "setup_failed"

    if LOCK_PATH.exists() and _lock_is_live():
        last_lock_outcome = "contended"
        return "contended"

    _steal_reason = None
    if LOCK_PATH.exists():
        # Log WHY we're stealing so a rerun is distinguishable in logs: recovery
        # from a predecessor killed without cleanup (dead PID) vs a plain stale
        # (heartbeat-died) lock.
        _stale_pid = _read_lock_pid()
        if _stale_pid is not None and _owner_alive(_stale_pid) is False:
            _steal_reason = "stolen_dead_pid"
            logger.info(f"stealing discovery lock from dead PID {_stale_pid} (predecessor killed without cleanup)")
        else:
            _steal_reason = "stolen_stale"
            logger.info("stealing stale discovery lock (heartbeat older than the stale window)")
        try:
            LOCK_PATH.unlink()
        except OSError as e:
            last_lock_error = str(e)
            last_lock_outcome = "setup_failed"
            logger.warning(f"could not steal stale lock: {e}")
            return "setup_failed"

    try:
        fd = os.open(str(LOCK_PATH), os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW, 0o600)
    except FileExistsError:
        last_lock_outcome = "contended"
        return "contended"
    except OSError as e:
        last_lock_error = str(e)
        last_lock_outcome = "setup_failed"
        logger.warning(f"could not create lock: {e}")
        return "setup_failed"

    try:
        try:
            os.write(fd, f"{os.getpid()} {_now_iso()}\n".encode("utf-8"))
        finally:
            os.close(fd)
    except OSError as e:
        last_lock_error = str(e)
        last_lock_outcome = "setup_failed"
        logger.warning(f"could not write lock: {e}")
        # Remove the lock file we just created so a write failure can't leave a
        # fresh ghost lock that makes the next run see false contention.
        try:
            LOCK_PATH.unlink(missing_ok=True)
        except OSError:
            pass
        return "setup_failed"
    last_lock_outcome = _steal_reason or "acquired"
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
