"""Single-pass directory index shared across per-tool extractors: each subtree
is walked once into a ``basename -> [dirs]`` map instead of once per tool."""

import logging
import os
import stat
import threading
from pathlib import Path
from typing import Callable, Dict, List, Optional

try:
    from .constants import MAX_SEARCH_DEPTH
except ImportError:  # pragma: no cover - direct-script execution fallback
    from constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


# Keyed by (skip_id, root_path, current_dir). Windows MCP discovery fans out over
# a ThreadPoolExecutor, so the cache is guarded by a lock.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}
_INDEX_LOCK = threading.Lock()

# Project-root marker FILES recorded by the same walk that indexes hidden dirs.
# OpenCode's primary project config is `<project>/opencode.json[c]` with no
# `.opencode/` dir beside it, so a dir-only index would miss most projects.
# Costs one set lookup per entry in a walk that already happens. Stored under a
# `file:` key so directory matchers never see them.
INDEXED_FILE_MARKERS = frozenset({"opencode.json", "opencode.jsonc"})
_FILE_KEY_PREFIX = "file:"


def _is_dispatchable(path: Path) -> bool:
    """True if ``path`` still resolves to a real directory. ``stat`` follows, so a
    stowed tool folder passes; containment is ``_within_scan_root``'s job."""
    try:
        return stat.S_ISDIR(os.stat(str(path)).st_mode)
    except OSError:
        return False


def _within_scan_root(target: Path, root_real: str) -> bool:
    """True if ``target`` resolves inside the scan root. ``realpath`` follows every
    link, so a dir swapped for one pointing elsewhere is refused. Never raises."""
    try:
        real = os.path.normcase(os.path.realpath(str(target)))
    except OSError:
        return False
    base = os.path.normcase(root_real)
    if real == base:
        return True
    # rstrip so a root that resolves to "/" doesn't become "//" and reject
    # everything; normcase folds case/separators on Windows.
    return real.startswith(base.rstrip(os.sep) + os.sep)


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> Optional[bool]:
    """Index hidden dirs by basename, ancestor-first; never follows links. Returns
    None (dir unopenable), False (a listing broke off partway; don't cache), or True."""
    try:
        scan = os.scandir(current_dir)
    except (PermissionError, OSError) as e:
        logger.debug("could not open %s: %s", current_dir, e)
        return None

    truncated = False
    with scan:
        try:
            for entry in scan:
                try:
                    item = Path(entry.path)
                    if should_skip(item):
                        continue
                    if len(item.relative_to(root_path).parts) > MAX_SEARCH_DEPTH:
                        continue
                    if not entry.is_dir():  # follows symlinks, like Path.is_dir()
                        if (entry.name in INDEXED_FILE_MARKERS
                                and not entry.is_symlink() and entry.is_file()):
                            index.setdefault(_FILE_KEY_PREFIX + entry.name, []).append(item)
                        continue
                    if entry.name.startswith("."):
                        index.setdefault(entry.name, []).append(item)
                    if not entry.is_symlink():  # never descend a link/junction
                        if _collect(root_path, item, should_skip, index) is False:
                            truncated = True  # only a mid-list break stops caching
                except (PermissionError, OSError, ValueError):
                    continue
                except Exception as e:  # one bad entry must not abort the walk
                    logger.debug("skipping %s: %s", entry.path, e)
                    continue
        except (PermissionError, OSError) as e:  # current_dir's own iteration cut short
            logger.debug("iteration stopped for %s: %s", current_dir, e)
            return False
    return False if truncated else True


def get_subtree_index(root_path: Path, current_dir: Path,
                      should_skip: Callable[[Path], bool],
                      skip_id: str) -> Dict[str, List[Path]]:
    """Memoized ``basename -> [dirs]`` map, keyed by ``skip_id``. Cached unless the
    root was unopenable or a listing broke off partway, so a later lookup retries."""
    key = (skip_id, str(root_path), str(current_dir))
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    # Build outside the lock so parallel walks don't serialize; publish atomically
    # (a duplicate concurrent build is wasted but harmless).
    index: Dict[str, List[Path]] = {}
    if _collect(root_path, current_dir, should_skip, index) is not True:
        # Root unopenable (None) or a listing broke off partway (False): may work
        # next time, so don't cache. A locked dir returns True and stays cached.
        logger.debug("index not safe to cache, re-attempt later: %s", current_dir)
        return index
    with _INDEX_LOCK:
        return _INDEX_CACHE.setdefault(key, index)


def outermost_only(dirs: List[Path]) -> List[Path]:
    """Keep only the shallowest match on each path (drop any under another match), in
    input order, so the index and fallback walks dispatch the same set the same way."""
    return [p for p in dirs if not any(other in p.parents for other in dirs)]


def _walk_direct(root_path: Path, current_dir: Path,
                 is_match: Callable[[str], bool],
                 on_match: Callable[[Path], None],
                 should_skip: Callable[[Path], bool]) -> None:
    """Per-tool walk: the index fallback, and the route for non-hidden markers.
    Matches are dispatched, never descended; no shared state, so faults stay local."""
    try:
        scan = os.scandir(current_dir)
    except (PermissionError, OSError):
        return
    with scan:
        try:
            for entry in scan:
                try:
                    item = Path(entry.path)
                    if should_skip(item):
                        continue
                    if len(item.relative_to(root_path).parts) > MAX_SEARCH_DEPTH:
                        continue
                    if not entry.is_dir():
                        continue
                    if is_match(entry.name):
                        root_real = os.path.realpath(str(root_path))
                        if _is_dispatchable(item) and _within_scan_root(item, root_real):
                            on_match(item)
                        continue
                    if not entry.is_symlink():  # never descend a link/junction
                        _walk_direct(root_path, item, is_match, on_match, should_skip)
                except (PermissionError, OSError, ValueError):
                    continue
                except Exception as e:
                    logger.debug("skipping %s: %s", entry.path, e)
                    continue
        except (PermissionError, OSError) as e:  # iterator faulted mid-walk
            logger.debug("iteration stopped for %s: %s", current_dir, e)


def dispatch_matches(root_path: Path, current_dir: Path,
                     should_skip: Callable[[Path], bool], skip_id: str,
                     is_match: Callable[[str], bool],
                     on_match: Callable[[Path], None],
                     markers_all_hidden: bool = True) -> None:
    """Dispatch matches to ``on_match`` via the shared index, falling back to an
    independent walk on fault. Non-hidden markers need ``markers_all_hidden=False``."""
    if not markers_all_hidden:
        _walk_direct(root_path, current_dir, is_match, on_match, should_skip)
        return
    try:
        index = get_subtree_index(root_path, current_dir, should_skip, skip_id)
        # Dirs for one name come out parents-first; a matcher over several names
        # (skills) can mix a child in before its parent, which outermost_only sorts out.
        matches = [d for name, dirs in index.items() if is_match(name) for d in dirs]
        targets = outermost_only(matches)
    except Exception as e:
        logger.warning("shared index failed (%s); independent walk fallback", e)
        _walk_direct(root_path, current_dir, is_match, on_match, should_skip)
        return
    root_real = os.path.realpath(str(root_path))
    for target in targets:
        # Re-validate before reading config through the path: a target swapped or
        # removed since indexing is dropped (with a signal), not read silently.
        if not _is_dispatchable(target):
            logger.warning("index target dropped, no longer a directory: %s", target)
            continue
        if not _within_scan_root(target, root_real):
            logger.warning("index target dropped, resolves outside scan root: %s", target)
            continue
        # Wrap on_match so an extractor error is contained the same way the direct
        # walk contains it, regardless of which route ran.
        try:
            on_match(target)
        except (PermissionError, OSError, ValueError):
            continue
        except Exception as e:
            logger.debug("on_match failed for %s: %s", target, e)


def _is_plain_file(path: Path) -> bool:
    """Regular file, not a symlink (lstat: the link itself is judged)."""
    try:
        return stat.S_ISREG(os.lstat(str(path)).st_mode)
    except OSError:
        return False


def _walk_direct_files(root_path: Path, current_dir: Path, file_names,
                       on_match: Callable[[Path], None],
                       should_skip: Callable[[Path], bool]) -> None:
    """Index fallback for marker files. Never descends links."""
    try:
        scan = os.scandir(current_dir)
    except (PermissionError, OSError):
        return
    root_real = os.path.realpath(str(root_path))
    with scan:
        try:
            for entry in scan:
                try:
                    item = Path(entry.path)
                    if should_skip(item):
                        continue
                    if len(item.relative_to(root_path).parts) > MAX_SEARCH_DEPTH:
                        continue
                    if entry.is_dir():
                        if not entry.is_symlink():
                            _walk_direct_files(root_path, item, file_names, on_match, should_skip)
                        continue
                    if entry.name in file_names and _is_plain_file(item) \
                            and _within_scan_root(item, root_real):
                        on_match(item)
                except (PermissionError, OSError, ValueError):
                    continue
                except Exception as e:
                    logger.debug("skipping %s: %s", entry.path, e)
                    continue
        except (PermissionError, OSError) as e:
            logger.debug("iteration stopped for %s: %s", current_dir, e)


def dispatch_file_matches(root_path: Path, current_dir: Path,
                          should_skip: Callable[[Path], bool], skip_id: str,
                          file_names, on_match: Callable[[Path], None]) -> None:
    """Dispatch project-root marker FILES (must be in ``INDEXED_FILE_MARKERS``)
    to ``on_match`` via the shared index, falling back to a direct walk."""
    unknown = set(file_names) - INDEXED_FILE_MARKERS
    if unknown:
        # Not in the index; the direct walk is the only route that can find them.
        logger.debug("file markers %s not indexed; direct walk", sorted(unknown))
        _walk_direct_files(root_path, current_dir, set(file_names), on_match, should_skip)
        return
    try:
        index = get_subtree_index(root_path, current_dir, should_skip, skip_id)
        targets = [f for name in file_names for f in index.get(_FILE_KEY_PREFIX + name, [])]
    except Exception as e:
        logger.warning("shared index failed (%s); independent walk fallback", e)
        _walk_direct_files(root_path, current_dir, set(file_names), on_match, should_skip)
        return
    root_real = os.path.realpath(str(root_path))
    for target in targets:
        if not _is_plain_file(target):
            logger.warning("index target dropped, no longer a regular file: %s", target)
            continue
        if not _within_scan_root(target, root_real):
            logger.warning("index target dropped, resolves outside scan root: %s", target)
            continue
        try:
            on_match(target)
        except (PermissionError, OSError, ValueError):
            continue
        except Exception as e:
            logger.debug("on_match failed for %s: %s", target, e)


def clear_cache() -> None:
    """Drop all cached indexes (test isolation)."""
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()
