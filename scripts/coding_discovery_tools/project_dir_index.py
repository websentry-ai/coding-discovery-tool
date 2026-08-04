"""Single-pass project directory index shared across per-tool extractors.

The discovery scan looks for many different tool marker directories (``.cursor``,
``.claude``, ``.roo``, ``.windsurf``, …) under the same home tree. Historically
every tool ran its OWN full recursive walk of that tree, matching a single
basename — so a scan re-``iterdir``-ed the identical directories a dozen or more
times (O(tools x filesystem)).

This module walks each subtree exactly ONCE, records a ``basename -> [dirs]`` map,
and memoizes it. Every walker then does a dict lookup instead of a fresh walk, so
the whole scan traverses the tree a single time (O(filesystem)).

Equivalence with the old per-basename walk is exact and deliberate. The traversal
uses the same filters (the caller injects the OS-specific skip predicate; the
``MAX_SEARCH_DEPTH`` cap is measured against ``root_path``; symlinked dirs are
recorded but never descended into). The old walk pruned at a match ("found
``.cursor`` — don't recurse into it"), which only ever hid a *same-named* dir
nested inside another; here we descend fully (so a *different* tool's dir nested
below is still found, as the old cross-basename walks did) and reproduce the prune
with :func:`outermost_only`. Dispatch stays depth-first, so output is unchanged.
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


# Memoized subtree indexes, keyed by (skip_id, root_path, current_dir). A scan is
# one sequential process, so this lives for the process and needs no locking.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> None:
    """Depth-first walk of ``current_dir`` recording every directory by basename.

    Mirrors the old ``walk_for_tool_directories`` recursion, minus the
    single-basename match/prune: one pass serves every tool, and it descends
    through matches (so a different tool's dir nested below is still found).

    Only HIDDEN directories (dot-prefixed) are recorded. Every tool-marker dir the
    scan looks for is hidden (``.cursor``, ``.claude``, ``.roo``, …), so this holds
    just the handful of hidden dirs per tree instead of every directory — bounding
    the cache's memory — without changing any lookup. The traversal still descends
    into non-hidden dirs to reach hidden ones nested inside them. A directory is
    always recorded before anything inside it, which :func:`outermost_only` relies on.

    Returns True if ``current_dir`` itself was readable. A caller uses this to avoid
    caching a result whose root could not be listed, so a one-off failure isn't
    baked in for the whole scan (the old per-tool walks re-listed each time).

    Uses :func:`os.scandir`, so ``is_dir``/``is_symlink`` come from the directory
    entry the OS already returned (no extra ``stat`` per item). ``Path.iterdir``
    is itself built on ``scandir``, so iteration order — and therefore dispatch
    order — is unchanged.
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
                # is_dir() follows symlinks (matches the old Path.is_dir()).
                if not entry.is_dir():
                    continue
                if entry.name.startswith("."):
                    index.setdefault(entry.name, []).append(item)
                # A name match still extracts even on a symlink (the old walk
                # checked the name before the symlink), but we never descend
                # into a symlink.
                if not entry.is_symlink():
                    _collect(root_path, item, should_skip, index)
            except (PermissionError, OSError, ValueError):
                # ValueError: item not under root_path.
                continue
            except Exception as e:
                # As in the old walk, one unexpected bad entry (e.g. an odd name
                # that trips a predicate) must not abort the whole traversal.
                logger.debug("skipping %s: %s", entry.path, e)
                continue
    return True


def get_subtree_index(root_path: Path, current_dir: Path,
                      should_skip: Callable[[Path], bool],
                      skip_id: str) -> Dict[str, List[Path]]:
    """Return the memoized ``basename -> [dirs]`` map for ``current_dir``.

    ``root_path`` is the depth reference (matches the old walk's
    ``item.relative_to(root_path)``); ``skip_id`` distinguishes skip policies so
    two callers that prune differently never share a cached traversal.

    If the subtree root can't be listed the (empty) result is returned but NOT
    cached, so a later tool re-attempts rather than inheriting a one-off failure.
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
    """Drop any directory nested under another one in the list.

    Reproduces the old walk's "found the tool dir — don't recurse into it": on any
    root-to-leaf path only the shallowest match survives. The index records a dir
    before its descendants, so a single "skip if an already-kept dir is an
    ancestor" pass suffices and preserves dispatch order.
    """
    kept: List[Path] = []
    for path in dirs:
        if not any(anchor in path.parents for anchor in kept):
            kept.append(path)
    return kept


def clear_cache() -> None:
    """Drop all memoized indexes (used for test isolation)."""
    _INDEX_CACHE.clear()
