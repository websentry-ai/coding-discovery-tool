"""
Integration tests for the phantom marketplace MCP fix.

Verifies that the project-scope walker skips .claude/plugins/ directories,
preventing plugin catalog entries from being reported as project-scope MCP
servers.  Tests exercise the full should_skip -> walk_for_claude_project_mcp_configs
pipeline with real temp directories.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    is_claude_plugins_path,
    walk_for_claude_project_mcp_configs,
)


def _write_mcp_json(directory: Path) -> Path:
    """Create a minimal .mcp.json file in the given directory."""
    mcp_file = directory / ".mcp.json"
    mcp_file.write_text(json.dumps({
        "mcpServers": {
            "test-server": {
                "command": "echo",
                "args": ["hello"]
            }
        }
    }))
    return mcp_file


class TestIsClaudePluginsPath(unittest.TestCase):
    """Test is_claude_plugins_path with various path shapes."""

    def test_rejects_path_inside_claude_plugins(self):
        path = Path("/Users/alice/.claude/plugins/marketplace/cache/some-plugin")
        self.assertTrue(is_claude_plugins_path(path))

    def test_rejects_path_at_plugins_level(self):
        path = Path("/Users/alice/.claude/plugins")
        self.assertTrue(is_claude_plugins_path(path))

    def test_rejects_windows_style_path(self):
        path = Path("C:/Users/alice/.claude/plugins/cache/plugin-x")
        self.assertTrue(is_claude_plugins_path(path))

    def test_allows_regular_project_path(self):
        path = Path("/Users/alice/projects/my-app")
        self.assertFalse(is_claude_plugins_path(path))

    def test_allows_claude_dir_without_plugins(self):
        path = Path("/Users/alice/.claude/mcp.json")
        self.assertFalse(is_claude_plugins_path(path))

    def test_allows_unrelated_plugins_directory(self):
        path = Path("/Users/alice/projects/plugins/something")
        self.assertFalse(is_claude_plugins_path(path))

    def test_allows_dot_claude_not_followed_by_plugins(self):
        path = Path("/Users/alice/.claude/settings/config.json")
        self.assertFalse(is_claude_plugins_path(path))

    def test_rejects_nested_user_claude_plugins(self):
        path = Path("/home/bob/.claude/plugins/cache/.mcp.json")
        self.assertTrue(is_claude_plugins_path(path))


class TestProjectScopeWalkerSkipsPlugins(unittest.TestCase):
    """Integration: walk_for_claude_project_mcp_configs skips .claude/plugins/."""

    def setUp(self):
        self.tmpdir_obj = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self.tmpdir_obj.name)

    def tearDown(self):
        self.tmpdir_obj.cleanup()

    def _build_should_skip_with_plugins_guard(self):
        """Return a should_skip closure that only checks is_claude_plugins_path.

        The real closures also call should_skip_path / should_skip_system_path
        but those are irrelevant to the plugin-skip behaviour under test.
        """
        def should_skip(item: Path) -> bool:
            return is_claude_plugins_path(item)
        return should_skip

    def test_walker_finds_legitimate_project_mcp(self):
        project_dir = self.tmpdir / "my-project"
        project_dir.mkdir(parents=True)
        _write_mcp_json(project_dir)

        projects = []
        walk_for_claude_project_mcp_configs(
            self.tmpdir, self.tmpdir, projects,
            self._build_should_skip_with_plugins_guard(),
            current_depth=0,
        )
        self.assertGreater(len(projects), 0, "Should find .mcp.json in a normal project")

    def test_walker_skips_mcp_inside_claude_plugins(self):
        plugins_dir = self.tmpdir / ".claude" / "plugins" / "marketplace" / "some-plugin"
        plugins_dir.mkdir(parents=True)
        _write_mcp_json(plugins_dir)

        projects = []
        walk_for_claude_project_mcp_configs(
            self.tmpdir, self.tmpdir, projects,
            self._build_should_skip_with_plugins_guard(),
            current_depth=0,
        )
        self.assertEqual(len(projects), 0, "Should NOT find .mcp.json inside .claude/plugins/")

    def test_walker_skips_plugins_but_finds_sibling_project(self):
        plugins_dir = self.tmpdir / ".claude" / "plugins" / "cache" / "plugin-x"
        plugins_dir.mkdir(parents=True)
        _write_mcp_json(plugins_dir)

        legitimate_dir = self.tmpdir / "real-project"
        legitimate_dir.mkdir(parents=True)
        _write_mcp_json(legitimate_dir)

        projects = []
        walk_for_claude_project_mcp_configs(
            self.tmpdir, self.tmpdir, projects,
            self._build_should_skip_with_plugins_guard(),
            current_depth=0,
        )
        self.assertEqual(len(projects), 1, "Should find only the legitimate project, not the plugin")

        for project in projects:
            project_path = project.get("project_root", project.get("config_path", ""))
            self.assertNotIn("plugins", project_path)

    def test_walker_skips_mcp_json_directly_in_plugins_dir(self):
        plugins_dir = self.tmpdir / ".claude" / "plugins"
        plugins_dir.mkdir(parents=True)
        _write_mcp_json(plugins_dir)

        projects = []
        walk_for_claude_project_mcp_configs(
            self.tmpdir, self.tmpdir, projects,
            self._build_should_skip_with_plugins_guard(),
            current_depth=0,
        )
        self.assertEqual(len(projects), 0, "Should NOT find .mcp.json directly in .claude/plugins/")


if __name__ == "__main__":
    unittest.main()
