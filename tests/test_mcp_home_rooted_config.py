"""
Regression tests for home-rooted project-scope ``.mcp.json`` detection.

Bug: when a Claude Code project's root *is* the user's home directory, its
project-scope config lives at ``~/.mcp.json`` (e.g. ``C:\\Users\\thiago\\.mcp.json``).
The project-scope walk's file branch previously called
``is_home_dotdir_descendant(entry)`` on the file itself; because the leaf
``.mcp.json`` is a dotted segment directly under ``/Users/<u>`` (or ``/home/<u>``),
the guard — meant only to skip the *contents of* hidden tool dirs like
``~/.cursor/`` — misclassified the file as a hidden tool dir and skipped it.
The server(s) in that file were therefore never reported.

Fix: the file branch now tests ``entry.parent``. A home-rooted ``.mcp.json`` is
read, while a ``.mcp.json`` that genuinely lives inside a hidden home tool dir
(``~/.cursor/.mcp.json``) is still skipped. The directory-recursion branch is
unchanged, so the walk still never descends into ``~/.cursor`` etc.

The home-rooted case is verified at the predicate level because the guard keys
on absolute path layout (``parts[1] in ("Users", "home")``); a CI temp dir is
never under a real ``/Users/<user>`` or ``/home/<user>`` root, so only
constructed ``Path`` objects can exercise it deterministically. A temp-dir walk
smoke test additionally confirms the file branch still surfaces normal
project ``.mcp.json`` files after the change.
"""

import json
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest import mock

import scripts.coding_discovery_tools.mcp_extraction_helpers as helpers
from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    is_home_dotdir_descendant,
    walk_for_claude_project_mcp_configs,
)
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


def _write_mcp_json(directory: Path, server_name: str = "test-server") -> Path:
    """Create a minimal .mcp.json file in the given directory."""
    mcp_file = directory / ".mcp.json"
    mcp_file.write_text(json.dumps({
        "mcpServers": {
            server_name: {"command": "echo", "args": ["hello"]}
        }
    }))
    return mcp_file


class TestHomeRootedMcpFileBranch(unittest.TestCase):
    """File branch decides via ``is_home_dotdir_descendant(entry.parent)``."""

    # --- the fix: a home-rooted leaf .mcp.json must be read ---

    def test_home_rooted_mcp_json_is_not_skipped_posix(self):
        for entry in (Path("/Users/alice/.mcp.json"),     # macOS home root
                      Path("/Users/thiago/.mcp.json"),
                      Path("/home/bob/.mcp.json")):        # Linux home root
            self.assertFalse(
                is_home_dotdir_descendant(entry.parent),
                f"{entry} (home-rooted project config) should be read",
            )

    def test_home_rooted_mcp_json_windows_drive(self):
        # Exercise real Windows drive-anchored parsing (thiago's actual case).
        read_case = PureWindowsPath(r"C:\Users\thiago\.mcp.json")
        self.assertFalse(is_home_dotdir_descendant(read_case.parent))
        # ...while a config inside a hidden home tool dir stays skipped.
        skip_case = PureWindowsPath(r"C:\Users\thiago\.cursor\.mcp.json")
        self.assertTrue(is_home_dotdir_descendant(skip_case.parent))

    # --- still correct: configs inside a hidden home tool dir stay skipped ---

    def test_mcp_json_inside_hidden_home_dir_is_skipped(self):
        entry = Path("/Users/alice/.cursor/.mcp.json")
        self.assertTrue(is_home_dotdir_descendant(entry.parent))

    def test_mcp_json_inside_hidden_home_dir_is_skipped_linux(self):
        entry = Path("/home/bob/.config/.mcp.json")
        self.assertTrue(is_home_dotdir_descendant(entry.parent))

    # --- no regression: normal sub-folder projects keep working ---

    def test_subfolder_project_mcp_json_is_not_skipped(self):
        entry = Path("/Users/alice/myproj/.mcp.json")
        self.assertFalse(is_home_dotdir_descendant(entry.parent))

    def test_deeply_nested_project_mcp_json_is_not_skipped(self):
        entry = Path("/Users/alice/work/myproj/.mcp.json")
        self.assertFalse(is_home_dotdir_descendant(entry.parent))

    # --- documents the bug: the OLD leaf-based check wrongly skipped it ---

    def test_old_leaf_check_wrongly_flagged_home_rooted_config(self):
        entry = Path("/Users/alice/.mcp.json")
        # Pre-fix: passing the file itself mis-flags it as a hidden tool dir.
        self.assertTrue(is_home_dotdir_descendant(entry))
        # Post-fix: passing the parent dir returns False -> file is read.
        self.assertFalse(is_home_dotdir_descendant(entry.parent))


class TestDirBranchUnchanged(unittest.TestCase):
    """The directory-recursion branch still tests the entry itself, so the walk
    never descends into hidden home tool dirs."""

    def test_hidden_home_dir_still_skipped(self):
        self.assertTrue(is_home_dotdir_descendant(Path("/Users/alice/.cursor")))
        self.assertTrue(is_home_dotdir_descendant(Path("/home/bob/.codex")))

    def test_normal_project_dir_not_skipped(self):
        self.assertFalse(is_home_dotdir_descendant(Path("/Users/alice/myproj")))


class TestClaudeWalkFileBranchSmoke(unittest.TestCase):
    """End-to-end smoke test of the walk's file branch over a real temp tree:
    a normal project ``.mcp.json`` is still surfaced after the change."""

    def setUp(self):
        # Don't spawn real MCP servers while scanning during extraction.
        self._orig_scan = helpers._scan_servers_in_mapping
        helpers._scan_servers_in_mapping = lambda mapping: {}

    def tearDown(self):
        helpers._scan_servers_in_mapping = self._orig_scan

    def test_walk_surfaces_subfolder_mcp_json(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj = root / "myproj"
            proj.mkdir()
            _write_mcp_json(proj, server_name="policycenter")

            found = []
            walk_for_claude_project_mcp_configs(
                root_path=root,
                current_dir=root,
                projects=found,
                should_skip_func=lambda p: False,
                current_depth=0,
            )

            paths = {p["path"] for p in found}
            self.assertIn(str(proj), paths)
            servers = [s["name"] for p in found for s in p["mcpServers"]]
            self.assertIn("policycenter", servers)

    def test_walk_file_branch_tests_parent_not_the_file(self):
        """Lock the fix at the walk level: the file branch must consult
        is_home_dotdir_descendant on the .mcp.json's PARENT dir, never on the
        file itself. The pre-fix bug passed the file, which skipped home-rooted
        configs; this test fails if anyone reverts the call to `entry`."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            proj = root / "proj"
            proj.mkdir()
            mcp_file = _write_mcp_json(proj, server_name="policycenter")

            with mock.patch.object(
                helpers, "is_home_dotdir_descendant", return_value=False
            ) as guard:
                found = []
                helpers.walk_for_claude_project_mcp_configs(
                    root_path=root,
                    current_dir=root,
                    projects=found,
                    should_skip_func=lambda p: False,
                    current_depth=0,
                )

            checked = [c.args[0] for c in guard.call_args_list if c.args]
            # File branch must test the parent dir, NOT the .mcp.json file.
            self.assertNotIn(
                mcp_file, checked,
                "file branch must call is_home_dotdir_descendant(entry.parent), "
                "not on the .mcp.json file itself",
            )
            self.assertIn(proj, checked)
            # ...and the server is still surfaced.
            servers = [s["name"] for p in found for s in p["mcpServers"]]
            self.assertIn("policycenter", servers)


class TestUnionMcpServers(unittest.TestCase):
    """The merge helper combines server lists by name instead of overwriting, so
    two config sources resolving to the same project path (e.g. a home-rooted
    ~/.mcp.json and ~/.claude.json projects[<home>]) don't clobber each other."""

    def test_disjoint_lists_are_combined(self):
        out = AIToolsDetector._union_mcp_servers([{"name": "alpha"}], [{"name": "beta"}])
        self.assertEqual([s["name"] for s in out], ["alpha", "beta"])

    def test_same_name_deduped_first_wins(self):
        existing = [{"name": "playwright", "command": "from-claude-json"}]
        incoming = [{"name": "playwright", "command": "from-mcp-json"},
                    {"name": "policycenter"}]
        out = AIToolsDetector._union_mcp_servers(existing, incoming)
        self.assertEqual([s["name"] for s in out], ["playwright", "policycenter"])
        # higher-precedence (earlier-merged) definition is preserved
        self.assertEqual(out[0]["command"], "from-claude-json")

    def test_handles_empty_and_none(self):
        self.assertEqual(AIToolsDetector._union_mcp_servers([], []), [])
        self.assertEqual(AIToolsDetector._union_mcp_servers(None, None), [])
        self.assertEqual(
            [s["name"] for s in AIToolsDetector._union_mcp_servers(None, [{"name": "x"}])],
            ["x"],
        )


class TestClaudeMergeHomeCollisionUnion(unittest.TestCase):
    """Regression for the collision the home-rooted fix can expose: when both
    ~/.claude.json projects[<home>] and a home-rooted ~/.mcp.json resolve to the
    same project path, both sources' servers must survive the merge (previously
    the later source silently overwrote the earlier one)."""

    @staticmethod
    def _detector():
        # Bypass heavy __init__; the merge methods use no instance state.
        return object.__new__(AIToolsDetector)

    def test_two_sources_same_path_union_not_overwrite(self):
        det = self._detector()
        home = "/Users/thiago"  # same .parts shape as C:\Users\thiago
        projects_dict = {}
        # Source order mirrors extract_mcp_config: ~/.claude.json first, the
        # project-scope .mcp.json walk last.
        mcp_projects = [
            {"path": home, "mcpServers": [{"name": "serverA"}], "scope": "project"},
            {"path": home, "mcpServers": [{"name": "policycenter"},
                                          {"name": "playwright"}], "scope": "project"},
        ]
        det._merge_claude_mcp_configs_into_projects(mcp_projects, projects_dict)
        names = {s["name"] for s in projects_dict[home]["mcpServers"]}
        self.assertEqual(names, {"serverA", "policycenter", "playwright"})

    def test_single_source_unchanged(self):
        det = self._detector()
        projects_dict = {}
        det._merge_claude_mcp_configs_into_projects(
            [{"path": "/Users/x/proj", "mcpServers": [{"name": "only"}]}], projects_dict
        )
        self.assertEqual(
            [s["name"] for s in projects_dict["/Users/x/proj"]["mcpServers"]], ["only"]
        )

    def test_default_merge_also_unions(self):
        det = self._detector()
        home = "/Users/thiago"
        projects_dict = {}
        det._merge_mcp_configs_into_projects(
            [{"path": home, "mcpServers": [{"name": "a"}]},
             {"path": home, "mcpServers": [{"name": "b"}]}],
            projects_dict,
        )
        self.assertEqual(
            {s["name"] for s in projects_dict[home]["mcpServers"]}, {"a", "b"}
        )


class TestCopilotCliSharesWalk(unittest.TestCase):
    """H2: Copilot CLI's Workspace .mcp.json uses the same project-scope walk,
    so the home-rooted fix (and a home-rooted ~/.mcp.json) applies to it too."""

    def test_copilot_cli_uses_shared_claude_walk(self):
        import importlib
        try:
            mod = importlib.import_module(
                "scripts.coding_discovery_tools.macos.copilot_cli.mcp_config_extractor"
            )
        except Exception as exc:  # platform-specific import shouldn't fail the suite
            self.skipTest(f"copilot_cli extractor not importable here: {exc}")
        self.assertTrue(
            hasattr(mod, "walk_for_claude_project_mcp_configs"),
            "Copilot CLI workspace scan must reuse walk_for_claude_project_mcp_configs",
        )


if __name__ == "__main__":
    unittest.main()
