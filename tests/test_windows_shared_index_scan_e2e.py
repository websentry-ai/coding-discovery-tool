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


if __name__ == "__main__":
    unittest.main()
