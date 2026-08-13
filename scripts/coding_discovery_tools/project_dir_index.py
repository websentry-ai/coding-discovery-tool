"""Single-pass directory index shared across per-tool extractors.

Walks each subtree once into a ``basename -> [dirs]`` map instead of once per
tool, so the whole scan traverses the tree a single time. Dispatch is kept
byte-identical to the old per-tool walk.
"""

import logging
import os
import stat
import threading
from pathlib import Path
from typing import Callable, Dict, List

try:
    from .constants import MAX_SEARCH_DEPTH
except ImportError:  # pragma: no cover - direct-script execution fallback
    from constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


# Keyed by (skip_id, root_path, current_dir). Windows MCP discovery fans out over
# a ThreadPoolExecutor, so the cache is guarded by a lock.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}
_INDEX_LOCK = threading.Lock()


def _is_dispatchable(path: Path) -> bool:
    """True only if ``path`` is still a real directory (or a Windows junction),
    not a symlink. Uses one ``lstat``: a symlink comes back ``S_IFLNK`` (so
    ``S_ISDIR`` is False and it is rejected), while a junction is ``S_IFDIR``
    with a reparse tag (so it is allowed, matching the old walk). Checked at
    dispatch time so a dir swapped for a symlink after indexing is refused before
    config is read through it. Never raises."""
    try:
        return stat.S_ISDIR(os.lstat(str(path)).st_mode)
    except OSError:
        return False


def _within_scan_root(target: Path, root_real: str) -> bool:
    """True if ``target`` fully resolves inside the scan root. The leaf ``lstat``
    only sees the leaf, so an indexed ANCESTOR (e.g. ``~/proj``) swapped for a
    symlink after indexing would leave the cached leaf resolving outside the
    scanned tree — and a privileged multi-user scan must not read another user's
    configs. ``realpath`` resolves the whole path; ``root_real`` is the caller's
    pre-resolved ``realpath(root_path)``. Never raises."""
    try:
        real = os.path.realpath(str(target))
    except OSError:
        return False
    return real == root_real or real.startswith(root_real + os.sep)


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> None:
    """Record every hidden dir under ``current_dir`` by basename. Returns True if
    ``current_dir`` was readable (callers skip caching an unreadable root).

    Only hidden dirs are stored (all tool markers are hidden) to bound memory, but
    the walk still descends into non-hidden dirs. scandir gives is_dir/is_symlink
    without an extra stat. A dir is recorded before its children — outermost_only
    relies on that. Symlinks and junctions are recorded (dispatch re-validates and
    drops symlinks) but never descended into: a privileged scan must not follow a
    user's link into another tree.
    """
    try:
        scan = os.scandir(current_dir)
    except (PermissionError, OSError) as e:
        logger.debug("could not read %s: %s", current_dir, e)
        return False

    with scan:
        for entry in scan:
            try:
                item = Path(entry.path)
                if should_skip(item):
                    continue
                if len(item.relative_to(root_path).parts) > MAX_SEARCH_DEPTH:
                    continue
                if not entry.is_dir():  # follows symlinks, like Path.is_dir()
                    continue
                if entry.name.startswith("."):
                    index.setdefault(entry.name, []).append(item)
                if not entry.is_symlink():  # never descend a link/junction
                    _collect(root_path, item, should_skip, index)
            except (PermissionError, OSError, ValueError):
                continue
            except Exception as e:  # one bad entry must not abort the walk
                logger.debug("skipping %s: %s", entry.path, e)
                continue
    return True


def get_subtree_index(root_path: Path, current_dir: Path,
                      should_skip: Callable[[Path], bool],
                      skip_id: str) -> Dict[str, List[Path]]:
    """Memoized ``basename -> [dirs]`` map for ``current_dir``.

    ``skip_id`` keeps callers with different prunes from sharing a tree. An
    unreadable root is returned empty but not cached, so a later tool retries.
    """
    key = (skip_id, str(root_path), str(current_dir))
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    # Build outside the lock so parallel walks of different subtrees don't
    # serialize; publish atomically. A duplicate concurrent build of the same key
    # is wasted but harmless (setdefault keeps whichever lands first).
    index: Dict[str, List[Path]] = {}
    readable = _collect(root_path, current_dir, should_skip, index)
    if not readable:
        logger.warning("subtree root unreadable, not caching: %s", current_dir)
        return index
    with _INDEX_LOCK:
        return _INDEX_CACHE.setdefault(key, index)


def outermost_only(dirs: List[Path]) -> List[Path]:
    """Keep only the shallowest match on any path (the old "don't recurse into a
    matched dir" prune). Input is dir-before-descendant order, so one pass works."""
    kept: List[Path] = []
    for path in dirs:
        if not any(anchor in path.parents for anchor in kept):
            kept.append(path)
    return kept


def _walk_direct(root_path: Path, current_dir: Path,
                 is_match: Callable[[str], bool],
                 on_match: Callable[[Path], None],
                 should_skip: Callable[[Path], bool]) -> None:
    """Stateless per-tool walk used as the index fallback and for callers whose
    marker is not a hidden dir (the index stores only hidden dirs). Matches are
    dispatched and not descended into; links/junctions are never descended. A
    matched dir is re-validated the same way as the index path before dispatch.
    No shared state, so a failure here is contained to one tool."""
    try:
        scan = os.scandir(current_dir)
    except (PermissionError, OSError):
        return
    with scan:
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


def dispatch_matches(root_path: Path, current_dir: Path,
                     should_skip: Callable[[Path], bool], skip_id: str,
                     is_match: Callable[[str], bool],
                     on_match: Callable[[Path], None],
                     markers_all_hidden: bool = True) -> None:
    """Dispatch matching dirs to ``on_match`` via the shared index, falling back to
    an independent walk if the index raises — so an index fault costs one tool, not
    the whole scan. ``on_match`` handles its own errors (a failure there is not an
    index failure, so no re-walk / double dispatch).

    The index stores only hidden dirs, so a caller matching a NON-hidden marker
    must pass ``markers_all_hidden=False`` to route straight to the direct walk;
    otherwise the marker would silently never match. All current markers are
    hidden, hence the default."""
    if not markers_all_hidden:
        _walk_direct(root_path, current_dir, is_match, on_match, should_skip)
        return
    try:
        index = get_subtree_index(root_path, current_dir, should_skip, skip_id)
        matches = [d for name, dirs in index.items() if is_match(name) for d in dirs]
        # Sort shallowest-first so an ancestor always precedes its descendants
        # before pruning. Index buckets are per-basename, so a differently-cased
        # nested match can otherwise appear ahead of its ancestor.
        matches.sort(key=lambda p: len(p.parts))
        targets = outermost_only(matches)
    except Exception as e:
        logger.warning("shared index failed (%s); independent walk fallback", e)
        _walk_direct(root_path, current_dir, is_match, on_match, should_skip)
        return
    # Resolve the scan root once for the containment re-check below.
    root_real = os.path.realpath(str(root_path))
    for target in targets:
        # Re-validate before reading config through the path: the leaf must still be
        # a real dir/junction (not a symlink swapped in since indexing — junctions
        # pass, symlinks and vanished dirs are dropped), and it must still resolve
        # inside the scan root (an ancestor swapped for a symlink would escape it).
        if _is_dispatchable(target) and _within_scan_root(target, root_real):
            on_match(target)


def clear_cache() -> None:
    """Drop all memoized indexes (test isolation)."""
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()
