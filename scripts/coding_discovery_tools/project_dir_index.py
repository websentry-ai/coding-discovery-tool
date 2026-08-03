"""Single-pass project directory index shared across per-tool extractors.

The discovery scan looks for many different tool marker directories (``.cursor``,
``.claude``, ``.roo``, ``.windsurf``, …) under the same home / filesystem tree.
Historically every tool ran its OWN full recursive walk of that tree, matching a
single basename — so a scan re-``iterdir``-ed the identical directories a dozen or
more times (O(tools x filesystem)).

This module walks each subtree exactly ONCE, recording a ``basename -> [dirs]``
map, and memoizes it keyed by the subtree that was actually traversed. Every walker
then becomes a dict lookup instead of a fresh walk, so the whole scan traverses the
tree a single time (O(filesystem)).

Behavioral equivalence with the old per-basename walk is exact and deliberate:

* The traversal replicates the old walk's filters verbatim — the caller injects the
  OS-specific skip predicate (macOS adds ``is_home_dotdir_descendant``; Linux does
  not), the ``MAX_SEARCH_DEPTH`` cap is measured the same way (path parts relative
  to ``root_path``), and symlinked directories are recorded but never descended
  into, exactly as before.
* The old walk pruned at a match ("found ``.cursor`` — don't recurse into it"),
  which only ever hid a *same-named* directory nested inside another. Here we do a
  full descent (needed so a different tool's dir nested below is still found — which
  the old cross-basename walks did too) and reproduce the prune with
  :func:`outermost_only`, which drops any match nested under another match of the
  *same* basename. The two are identical for every basename simultaneously.

Dispatch order is preserved (depth-first, as the old walk emitted matches) so
downstream output is unchanged, not merely equivalent after sorting.
"""

import threading
from pathlib import Path
from typing import Callable, Dict, List

try:
    from .constants import MAX_SEARCH_DEPTH
except ImportError:  # pragma: no cover - direct-script execution fallback
    from constants import MAX_SEARCH_DEPTH


# Memoized subtree indexes, keyed by (skip_id, root_path, current_dir). A scan is
# one process, so this lives for the process and is cleared implicitly on exit.
_INDEX_CACHE: Dict[tuple, Dict[str, List[Path]]] = {}
_INDEX_LOCK = threading.Lock()


def _collect(root_path: Path, current_dir: Path,
             should_skip: Callable[[Path], bool],
             index: Dict[str, List[Path]]) -> None:
    """Depth-first walk of ``current_dir`` recording every directory by basename.

    Mirrors the old ``walk_for_tool_directories`` recursion exactly, minus the
    single-basename match/prune: it records ALL directories (so one pass serves
    every tool) and descends through matches (so nested different-tool dirs are
    still found), leaving same-basename de-nesting to :func:`outermost_only`.
    """
    try:
        for item in current_dir.iterdir():
            try:
                if should_skip(item):
                    continue
                try:
                    depth = len(item.relative_to(root_path).parts)
                except ValueError:
                    continue
                if depth > MAX_SEARCH_DEPTH:
                    continue

                if item.is_dir():
                    index.setdefault(item.name, []).append(item)
                    # Symlinked dirs are recorded (a name match still extracts,
                    # matching the old name-before-symlink check) but never
                    # descended into.
                    if item.is_symlink():
                        continue
                    _collect(root_path, item, should_skip, index)
            except (PermissionError, OSError):
                continue
            except Exception:
                # Same defensive swallow as the old walk: one bad entry must not
                # abort the whole traversal.
                continue
    except (PermissionError, OSError):
        return


def get_subtree_index(root_path: Path, current_dir: Path,
                      should_skip: Callable[[Path], bool],
                      skip_id: str) -> Dict[str, List[Path]]:
    """Return the memoized ``basename -> [dirs]`` map for ``current_dir``.

    ``root_path`` is the depth reference (matches the old walk's
    ``item.relative_to(root_path)``); ``skip_id`` distinguishes skip policies so
    two callers with different pruning never share a cached traversal.
    """
    key = (skip_id, str(root_path), str(current_dir))
    cached = _INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached is not None:
            return cached
        index: Dict[str, List[Path]] = {}
        _collect(root_path, current_dir, should_skip, index)
        _INDEX_CACHE[key] = index
        return index


def outermost_only(dirs: List[Path]) -> List[Path]:
    """Drop any directory nested under another in ``dirs`` (same-basename lists).

    Reproduces the old walk's "found the tool dir — don't recurse into it": only
    the shallowest match on any root-to-leaf path survives. Input order (depth-first
    dispatch order) is preserved for the survivors.
    """
    if len(dirs) <= 1:
        return list(dirs)
    deduped = list(dict.fromkeys(dirs))
    dropped = set()
    anchors: List[Path] = []
    for p in sorted(deduped, key=lambda d: len(d.parts)):
        if any(anchor in p.parents for anchor in anchors):
            dropped.add(p)
        else:
            anchors.append(p)
    return [p for p in deduped if p not in dropped]


def clear_cache() -> None:
    """Drop all memoized indexes (test isolation / long-lived hosts)."""
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()
