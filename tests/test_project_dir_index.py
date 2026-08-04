"""Contract tests for the shared single-pass project directory index.

The index replaces N independent per-basename tree walks with one memoized walk.
These tests pin the exact behaviors the consolidation relies on to stay identical
to the old ``walk_for_tool_directories`` recursion: full-descent recording,
same-basename de-nesting (``outermost_only``), the depth cap, symlink handling,
skip-predicate pruning, and per-key memoization.
"""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.project_dir_index import (  # noqa: E402
    get_subtree_index,
    outermost_only,
    clear_cache,
)
from coding_discovery_tools.constants import MAX_SEARCH_DEPTH  # noqa: E402


def _never_skip(_p: Path) -> bool:
    return False


class TestOutermostOnly(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertEqual(outermost_only([]), [])
        p = Path("/a/.cursor")
        self.assertEqual(outermost_only([p]), [p])

    def test_drops_nested_same_basename(self):
        outer = Path("/a/.cursor")
        inner = Path("/a/.cursor/x/.cursor")
        # The index records a dir before its descendants, so the outer comes
        # first; old walk found only the outer one (pruned at match).
        self.assertEqual(outermost_only([outer, inner]), [outer])

    def test_keeps_siblings(self):
        a = Path("/a/.cursor")
        b = Path("/b/.cursor")
        self.assertEqual(set(outermost_only([a, b])), {a, b})

    def test_preserves_input_order_of_survivors(self):
        a = Path("/b/.cursor")
        b = Path("/a/.cursor")
        c = Path("/a/.cursor/n/.cursor")  # nested under b -> dropped
        self.assertEqual(outermost_only([a, b, c]), [a, b])


class TestSubtreeIndex(unittest.TestCase):
    def setUp(self):
        clear_cache()
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        clear_cache()
        self._tmp.cleanup()

    def mk(self, *parts):
        d = self.root.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_records_dirs_by_basename(self):
        self.mk("proj", ".cursor")
        self.mk("proj", "src", ".windsurf")
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        self.assertIn(".cursor", idx)
        self.assertIn(".windsurf", idx)
        self.assertEqual([p.name for p in idx[".cursor"]], [".cursor"])

    def test_cross_basename_nesting_is_found(self):
        # A .cursor nested inside a .roo must still be recorded (the old
        # per-basename walks descended through non-matching dirs).
        self.mk("wrap", ".roo", "inner", ".cursor")
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        found = {str(p) for p in idx.get(".cursor", [])}
        self.assertTrue(any(".roo" in p and p.endswith(".cursor") for p in found))

    def test_same_basename_nesting_recorded_then_denested(self):
        self.mk("a", ".cursor")
        self.mk("a", ".cursor", "deep", ".cursor")
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        # Both are recorded by the full-descent walk...
        self.assertEqual(len(idx[".cursor"]), 2)
        # ...but outermost_only collapses them to the shallow one, matching the
        # old "don't recurse into a matched dir" prune.
        kept = outermost_only(idx[".cursor"])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0], self.root / "a" / ".cursor")

    def test_depth_cap(self):
        # Build a chain one level beyond MAX_SEARCH_DEPTH and plant a marker at
        # the bottom; it must NOT be recorded.
        chain = self.root
        for i in range(MAX_SEARCH_DEPTH + 1):
            chain = chain / f"d{i}"
        (chain / ".cursor").mkdir(parents=True, exist_ok=True)
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        for p in idx.get(".cursor", []):
            depth = len(p.relative_to(self.root).parts)
            self.assertLessEqual(depth, MAX_SEARCH_DEPTH)

    def test_skip_predicate_prunes_subtree(self):
        self.mk("keep", ".cursor")
        self.mk("node_modules", "pkg", ".cursor")

        def skip(p: Path) -> bool:
            return "node_modules" in p.parts

        idx = get_subtree_index(self.root, self.root, skip, "t")
        found = {str(p) for p in idx.get(".cursor", [])}
        self.assertTrue(any("keep" in p for p in found))
        self.assertFalse(any("node_modules" in p for p in found))

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_symlinked_dir_recorded_but_not_descended(self):
        target = self.mk("real")
        (target / ".cursor").mkdir()
        link = self.root / "link"
        os.symlink(target, link)
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        # The symlink dir itself is recorded by basename...
        self.assertIn("link", idx)
        # ...but we never descend into it, so its .cursor is not reached via the link.
        via_link = [p for p in idx.get(".cursor", []) if "link" in p.parts]
        self.assertEqual(via_link, [])

    def test_memoized_per_key(self):
        self.mk("p", ".cursor")
        a = get_subtree_index(self.root, self.root, _never_skip, "t")
        b = get_subtree_index(self.root, self.root, _never_skip, "t")
        self.assertIs(a, b)  # same cached object, no re-walk
        c = get_subtree_index(self.root, self.root, _never_skip, "other")
        self.assertIsNot(a, c)  # different skip policy -> separate index


if __name__ == "__main__":
    unittest.main()
