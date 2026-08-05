"""Single-pass directory index shared across per-tool extractors.

Walks each subtree once into a ``basename -> [dirs]`` map instead of once per
tool, so the whole scan traverses the tree a single time. Dispatch is kept
byte-identical to the old per-tool walk.
"""

import logging
import os
from pathlib import Path
from typing import Callable, Dict, List

try:
    from .constants import MAX_SEARCH_DEPTH
except ImportError:  # pragma: no cover - direct-script execution fallback
    from constants import MAX_SEARCH_DEPTH

logger = logging.getLogger(__name__)


# Keyed by (skip_id, root_path, current_dir). One sequential scan per process, so
# no locking needed.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> None:
    """Record every hidden dir under ``current_dir`` by basename. Returns True if
    ``current_dir`` was readable (callers skip caching an unreadable root).

    Only hidden dirs are stored (all tool markers are hidden) to bound memory, but
    the walk still descends into non-hidden dirs. scandir gives is_dir/is_symlink
    without an extra stat. A dir is recorded before its children — outermost_only
    relies on that.
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
                if not entry.is_symlink():  # record symlinks, never descend them
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
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = {}
        readable = _collect(root_path, current_dir, should_skip, index)
        if readable:
            _INDEX_CACHE[key] = index
        else:
            logger.warning("subtree root unreadable, not caching: %s", current_dir)
    return index


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
    """Stateless per-tool walk used as the index fallback. Matches are dispatched
    and not descended into; symlinks are never descended. No shared state, so a
    failure here is contained to one tool."""
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
                    on_match(item)
                    continue
                if not entry.is_symlink():
                    _walk_direct(root_path, item, is_match, on_match, should_skip)
            except (PermissionError, OSError, ValueError):
                continue
            except Exception as e:
                logger.debug("skipping %s: %s", entry.path, e)
                continue


def dispatch_matches(root_path: Path, current_dir: Path,
                     should_skip: Callable[[Path], bool], skip_id: str,
                     is_match: Callable[[str], bool],
                     on_match: Callable[[Path], None]) -> None:
    """Dispatch matching dirs to ``on_match`` via the shared index, falling back to
    an independent walk if the index raises — so an index fault costs one tool, not
    the whole scan. ``on_match`` handles its own errors (a failure there is not an
    index failure, so no re-walk / double dispatch)."""
    try:
        index = get_subtree_index(root_path, current_dir, should_skip, skip_id)
        targets = outermost_only(
            [d for name, dirs in index.items() if is_match(name) for d in dirs]
        )
    except Exception as e:
        logger.warning("shared index failed (%s); independent walk fallback", e)
        _walk_direct(root_path, current_dir, is_match, on_match, should_skip)
        return
    for target in targets:
        on_match(target)


def clear_cache() -> None:
    """Drop all memoized indexes (test isolation)."""
    _INDEX_CACHE.clear()
