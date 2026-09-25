"""End-to-end tests for the WINDOWS shared-directory-index walk.

The Windows per-tool extractors used to each re-walk the whole ``C:\\`` drive with
a bespoke ``_walk_for_*`` recursion. They now route through the single-pass shared
directory index via ``windows_extraction_helpers.walk_for_tool_directories`` — the
same index Linux/macOS already use through their own ``walk_for_tool_directories``.

These tests drive the REAL production Windows wrapper over real on-disk fixtures
(not ``dispatch_matches`` in isolation — that is covered by
``test_project_dir_index``), and they prove that every migrated Windows extractor
actually dispatches through the shared index rather than re-walking the drive.

The fixtures are planted under ``$HOME`` so the walk's real prune (Windows system
directory names) does not skip them, and so the tree is walkable on POSIX CI too
(the Windows prune never matches a POSIX temp path, so the walk behaves the same).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools import windows_extraction_helpers as weh  # noqa: E402
from coding_discovery_tools.windows_extraction_helpers import (  # noqa: E402
    walk_for_tool_directories,
    _WINDOWS_PROJECT_SKIP_ID,
)
from coding_discovery_tools import project_dir_index as pdi  # noqa: E402
from coding_discovery_tools.constants import MAX_SEARCH_DEPTH  # noqa: E402


def _win_dispatched(root: Path, markers):
    """Run the real Windows tool-dir walk and return dispatched dirs relative to
    root, sorted — the set of tool-config dirs the scan would extract from."""
    pdi.clear_cache()
    found = []

    def on_match(tool_dir, _acc):
        found.append(os.path.relpath(str(tool_dir), str(root)).replace(os.sep, "/"))

    walk_for_tool_directories(root, root, markers, on_match, {})
    return sorted(found)


class TestWindowsToolDirWalkE2E(unittest.TestCase):
    def setUp(self):
        pdi.clear_cache()
        self._tmp = tempfile.mkdtemp(prefix="win-scan-e2e-", dir=str(Path.home()))
        self.root = Path(self._tmp).resolve()

    def tearDown(self):
        for p in self.root.rglob("*"):
            try:
                p.chmod(0o755)
            except OSError:
                pass
        shutil.rmtree(self._tmp, ignore_errors=True)
        pdi.clear_cache()

    def _mk(self, *parts):
        d = self.root.joinpath(*parts)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def test_walk_dispatches_planted_tool_dirs_denested(self):
        # Nesting (de-nest to outermost), a sibling, and a marker under a
        # differently-named dir (still reached). The deep nested one is pruned.
        self._mk("a", ".clinerules", "sub")
        self._mk("a", ".clinerules", "deep", ".clinerules")
        self._mk("b", "src", ".clinerules")
        self._mk("c", ".roo", "inner", ".clinerules")
        self.assertEqual(
            _win_dispatched(self.root, ".clinerules"),
            ["a/.clinerules", "b/src/.clinerules", "c/.roo/inner/.clinerules"],
        )

    def test_walk_matches_multiple_markers_in_one_pass(self):
        # A skills-style call passes an iterable of hidden marker dirs; every one
        # must be dispatched from the SAME single walk.
        self._mk("proj1", ".cursor")
        self._mk("proj2", ".agents")
        self._mk("proj3", ".claude")
        self._mk("proj4", "nomarker")
        self.assertEqual(
            _win_dispatched(self.root, (".cursor", ".agents", ".claude")),
            ["proj1/.cursor", "proj2/.agents", "proj3/.claude"],
        )

    def test_second_tool_reuses_the_memoized_index(self):
        # The whole point of the migration: the C:\ subtree is indexed ONCE and
        # every subsequent per-tool walk reads that cached map. Prove the index is
        # populated for the Windows skip id after the first walk and that a second
        # marker resolves against the same cached tree.
        self._mk("repo", ".clinerules")
        self._mk("repo", ".cursor")
        first = _win_dispatched(self.root, ".clinerules")
        # Do NOT clear the cache between the two walks (real scans share it).
        second_found = []
        walk_for_tool_directories(
            self.root, self.root, ".cursor",
            lambda d, _a: second_found.append(d.name), {},
        )
        self.assertEqual(first, ["repo/.clinerules"])
        self.assertEqual(second_found, [".cursor"])
        # The index cache holds an entry keyed by the shared Windows skip id.
        self.assertTrue(
            any(k[0] == _WINDOWS_PROJECT_SKIP_ID for k in pdi._INDEX_CACHE),
            "the shared index must be memoized under the Windows skip id",
        )

    @unittest.skipUnless(os.name == "posix", "chmod 000 is POSIX-specific")
    def test_walk_survives_unreadable_subdir(self):
        # An unreadable subtree mid-walk must not hide sibling tool dirs and must
        # not raise — the scan degrades exactly as the old per-tool walk did.
        self._mk("readable", ".clinerules")
        blocked = self._mk("locked", "sub")
        (blocked / ".clinerules").mkdir()
        os.chmod(str(blocked), 0o000)
        try:
            got = _win_dispatched(self.root, ".clinerules")
        finally:
            os.chmod(str(blocked), 0o755)
        self.assertIn("readable/.clinerules", got,
                      "a sibling fault must not hide readable tools")

    def test_shared_helper_does_not_prune_other_tool_config_dirs(self):
        # DELIBERATE limitation, and the exact reason 8 skills extractors were NOT
        # migrated: the shared helper prunes ONLY system dirs. A marker bundled
        # inside another tool's per-user config dir (e.g. ~/.antigravity/...) IS
        # dispatched -- a false positive for a skills walk that must apply
        # traverses_other_tool_config_dir. Guard-free extractors are unaffected;
        # guarded ones keep their bespoke walk (see TestGuardedSkillsExtractors...).
        self._mk(".antigravity", "extensions", "pkg", ".claude")
        self._mk("myproject", ".claude")
        got = _win_dispatched(self.root, ".claude")
        self.assertIn("myproject/.claude", got)
        self.assertIn(
            ".antigravity/extensions/pkg/.claude", got,
            "shared helper does not prune other-tool config dirs; guarded skills "
            "extractors must not use it as-is",
        )

    def test_marker_beyond_max_depth_is_not_dispatched(self):
        # The depth limit must match the old bespoke walk (MAX_SEARCH_DEPTH).
        deep = self.root
        for i in range(MAX_SEARCH_DEPTH + 2):
            deep = deep / f"d{i}"
        (deep / ".clinerules").mkdir(parents=True)
        self._mk("shallow", ".clinerules")
        got = _win_dispatched(self.root, ".clinerules")
        self.assertEqual(got, ["shallow/.clinerules"],
                         "a marker past MAX_SEARCH_DEPTH must not be dispatched")

    @unittest.skipUnless(os.name == "posix", "symlink creation is POSIX here")
    def test_symlinked_marker_dir_is_not_descended(self):
        # The index never descends a symlink (matches the old walk's is_symlink
        # skip). A marker reachable ONLY through a symlink is not dispatched.
        self._mk("real", "hidden_target", ".clinerules")
        os.symlink(str(self.root / "real" / "hidden_target"), str(self.root / "link"))
        got = _win_dispatched(self.root, ".clinerules")
        self.assertIn("real/hidden_target/.clinerules", got)
        self.assertFalse(
            any(g.startswith("link/") for g in got),
            "a marker reached only via a symlink must not be dispatched",
        )

    def test_matches_old_bespoke_walk_semantics(self):
        # Backward-compat oracle for the guard-free extractors: the old bespoke
        # walk = recurse, skip system dirs, depth-limit, dispatch the OUTERMOST
        # marker, never descend a matched dir or a symlink. The shared helper must
        # produce exactly that set. (Guarded extractors keep their own walk and
        # are covered separately.)
        self._mk("p1", ".clinerules")
        self._mk("p1", "sub", ".clinerules")            # nested under a match -> pruned
        self._mk("p2", "src", "nested", ".clinerules")
        self._mk("p3")                                   # no marker
        self.assertEqual(
            _win_dispatched(self.root, ".clinerules"),
            self._old_walk_oracle(".clinerules"),
        )

    def _old_walk_oracle(self, marker):
        """The guard-free bespoke walk's semantics, as a backward-compat oracle."""
        from coding_discovery_tools.windows_extraction_helpers import (
            should_skip_path, get_windows_system_directories,
        )
        sysdirs = get_windows_system_directories()
        found = []

        def rec(cur, depth):
            if depth > MAX_SEARCH_DEPTH:
                return
            try:
                entries = list(os.scandir(cur))
            except OSError:
                return
            for e in entries:
                p = Path(e.path)
                if should_skip_path(p, sysdirs):
                    continue
                try:
                    if len(p.relative_to(self.root).parts) > MAX_SEARCH_DEPTH:
                        continue
                except ValueError:
                    continue
                if not e.is_dir():
                    continue
                if e.name == marker:
                    found.append(os.path.relpath(str(p), str(self.root)).replace(os.sep, "/"))
                    continue  # outermost: never descend a matched dir
                if not e.is_symlink():
                    rec(p, depth + 1)

        rec(self.root, 0)
        return sorted(found)


# Each migrated extractor, its module path, the class, and the marker(s) its
# project-level walk now dispatches through the shared index. The tuple is what a
# migrated extractor's ``_extract_project_level_rules(root, acc)`` must route.
_MIGRATED = [
    ("coding_discovery_tools.windows.cline.cline_rules_extractor",
     "WindowsClineRulesExtractor", (".clinerules",)),
    ("coding_discovery_tools.windows.antigravity.antigravity_rules_extractor",
     "WindowsAntigravityRulesExtractor", (".agent",)),
    ("coding_discovery_tools.windows.kilocode.kilocode_rules_extractor",
     "WindowsKiloCodeRulesExtractor", (".kilocode",)),
    ("coding_discovery_tools.windows.opencode.opencode_rules_extractor",
     "WindowsOpenCodeRulesExtractor", (".opencode",)),
    ("coding_discovery_tools.windows.windsurf.windsurf_rules_extractor",
     "WindowsWindsurfRulesExtractor", (".windsurf",)),
    ("coding_discovery_tools.windows.cursor_cli.cursor_cli_rules_extractor",
     "WindowsCursorCliRulesExtractor", (".cursor",)),
    ("coding_discovery_tools.windows.roo_code.roo_code_rules_extractor",
     "WindowsRooRulesExtractor", (".roo",)),
    ("coding_discovery_tools.windows.junie.junie_rules_extractor",
     "WindowsJunieRulesExtractor", (".junie",)),
    ("coding_discovery_tools.windows.cursor.cursor_rules_extractor",
     "WindowsCursorRulesExtractor", (".cursor",)),
]


class TestMigratedExtractorsRouteThroughSharedIndex(unittest.TestCase):
    """Every migrated Windows extractor must dispatch its project-level walk
    through the shared index (``dispatch_matches`` with the Windows skip id),
    not a bespoke re-walk of the drive."""

    def setUp(self):
        pdi.clear_cache()
        self._tmp = tempfile.mkdtemp(prefix="win-route-e2e-", dir=str(Path.home()))
        self.root = Path(self._tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        pdi.clear_cache()

    def _import(self, module_path, class_name):
        import importlib
        mod = importlib.import_module(module_path)
        return getattr(mod, class_name)

    def test_each_extractor_dispatches_via_shared_windows_index(self):
        for module_path, class_name, markers in _MIGRATED:
            with self.subTest(extractor=class_name):
                pdi.clear_cache()
                # Plant a project carrying this tool's outermost marker dir.
                marker = markers[0]
                (self.root / "proj" / marker).mkdir(parents=True, exist_ok=True)

                seen_skip_ids = []
                # walk_for_tool_directories calls the name bound INSIDE the helper
                # module, so patch it there, not on project_dir_index.
                real_dispatch = weh.dispatch_matches

                def spy(root_path, current_dir, should_skip, skip_id,
                        is_match, on_match, markers_all_hidden=True, _real=real_dispatch):
                    seen_skip_ids.append(skip_id)
                    return _real(root_path, current_dir, should_skip, skip_id,
                                 is_match, on_match, markers_all_hidden=markers_all_hidden)

                cls = self._import(module_path, class_name)
                extractor = cls()

                import unittest.mock as mock
                with mock.patch.object(weh, "dispatch_matches", spy):
                    extractor._extract_project_level_rules(self.root, {})

                self.assertIn(
                    _WINDOWS_PROJECT_SKIP_ID, seen_skip_ids,
                    f"{class_name} must dispatch through the shared Windows index",
                )


class TestGuardedSkillsExtractorsStayUnmigrated(unittest.TestCase):
    """The skills extractors whose bespoke walk applies extra prune guards
    (``traverses_other_tool_config_dir`` / ``is_symlink_or_junction``) must NOT be
    moved onto the generic shared helper: it does not replicate those guards, so
    migrating them would report other tools' bundled config dirs as the user's own
    (proven in ``test_shared_helper_does_not_prune_other_tool_config_dirs``). This
    locks that decision so a future refactor cannot silently reintroduce the bug."""

    GUARDED = [
        "codex/skills_extractor.py",
        "gemini_cli/skills_extractor.py",
        "junie/skills_extractor.py",
        "kilocode/skills_extractor.py",
        "opencode/skills_extractor.py",
        "replit/skills_extractor.py",
        "windsurf/skills_extractor.py",
        "copilot_cli/copilot_cli_skills_extractor.py",
    ]

    def test_guarded_skills_keep_their_prune_and_avoid_shared_helper(self):
        base = (Path(__file__).resolve().parent.parent
                / "scripts" / "coding_discovery_tools" / "windows")
        for rel in self.GUARDED:
            src = (base / rel).read_text()
            with self.subTest(extractor=rel):
                self.assertIn("traverses_other_tool_config_dir", src,
                              f"{rel} must keep its other-tool prune")
                self.assertNotIn("walk_for_tool_directories", src,
                                 f"{rel} must NOT use the guard-free shared helper")


class TestClineSkillsRoutesThroughSharedIndex(unittest.TestCase):
    """cline skills IS migrated (it had no extra prune guard). Prove its
    project-level walk dispatches through the shared Windows index."""

    def setUp(self):
        pdi.clear_cache()
        self._tmp = tempfile.mkdtemp(prefix="win-cline-skills-", dir=str(Path.home()))
        self.root = Path(self._tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        pdi.clear_cache()

    def test_cline_skills_dispatches_via_shared_windows_index(self):
        import importlib
        import unittest.mock as mock
        (self.root / "proj" / ".clinerules" / "skills").mkdir(parents=True)
        mod = importlib.import_module(
            "coding_discovery_tools.windows.cline.skills_extractor")
        extractor = mod.WindowsClineSkillsExtractor()
        seen = []
        real = weh.dispatch_matches

        def spy(root_path, current_dir, should_skip, skip_id, is_match, on_match,
                markers_all_hidden=True, _real=real):
            seen.append(skip_id)
            return _real(root_path, current_dir, should_skip, skip_id, is_match,
                         on_match, markers_all_hidden=markers_all_hidden)

        with mock.patch.object(weh, "dispatch_matches", spy):
            extractor._extract_project_level_skills(self.root, {})
        self.assertIn(_WINDOWS_PROJECT_SKIP_ID, seen,
                      "cline skills must dispatch through the shared Windows index")


if __name__ == "__main__":
    unittest.main()
