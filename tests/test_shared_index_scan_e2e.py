"""End-to-end tests that drive the REAL production walk wrappers over real
on-disk fixtures — not ``dispatch_matches`` in isolation (that is covered by
``test_project_dir_index``). These exercise the wrappers the scan actually calls
(``walk_for_tool_directories`` for tool-config dirs, ``walk_for_cursor_mcp_configs``
for MCP configs), so the wiring the units bypass is covered: the real skip
predicates, the real ``skip_id``, the ``markers_all_hidden`` routing, and the
on-match extraction callback.

Regression anchors (proven by running the shipped walk across versions):
  - staging (pre-refactor per-tool walk) and this branch dispatch the SAME dirs,
    INCLUDING a symlinked tool dir resolving inside the scan root;
  - the intermediate shared-index commit that stat-ed with ``lstat`` DROPPED that
    symlinked dir — the symptom ``test_walk_finds_symlinked_tool_dir_inside_root``
    guards against.
"""

import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.linux_extraction_helpers import walk_for_tool_directories  # noqa: E402
from coding_discovery_tools.mcp_extraction_helpers import (  # noqa: E402
    walk_for_cursor_mcp_configs,
    walk_for_mcp_configs_generic,
)
from coding_discovery_tools import project_dir_index as pdi  # noqa: E402

import json  # noqa: E402


def _dispatched(root: Path, tool_dir: str):
    """Run the real Linux tool-dir walk and return dispatched dirs relative to
    root, sorted — the set of tool-config dirs the scan would extract from."""
    pdi.clear_cache()
    found = []
    walk_for_tool_directories(
        root, root, tool_dir,
        lambda d, _projects_by_root: found.append(os.path.relpath(str(d), str(root))),
        {},
    )
    return sorted(found)


class TestToolDirWalkE2E(unittest.TestCase):
    def setUp(self):
        pdi.clear_cache()
        self._tmp = tempfile.mkdtemp(prefix="scan-e2e-")
        self.root = Path(self._tmp).resolve()

    def tearDown(self):
        # Restore perms first so an unreadable-dir fixture can be removed.
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
        # Nesting (de-nest to outermost), a sibling, and a .cursor under a
        # differently-named dir (still reached). The deep nested .cursor is pruned.
        self._mk("a", ".cursor", "rules")
        self._mk("a", ".cursor", "deep", ".cursor")
        self._mk("b", "src", ".cursor")
        self._mk("c", ".roo", "inner", ".cursor")
        self.assertEqual(
            _dispatched(self.root, ".cursor"),
            ["a/.cursor", "b/src/.cursor", "c/.roo/inner/.cursor"],
        )

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_walk_finds_symlinked_tool_dir_inside_root(self):
        # A .cursor that is a symlink to a real dir INSIDE the scan root (stow,
        # chezmoi, a shared monorepo rules folder). Must be dispatched: this is
        # the exact case the intermediate lstat-based check dropped.
        self._mk("shared", ".cursor")
        self._mk("proj")
        os.symlink(self.root / "shared" / ".cursor", self.root / "proj" / ".cursor")
        got = _dispatched(self.root, ".cursor")
        self.assertIn("proj/.cursor", got, "symlinked tool dir inside root must be extracted")
        self.assertIn("shared/.cursor", got)

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_walk_refuses_symlink_escaping_the_scan_root(self):
        # A .cursor symlinked to a dir OUTSIDE the scan root must NOT be extracted:
        # a privileged scan must not read another tree through a planted link.
        outside = tempfile.mkdtemp(prefix="scan-e2e-outside-")
        try:
            (Path(outside) / ".cursor").mkdir()
            self._mk("proj")
            os.symlink(Path(outside) / ".cursor", self.root / "proj" / ".cursor")
            self._mk("real", ".cursor")  # a legit one, to prove the walk still ran
            got = _dispatched(self.root, ".cursor")
            self.assertIn("real/.cursor", got)
            self.assertNotIn("proj/.cursor", got, "escaping symlink must be refused")
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    @unittest.skipUnless(os.name == "nt", "junctions are a Windows reparse-point feature")
    def test_walk_finds_junctioned_tool_dir_inside_root(self):
        # The Windows analog of the stow symlink case: a .cursor that is a JUNCTION
        # (mklink /J) to a real dir inside the scan root must be dispatched. The
        # index treats it as a link (recorded, not descended) but still extracts it.
        import subprocess
        self._mk("shared", ".cursor")
        self._mk("proj")
        link = self.root / "proj" / ".cursor"
        target = self.root / "shared" / ".cursor"
        rc = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                            capture_output=True, text=True)
        if rc.returncode != 0:  # junction creation genuinely unavailable
            self.skipTest(f"mklink /J failed: {rc.stderr.strip()}")
        got = _dispatched(self.root, ".cursor")
        self.assertIn("proj\\.cursor", got, "junctioned tool dir inside root must be extracted")
        self.assertIn("shared\\.cursor", got)

    @unittest.skipUnless(os.name == "posix", "chmod 000 is POSIX-specific")
    def test_walk_survives_unreadable_subdir(self):
        # An unreadable subtree mid-walk must not hide sibling tool dirs, and must
        # not raise — the scan degrades gracefully as the old per-tool walk did.
        self._mk("readable", ".cursor")
        blocked = self._mk("locked", "sub")
        (blocked / ".cursor").mkdir()
        os.chmod(str(blocked), 0o000)
        try:
            got = _dispatched(self.root, ".cursor")
        finally:
            os.chmod(str(blocked), 0o755)
        self.assertIn("readable/.cursor", got, "a sibling fault must not hide readable tools")


class TestMcpWalkE2E(unittest.TestCase):
    def setUp(self):
        pdi.clear_cache()
        self._tmp = tempfile.mkdtemp(prefix="mcp-e2e-")
        self.root = Path(self._tmp).resolve()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)
        pdi.clear_cache()

    def test_cursor_mcp_walk_extracts_planted_config(self):
        # The real MCP walker must find a project's .cursor/mcp.json and surface
        # its server. Exercises walk_for_mcp_configs_generic with the real skip_id.
        proj = self.root / "work" / "repo1" / ".cursor"
        proj.mkdir(parents=True)
        (proj / "mcp.json").write_text(
            json.dumps({"mcpServers": {"fs": {"command": "npx", "args": ["-y", "server-fs"]}}}),
            encoding="utf-8",
        )
        projects = []
        walk_for_cursor_mcp_configs(
            self.root, self.root, projects,
            global_cursor_dir=self.root / "___no_global___",
            should_skip_func=lambda _p: False,
        )
        self.assertTrue(projects, "the planted .cursor/mcp.json must be discovered")
        blob = json.dumps(projects)
        self.assertIn("fs", blob, "the MCP server name must survive extraction")

    def test_mcp_generic_requires_skip_id(self):
        # skip_id is keyword-only and required: it keys the shared cache, so a
        # caller that forgets it must fail loudly, not silently share another
        # tool's index. This is an intentional, guarded signature change.
        with self.assertRaises(TypeError):
            walk_for_mcp_configs_generic(
                self.root, self.root, [], ".cursor", ["mcp.json"],
                "Cursor", None, lambda _p: False, 0,
            )


if __name__ == "__main__":
    unittest.main()
