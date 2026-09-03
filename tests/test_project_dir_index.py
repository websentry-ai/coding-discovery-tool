"""Contract tests for the shared single-pass project directory index —
de-nesting, depth cap, symlink handling, skip pruning, memoization, and the
fail-safe fallback."""

import os
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools import project_dir_index as pdi  # noqa: E402
from coding_discovery_tools.project_dir_index import (  # noqa: E402
    get_subtree_index,
    outermost_only,
    dispatch_matches,
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
        link = self.root / ".link"  # a hidden symlink to a directory
        os.symlink(target, link)
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        # The symlinked dir is recorded by basename (dispatch re-validates and
        # drops it before reading config)...
        self.assertIn(".link", idx)
        # ...but never descended, so its .cursor is not reached via the link — a
        # privileged scan must not follow a user's symlink into another tree.
        via_link = [p for p in idx.get(".cursor", []) if ".link" in p.parts]
        self.assertEqual(via_link, [])

    def test_non_hidden_dirs_are_not_stored_but_are_descended(self):
        # A non-hidden dir is traversed (so nested hidden dirs are found) but not
        # itself stored, which keeps the cache small.
        self.mk("src", "components", ".cursor")
        idx = get_subtree_index(self.root, self.root, _never_skip, "t")
        self.assertNotIn("src", idx)
        self.assertNotIn("components", idx)
        # Compare path parts (OS-agnostic — Windows uses backslash separators).
        found = idx.get(".cursor", [])
        self.assertTrue(any(p.parts[-2:] == ("components", ".cursor") for p in found))

    def test_unreadable_root_is_not_cached(self):
        # First lookup of a not-yet-existing subtree root is unreadable -> empty
        # and NOT cached, so a later lookup re-attempts and sees the new state
        # (the old per-tool walks re-listed every time; a one-off failure must not
        # be baked in for the whole scan).
        later = self.root / "appears_later"
        self.assertEqual(get_subtree_index(self.root, later, _never_skip, "t"), {})
        (later / ".cursor").mkdir(parents=True)
        self.assertIn(".cursor", get_subtree_index(self.root, later, _never_skip, "t"))

    def test_unexpected_entry_error_does_not_abort_build(self):
        # A predicate that blows up on one entry must not stop the whole walk —
        # sibling subtrees are still indexed (matches the old walk's broad guard).
        self.mk("a", ".cursor")
        self.mk("b", ".windsurf")
        boom = str(self.root / "a")

        def skip(p: Path) -> bool:
            if str(p) == boom:
                raise RuntimeError("boom")
            return False

        idx = get_subtree_index(self.root, self.root, skip, "boom")
        self.assertIn(".windsurf", idx)

    def test_memoized_per_key(self):
        self.mk("p", ".cursor")
        a = get_subtree_index(self.root, self.root, _never_skip, "t")
        b = get_subtree_index(self.root, self.root, _never_skip, "t")
        self.assertIs(a, b)  # same cached object, no re-walk
        c = get_subtree_index(self.root, self.root, _never_skip, "other")
        self.assertIsNot(a, c)  # different skip policy -> separate index


class TestFailSafeDispatch(unittest.TestCase):
    """The shared index is an optimization, not a single point of failure: if it
    faults, one tool degrades to an independent walk instead of breaking discovery
    for every tool."""

    def setUp(self):
        clear_cache()
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        # A spread of matches including same-name nesting (must de-nest) and a
        # cross-name wrapper (must still be reached).
        for parts in [("a", ".cursor"), ("a", ".cursor", "deep", ".cursor"),
                      ("b", "src", ".cursor"), ("c", ".roo", "inner", ".cursor")]:
            self.root.joinpath(*parts).mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        clear_cache()
        self._tmp.cleanup()

    def _collect_via(self, force_index_error):
        got = []
        orig = pdi.get_subtree_index
        if force_index_error:
            pdi.get_subtree_index = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            dispatch_matches(self.root, self.root, _never_skip, "t",
                             lambda n: n == ".cursor", got.append)
        finally:
            pdi.get_subtree_index = orig
        # No sort: both routes walk the same os.scandir order, so comparing the
        # lists as-is pins dispatch order, not just the set of dirs found.
        return [str(p) for p in got]

    def test_fallback_dispatch_is_identical_to_index(self):
        # The independent fallback walk must discover the same dirs in the same
        # order the index does.
        via_index = self._collect_via(force_index_error=False)
        clear_cache()
        via_fallback = self._collect_via(force_index_error=True)
        self.assertEqual(via_index, via_fallback)
        self.assertTrue(via_index)  # and it actually found the planted dirs

    def test_index_fault_does_not_break_discovery(self):
        # Even when the shared index raises, matches are still dispatched.
        got = self._collect_via(force_index_error=True)
        self.assertIn(str(self.root / "a" / ".cursor"), got)
        # same-name nesting is still de-nested by the fallback (prune-at-match)
        self.assertNotIn(str(self.root / "a" / ".cursor" / "deep" / ".cursor"), got)
        # a .cursor nested under a differently-named dir is still reached
        self.assertIn(str(self.root / "c" / ".roo" / "inner" / ".cursor"), got)


class TestReviewHardening(unittest.TestCase):
    """Regressions for the security-review findings on the shared index."""

    def setUp(self):
        clear_cache()
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        clear_cache()
        self._tmp.cleanup()

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_symlinked_tool_dir_inside_root_is_dispatched(self):
        # A .cursor that is a symlink to a real dir INSIDE the scan root (stow,
        # chezmoi, a monorepo sharing one rules folder) must dispatch: stat follows
        # the link and containment passes. Only escaping links are refused (below).
        target = self.root / "shared" / ".cursor"
        target.mkdir(parents=True)
        link = self.root / "proj" / ".cursor"
        link.parent.mkdir(parents=True)
        os.symlink(target, link)
        index = {".cursor": [link]}
        got = []
        orig = pdi.get_subtree_index
        pdi.get_subtree_index = lambda *a, **k: index
        try:
            dispatch_matches(self.root, self.root, _never_skip, "t",
                             lambda n: n.lower() == ".cursor", got.append)
        finally:
            pdi.get_subtree_index = orig
        self.assertEqual([str(p) for p in got], [str(link)])

    def test_dispatch_skips_vanished_indexed_dir(self):
        # Finding 1: a dir removed after indexing is skipped, not dispatched.
        gone = self.root / "proj" / ".cursor"
        index = {".cursor": [gone]}  # never created on disk
        got = []
        orig = pdi.get_subtree_index
        pdi.get_subtree_index = lambda *a, **k: index
        try:
            dispatch_matches(self.root, self.root, _never_skip, "t",
                             lambda n: n.lower() == ".cursor", got.append)
        finally:
            pdi.get_subtree_index = orig
        self.assertEqual(got, [])

    def test_concurrent_get_subtree_index_is_safe(self):
        # Finding 3: parallel walks (Windows MCP ThreadPoolExecutor) must not race
        # the shared cache. Exercise the locked path from many threads.
        import threading
        self.root.joinpath("a", ".cursor").mkdir(parents=True)
        clear_cache()
        results, errors = [], []

        def worker():
            try:
                results.append(get_subtree_index(self.root, self.root, _never_skip, "t"))
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertTrue(all(set(r) == set(results[0]) for r in results))

    def test_mcp_walker_requires_and_forwards_skip_id(self):
        # skip_id is required (no default), so a caller with different prune rules
        # cannot silently share another tool's index; it is forwarded to dispatch.
        from coding_discovery_tools import mcp_extraction_helpers as mh
        seen = []
        orig = mh.dispatch_matches
        mh.dispatch_matches = lambda root, cur, prune, skip_id, m, om, **kw: seen.append(skip_id)
        try:
            mh.walk_for_mcp_configs_generic(
                self.root, self.root, [], ".cursor", "mcp.json", "Cursor", None,
                _never_skip, skip_id="cursor_ide")
            mh.walk_for_mcp_configs_generic(
                self.root, self.root, [], ".cursor", "mcp.json", "Cursor", None,
                _never_skip, skip_id="cursor_cli")
        finally:
            mh.dispatch_matches = orig
        self.assertEqual(seen, ["cursor_ide", "cursor_cli"])
        # Omitting it is a TypeError, not a silent fall back to a shared id.
        with self.assertRaises(TypeError):
            mh.walk_for_mcp_configs_generic(
                self.root, self.root, [], ".cursor", "mcp.json", "Cursor", None,
                _never_skip)

    def test_dispatch_allows_real_indexed_dir(self):
        # Finding B guard, positive side: a real directory (and, by the same
        # S_ISDIR stat, a Windows junction — stat follows to a real dir) still
        # dispatches; only vanished dirs and escaping links are dropped.
        real = self.root / "proj" / ".cursor"
        real.mkdir(parents=True)
        index = {".cursor": [real]}
        got = []
        orig = pdi.get_subtree_index
        pdi.get_subtree_index = lambda *a, **k: index
        try:
            dispatch_matches(self.root, self.root, _never_skip, "t",
                             lambda n: n.lower() == ".cursor", got.append)
        finally:
            pdi.get_subtree_index = orig
        self.assertEqual([str(p) for p in got], [str(real)])

    def test_mcp_walker_flags_non_hidden_marker(self):
        # Finding A: a non-hidden marker can't live in the hidden-only index, so
        # the MCP walker must pass markers_all_hidden=False to route to the direct
        # walk instead of silently matching nothing.
        from coding_discovery_tools import mcp_extraction_helpers as mh
        seen = {}
        orig = mh.dispatch_matches

        def capture(root, cur, prune, skip_id, m, om, markers_all_hidden=True):
            seen["hidden"] = markers_all_hidden

        mh.dispatch_matches = capture
        try:
            mh.walk_for_mcp_configs_generic(
                self.root, self.root, [], ".cursor", "mcp.json", "Cursor", None,
                _never_skip, skip_id="h")
            hidden_flag = seen["hidden"]
            mh.walk_for_mcp_configs_generic(
                self.root, self.root, [], "AGENTS", "mcp.json", "X", None,
                _never_skip, skip_id="nh")
        finally:
            mh.dispatch_matches = orig
        self.assertTrue(hidden_flag)        # ".cursor" -> index path
        self.assertFalse(seen["hidden"])    # "AGENTS"  -> direct walk

    def test_non_hidden_marker_found_via_direct_route(self):
        # Finding A end-to-end: with markers_all_hidden=False the non-hidden marker
        # dir is dispatched, where the hidden-only index alone would miss it.
        d = self.root / "proj" / "AGENTS"
        d.mkdir(parents=True)
        got = []
        dispatch_matches(self.root, self.root, _never_skip, "t",
                         lambda n: n == "AGENTS", got.append,
                         markers_all_hidden=False)
        self.assertEqual([str(p) for p in got], [str(d)])

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_dispatch_drops_leaf_when_ancestor_swapped_to_escaping_symlink(self):
        # HIGH: only the leaf is lstat'd, so an indexed ANCESTOR swapped for a
        # symlink pointing OUTSIDE the scan root must be caught by containment —
        # the leaf itself is a real dir (reached through the symlink), so the type
        # check passes and only the realpath containment stops it.
        scan = self.root / "scan"
        proj = scan / "proj"
        (proj / ".cursor").mkdir(parents=True)
        index = {".cursor": [proj / ".cursor"]}
        outside = self.root / "outside"
        (outside / ".cursor").mkdir(parents=True)
        import shutil
        shutil.rmtree(proj)
        os.symlink(outside, proj)  # ancestor now escapes the scan root
        got = []
        orig = pdi.get_subtree_index
        pdi.get_subtree_index = lambda *a, **k: index
        try:
            dispatch_matches(scan, scan, _never_skip, "t",
                             lambda n: n.lower() == ".cursor", got.append)
        finally:
            pdi.get_subtree_index = orig
        self.assertEqual(got, [])

    def test_rules_walkers_flag_non_hidden_marker(self):
        # TRIAGE: the macOS and Linux rules walkers must mirror the MCP walker and
        # route a non-hidden marker to the direct walk, not the hidden-only index.
        from coding_discovery_tools import macos_extraction_helpers as macos_h
        from coding_discovery_tools import linux_extraction_helpers as linux_h
        for mod in (macos_h, linux_h):
            seen = {}
            orig = mod.dispatch_matches

            def capture(root, cur, prune, skip_id, m, om, markers_all_hidden=True):
                seen["hidden"] = markers_all_hidden

            mod.dispatch_matches = capture
            try:
                mod.walk_for_tool_directories(
                    self.root, self.root, ".cursor", lambda d, p: None, {})
                hidden_flag = seen["hidden"]
                mod.walk_for_tool_directories(
                    self.root, self.root, "AGENTS", lambda d, p: None, {})
            finally:
                mod.dispatch_matches = orig
            self.assertTrue(hidden_flag, f"{mod.__name__}: hidden marker -> index")
            self.assertFalse(seen["hidden"], f"{mod.__name__}: non-hidden -> walk")

    @unittest.skipUnless(os.name == "posix", "filesystem-root '/' sweep is POSIX-only")
    def test_within_scan_root_handles_filesystem_root(self):
        # A scan rooted at "/" (macOS sweeps from filesystem root) must still
        # contain its descendants — a naive root_real + os.sep would be "//" and
        # reject everything, silently losing all discovery on that path.
        self.assertTrue(pdi._within_scan_root(Path("/Users/x/.cursor"), "/"))
        self.assertTrue(pdi._within_scan_root(Path("/"), "/"))
        # Real containment still holds: a sibling tree is not "under" the root.
        self.assertFalse(pdi._within_scan_root(Path("/nope/x"), "/home/alice"))

    def test_walk_direct_survives_iterator_fault(self):
        # An OSError raised while ADVANCING the scandir iterator (not on one entry)
        # must be caught so the tool's walk isn't aborted — entries seen before the
        # fault are still dispatched, matching the old walker's resilience.
        target = self.root / ".cursor"
        target.mkdir()

        class FakeEntry:
            path = str(target)
            name = ".cursor"

            def is_dir(self, follow_symlinks=True):
                return True

            def is_symlink(self):
                return False

        class FaultyScan:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def __iter__(self):
                yield FakeEntry()
                raise OSError("iterator faulted mid-walk")

        got = []
        orig = os.scandir
        os.scandir = lambda p: FaultyScan()
        try:
            pdi._walk_direct(self.root, self.root, lambda n: n == ".cursor",
                             got.append, _never_skip)
        finally:
            os.scandir = orig
        self.assertEqual([str(p) for p in got], [str(target)])

    def test_skip_system_dirs_frozen_and_prefixes_in_sync(self):
        # Finding 5: source is immutable, so the import-time derived prefix tuple
        # can't silently drift out of sync.
        from coding_discovery_tools.constants import SKIP_SYSTEM_DIRS
        from coding_discovery_tools import macos_extraction_helpers as macos_h
        from coding_discovery_tools import linux_extraction_helpers as linux_h
        self.assertIsInstance(SKIP_SYSTEM_DIRS, frozenset)
        self.assertEqual(set(macos_h._SKIP_SYSTEM_PREFIXES), set(SKIP_SYSTEM_DIRS))
        self.assertEqual(set(linux_h._LINUX_SKIP_SYSTEM_PREFIXES),
                         {d + "/" for d in linux_h._LINUX_SKIP_SYSTEM_DIRS})


def _staging_reference(root: Path, current: Path, is_match, should_skip) -> list:
    """Independent DFS walk mirroring the pre-index per-tool behaviour: dispatch a
    matching dir (never descend it), descend the rest, never descend a symlink,
    honour the depth cap, and only dispatch a real dir inside the scan root.
    Encounter (DFS) order — what the old per-tool walk produced."""
    root_real = os.path.realpath(str(root))
    out: list = []

    def walk(d: Path):
        try:
            entries = list(os.scandir(d))
        except OSError:
            return
        for e in entries:
            item = Path(e.path)
            if should_skip(item) or len(item.relative_to(root).parts) > MAX_SEARCH_DEPTH:
                continue
            if not e.is_dir():
                continue
            if is_match(e.name):
                if pdi._is_dispatchable(item) and pdi._within_scan_root(item, root_real):
                    out.append(item)
                continue
            if not e.is_symlink():
                walk(item)

    walk(Path(current))
    return out


class TestStagingParity(unittest.TestCase):
    """Diff BOTH new routes (the shared index and the direct fallback) against an
    independent DFS reference, over the cases the three review divergences came
    from. Running both implementations over the same tree and diffing is what
    would have caught symlink drop, fault caching, and the dispatch-order shift."""

    def setUp(self):
        clear_cache()
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self):
        clear_cache()
        self._tmp.cleanup()

    def _build(self, layout):
        for parts in layout:
            self.root.joinpath(*parts).mkdir(parents=True, exist_ok=True)

    def _assert_both_routes_match_reference(self, is_match, should_skip=_never_skip):
        ref = [str(p) for p in _staging_reference(self.root, self.root, is_match, should_skip)]
        clear_cache()
        idx_got: list = []
        dispatch_matches(self.root, self.root, should_skip, "parity", is_match, idx_got.append)
        fb_got: list = []
        pdi._walk_direct(self.root, self.root, is_match, fb_got.append, should_skip)
        self.assertEqual([str(p) for p in idx_got], ref, "index route diverged from the reference")
        self.assertEqual([str(p) for p in fb_got], ref, "fallback route diverged from the reference")

    def test_parity_nesting_siblings_and_cross_name(self):
        self._build([("a", ".cursor"), ("a", ".cursor", "deep", ".cursor"),
                     ("b", "src", ".cursor"), ("c", ".roo", "inner", ".cursor")])
        self._assert_both_routes_match_reference(lambda n: n == ".cursor")

    def test_parity_with_skip_prune(self):
        self._build([("keep", ".cursor"), ("node_modules", "pkg", ".cursor")])
        self._assert_both_routes_match_reference(
            lambda n: n == ".cursor", lambda p: "node_modules" in p.parts)

    def test_parity_depth_cap(self):
        chain = self.root
        for i in range(MAX_SEARCH_DEPTH + 1):
            chain = chain / f"d{i}"
        (chain / ".cursor").mkdir(parents=True, exist_ok=True)
        (self.root / "shallow" / ".cursor").mkdir(parents=True, exist_ok=True)
        self._assert_both_routes_match_reference(lambda n: n == ".cursor")

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_parity_symlinked_tool_dir_inside_root(self):
        # The stow case that blocked the PR: a symlinked .cursor resolving inside
        # the root must be dispatched by every route.
        (self.root / "shared" / ".cursor").mkdir(parents=True)
        (self.root / "proj").mkdir(parents=True)
        os.symlink(self.root / "shared" / ".cursor", self.root / "proj" / ".cursor")
        self._assert_both_routes_match_reference(lambda n: n == ".cursor")


if __name__ == "__main__":
    unittest.main()
