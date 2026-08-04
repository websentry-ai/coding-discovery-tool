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

from pathlib import Path
from typing import Callable, Dict, List

try:
    from .constants import MAX_SEARCH_DEPTH
except ImportError:  # pragma: no cover - direct-script execution fallback
    from constants import MAX_SEARCH_DEPTH


# Memoized subtree indexes, keyed by (skip_id, root_path, current_dir). A scan is
# one sequential process, so this lives for the process and needs no locking.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> None:
    """Depth-first walk of ``current_dir`` recording every directory by basename.

    Mirrors the old ``walk_for_tool_directories`` recursion, minus the
    single-basename match/prune: it records ALL directories (one pass serves every
    tool) and descends through matches (so nested different-tool dirs are still
    found). A directory is always recorded before anything inside it, which
    :func:`outermost_only` relies on.
    """
    try:
        entries = list(current_dir.iterdir())
    except (PermissionError, OSError):
        return

    for item in entries:
        try:
            if should_skip(item):
                continue
            if len(item.relative_to(root_path).parts) > MAX_SEARCH_DEPTH:
                continue
            if not item.is_dir():
                continue
            index.setdefault(item.name, []).append(item)
            # A name match still extracts even on a symlink (the old walk checked
            # the name before the symlink), but we never descend into a symlink.
            if not item.is_symlink():
                _collect(root_path, item, should_skip, index)
        except (PermissionError, OSError, ValueError):
            # ValueError: item not under root_path. As in the old walk, one bad
            # entry must not abort the traversal.
            continue


def get_subtree_index(root_path: Path, current_dir: Path,
                      should_skip: Callable[[Path], bool],
                      skip_id: str) -> Dict[str, List[Path]]:
    """Return the memoized ``basename -> [dirs]`` map for ``current_dir``.

    ``root_path`` is the depth reference (matches the old walk's
    ``item.relative_to(root_path)``); ``skip_id`` distinguishes skip policies so
    two callers that prune differently never share a cached traversal.
    """
    key = (skip_id, str(root_path), str(current_dir))
    index = _INDEX_CACHE.get(key)
    if index is None:
        index = {}
        _collect(root_path, current_dir, should_skip, index)
        _INDEX_CACHE[key] = index
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
