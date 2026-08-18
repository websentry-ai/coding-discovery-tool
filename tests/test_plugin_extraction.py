"""
Integration tests for plugin provenance detection.

Tests cover Claude Code and Cursor plugin extraction from real filesystem
fixtures created in temp directories, including blocklist handling,
marketplace classification, capability detection, and skill tagging.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.plugin_extraction_helpers import (
    extract_claude_code_plugins,
    extract_cursor_plugins,
    build_plugin_install_path_lookup,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    extract_item_info,
    SKILL_CONFIG,
    _find_plugin_provenance,
)


def _write_json(path: Path, data: dict) -> None:
    """Write a dict as JSON to the given path, creating parent dirs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _create_claude_code_fixture(base_dir: Path) -> Path:
    """
    Create a Claude Code plugins directory with 4 plugins, 1 blocked.

    Returns the plugins_dir path.
    """
    plugins_dir = base_dir / ".claude" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)

    # installed_plugins.json with 4 plugins
    installed = {
        "version": 2,
        "plugins": {
            "slack@claude-plugins-official": [{
                "scope": "user",
                "installPath": str(plugins_dir / "cache" / "claude-plugins-official" / "slack" / "1.0.0"),
                "version": "1.0.0",
                "installedAt": "2026-03-14T16:29:37.836Z",
                "lastUpdated": "2026-05-09T23:22:16.521Z",
                "gitCommitSha": "b97f6eadd92917d0f8266309b74396b5a3c4f857",
            }],
            "github@claude-plugins-official": [{
                "scope": "user",
                "installPath": str(plugins_dir / "cache" / "claude-plugins-official" / "github" / "2.0.0"),
                "version": "2.0.0",
                "installedAt": "2026-04-01T10:00:00.000Z",
                "gitCommitSha": "abc123",
            }],
            "my-tool@community-marketplace": [{
                "scope": "user",
                "installPath": str(plugins_dir / "cache" / "community-marketplace" / "my-tool" / "0.1.0"),
                "version": "0.1.0",
                "installedAt": "2026-04-15T12:00:00.000Z",
                "gitCommitSha": "def456",
            }],
            "blocked-plugin@claude-plugins-official": [{
                "scope": "user",
                "installPath": str(plugins_dir / "cache" / "claude-plugins-official" / "blocked-plugin" / "1.0.0"),
                "version": "1.0.0",
                "installedAt": "2026-05-01T00:00:00.000Z",
                "gitCommitSha": "ghi789",
            }],
        },
    }
    _write_json(plugins_dir / "installed_plugins.json", installed)

    # known_marketplaces.json
    marketplaces = {
        "claude-plugins-official": {
            "source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
            "installLocation": "...",
            "lastUpdated": "2026-05-01T00:00:00.000Z",
        },
        "community-marketplace": {
            "source": {"source": "github", "repo": "community/marketplace"},
            "installLocation": "...",
            "lastUpdated": "2026-05-01T00:00:00.000Z",
        },
    }
    _write_json(plugins_dir / "known_marketplaces.json", marketplaces)

    # blocklist.json with 1 blocked plugin
    blocklist = {
        "fetchedAt": "2026-05-10T00:00:00.000Z",
        "plugins": [
            {
                "plugin": "blocked-plugin@claude-plugins-official",
                "added_at": "2026-05-01T00:00:00.000Z",
                "reason": "security-concern",
                "text": "This plugin was removed for security reasons",
            },
        ],
    }
    _write_json(plugins_dir / "blocklist.json", blocklist)

    # Create cache directories with manifests for slack plugin
    slack_dir = plugins_dir / "cache" / "claude-plugins-official" / "slack" / "1.0.0"
    slack_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "name": "slack",
        "description": "Slack integration for Claude",
        "version": "1.0.0",
        "author": {"name": "Slack", "url": "https://slack.com"},
        "homepage": "https://slack.com/claude",
        "repository": "https://github.com/anthropics/claude-plugins-official",
        "license": "MIT",
        "keywords": ["slack", "messaging"],
        "commands": [{"name": "send-message"}],
        "agents": [],
        "mcpServers": {"slack-mcp": {"command": "npx", "args": ["slack-mcp"]}},
    }
    _write_json(slack_dir / ".claude-plugin" / "plugin.json", manifest)
    # Create skills directory on disk
    (slack_dir / "skills").mkdir(parents=True, exist_ok=True)

    # Create cache directories for github plugin (minimal manifest)
    github_dir = plugins_dir / "cache" / "claude-plugins-official" / "github" / "2.0.0"
    github_dir.mkdir(parents=True, exist_ok=True)
    _write_json(github_dir / ".claude-plugin" / "plugin.json", {
        "name": "github",
        "version": "2.0.0",
    })

    # Create cache directories for community plugin with hooks
    my_tool_dir = plugins_dir / "cache" / "community-marketplace" / "my-tool" / "0.1.0"
    my_tool_dir.mkdir(parents=True, exist_ok=True)
    (my_tool_dir / "hooks").mkdir(parents=True, exist_ok=True)
    (my_tool_dir / "skills").mkdir(parents=True, exist_ok=True)
    _write_json(my_tool_dir / ".claude-plugin" / "plugin.json", {
        "name": "my-tool",
        "version": "0.1.0",
        "author": {"name": "Community Dev"},
        "license": "Apache-2.0",
    })

    # Create cache for blocked plugin (no manifest on disk)
    blocked_dir = plugins_dir / "cache" / "claude-plugins-official" / "blocked-plugin" / "1.0.0"
    blocked_dir.mkdir(parents=True, exist_ok=True)

    return plugins_dir


class TestClaudeCodePluginExtraction(unittest.TestCase):
    """Tests for extract_claude_code_plugins with real filesystem fixtures."""

    def test_four_plugins_with_one_blocked(self):
        """Verify count, blocked flag, is_official, marketplace_name for 4 plugins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = _create_claude_code_fixture(Path(tmpdir))
            plugins = extract_claude_code_plugins(plugins_dir)

            self.assertEqual(len(plugins), 4)

            by_id = {p["plugin_id"]: p for p in plugins}

            # Blocked plugin
            blocked = by_id["blocked-plugin@claude-plugins-official"]
            self.assertTrue(blocked["blocked"])
            self.assertEqual(blocked["block_reason"], "security-concern")
            self.assertTrue(blocked["is_official"])

            # Non-blocked official plugins
            slack = by_id["slack@claude-plugins-official"]
            self.assertFalse(slack["blocked"])
            self.assertIsNone(slack["block_reason"])
            self.assertTrue(slack["is_official"])
            self.assertEqual(slack["marketplace_name"], "claude-plugins-official")

            # Community plugin
            community = by_id["my-tool@community-marketplace"]
            self.assertFalse(community["blocked"])
            self.assertFalse(community["is_official"])
            self.assertEqual(community["marketplace_name"], "community-marketplace")

    def test_missing_plugins_dir_returns_empty(self):
        """Missing plugins directory returns empty list without crashing."""
        result = extract_claude_code_plugins(Path("/nonexistent/path"))
        self.assertEqual(result, [])

    def test_malformed_json_returns_empty(self):
        """Malformed installed_plugins.json returns empty list without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / ".claude" / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)
            (plugins_dir / "installed_plugins.json").write_text("not valid json{{{")
            result = extract_claude_code_plugins(plugins_dir)
            self.assertEqual(result, [])

    def test_full_manifest_fields(self):
        """All manifest fields are populated for a plugin with full metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = _create_claude_code_fixture(Path(tmpdir))
            plugins = extract_claude_code_plugins(plugins_dir)
            by_id = {p["plugin_id"]: p for p in plugins}

            slack = by_id["slack@claude-plugins-official"]
            self.assertEqual(slack["plugin_name"], "slack")
            self.assertEqual(slack["version"], "1.0.0")
            self.assertEqual(slack["scope"], "user")
            self.assertTrue(slack["enabled"])
            self.assertEqual(slack["installed_at"], "2026-03-14T16:29:37.836Z")
            self.assertEqual(slack["git_commit_sha"], "b97f6eadd92917d0f8266309b74396b5a3c4f857")
            self.assertEqual(slack["source_type"], "github")
            self.assertEqual(slack["source_url"], "https://github.com/anthropics/claude-plugins-official")
            self.assertEqual(slack["source_repo"], "anthropics/claude-plugins-official")
            self.assertEqual(slack["marketplace_source_type"], "github")
            self.assertEqual(slack["marketplace_repo"], "anthropics/claude-plugins-official")
            self.assertEqual(slack["author_name"], "Slack")
            self.assertEqual(slack["homepage"], "https://slack.com/claude")
            self.assertEqual(slack["license"], "MIT")
            self.assertTrue(slack["has_mcp_servers"])
            self.assertTrue(slack["has_skills"])
            self.assertFalse(slack["has_hooks"])
            self.assertFalse(slack["has_agents"])
            self.assertTrue(slack["has_commands"])

    def test_minimal_manifest_fields(self):
        """Missing manifest fields are None for a plugin with minimal metadata."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = _create_claude_code_fixture(Path(tmpdir))
            plugins = extract_claude_code_plugins(plugins_dir)
            by_id = {p["plugin_id"]: p for p in plugins}

            github = by_id["github@claude-plugins-official"]
            self.assertEqual(github["plugin_name"], "github")
            self.assertIsNone(github["author_name"])
            self.assertIsNone(github["homepage"])
            self.assertIsNone(github["license"])
            self.assertFalse(github["has_mcp_servers"])
            self.assertFalse(github["has_skills"])
            self.assertFalse(github["has_hooks"])
            self.assertFalse(github["has_agents"])
            self.assertFalse(github["has_commands"])


class TestCursorPluginExtraction(unittest.TestCase):
    """Tests for extract_cursor_plugins with real filesystem fixtures."""

    def test_directory_walk_discovery(self):
        """Cursor plugins are discovered by walking cache directory structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / ".cursor" / "plugins"
            cache_dir = plugins_dir / "cache"

            # Create a plugin under cursor-official marketplace
            version_dir = cache_dir / "cursor-plugins-official" / "my-plugin" / "abc123"
            version_dir.mkdir(parents=True, exist_ok=True)
            cursor_plugin_dir = version_dir / ".cursor-plugin"
            cursor_plugin_dir.mkdir(parents=True, exist_ok=True)
            _write_json(cursor_plugin_dir / "plugin.json", {
                "name": "my-plugin",
                "version": "1.2.3",
                "author": {"name": "Cursor Inc"},
                "license": "MIT",
            })
            _write_json(cursor_plugin_dir / "marketplace.json", {
                "source": {"source": "github", "repo": "cursor/official-plugins"},
            })

            plugins = extract_cursor_plugins(plugins_dir)
            self.assertEqual(len(plugins), 1)

            plugin = plugins[0]
            self.assertEqual(plugin["plugin_name"], "my-plugin")
            self.assertEqual(plugin["marketplace_name"], "cursor-plugins-official")
            self.assertTrue(plugin["is_official"])
            self.assertEqual(plugin["version"], "1.2.3")
            self.assertEqual(plugin["marketplace_source_type"], "github")
            self.assertEqual(plugin["marketplace_repo"], "cursor/official-plugins")
            self.assertEqual(plugin["source_url"], "https://github.com/cursor/official-plugins")

    def test_empty_dir_returns_empty(self):
        """Empty plugins directory returns empty list without crashing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / ".cursor" / "plugins"
            plugins_dir.mkdir(parents=True, exist_ok=True)
            result = extract_cursor_plugins(plugins_dir)
            self.assertEqual(result, [])

    def test_nonexistent_dir_returns_empty(self):
        """Non-existent directory returns empty list without crashing."""
        result = extract_cursor_plugins(Path("/nonexistent/cursor/plugins"))
        self.assertEqual(result, [])

    def test_plugin_with_hooks_skills_on_disk(self):
        """Capability flags reflect actual disk contents."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / ".cursor" / "plugins"
            version_dir = plugins_dir / "cache" / "community" / "rich-plugin" / "v1"
            version_dir.mkdir(parents=True, exist_ok=True)

            # Create capabilities on disk
            (version_dir / "hooks").mkdir()
            (version_dir / "skills").mkdir()
            _write_json(version_dir / ".mcp.json", {
                "mcpServers": {"test": {"command": "echo"}},
            })

            _write_json(version_dir / ".cursor-plugin" / "plugin.json", {
                "name": "rich-plugin",
                "version": "1.0.0",
                "agents": [{"name": "helper"}],
                "commands": [{"name": "do-thing"}],
            })

            plugins = extract_cursor_plugins(plugins_dir)
            self.assertEqual(len(plugins), 1)

            plugin = plugins[0]
            self.assertTrue(plugin["has_hooks"])
            self.assertTrue(plugin["has_skills"])
            self.assertTrue(plugin["has_mcp_servers"])
            self.assertTrue(plugin["has_agents"])
            self.assertTrue(plugin["has_commands"])


class TestBuildPluginLookup(unittest.TestCase):
    """Tests for build_plugin_install_path_lookup."""

    def test_keys_are_install_paths(self):
        """Lookup keys are install path strings, values have correct fields."""
        plugins = [
            {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
                "install_path": "/home/user/.claude/plugins/cache/official/slack/1.0.0",
            },
            {
                "plugin_id": "tool@community",
                "marketplace_name": "community",
                "source_type": "github",
                "is_official": False,
                "install_path": "/home/user/.claude/plugins/cache/community/tool/0.1.0",
            },
        ]
        lookup = build_plugin_install_path_lookup(plugins)

        self.assertEqual(len(lookup), 2)
        self.assertIn("/home/user/.claude/plugins/cache/official/slack/1.0.0", lookup)
        self.assertIn("/home/user/.claude/plugins/cache/community/tool/0.1.0", lookup)

        entry = lookup["/home/user/.claude/plugins/cache/official/slack/1.0.0"]
        self.assertEqual(entry["plugin_id"], "slack@official")
        self.assertEqual(entry["marketplace_name"], "official")
        self.assertEqual(entry["source_type"], "github")
        self.assertTrue(entry["is_official"])

    def test_skips_plugins_without_install_path(self):
        """Plugins with no install_path are excluded from the lookup."""
        plugins = [
            {"plugin_id": "no-path@x", "install_path": None},
            {"plugin_id": "empty@x", "install_path": ""},
        ]
        lookup = build_plugin_install_path_lookup(plugins)
        self.assertEqual(len(lookup), 0)


class TestSkillPluginTagging(unittest.TestCase):
    """Tests for skill provenance tagging via plugin_lookup."""

    def test_skill_inside_plugin_path_gets_plugin_source(self):
        """A skill whose file_path falls under a plugin install path is tagged as 'plugin'."""
        plugin_lookup = {
            "/home/user/.claude/plugins/cache/official/slack/1.0.0": {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
        }
        provenance = _find_plugin_provenance(
            "/home/user/.claude/plugins/cache/official/slack/1.0.0/skills/send/SKILL.md",
            plugin_lookup,
        )
        self.assertEqual(provenance["source"], "plugin")
        self.assertEqual(provenance["plugin_id"], "slack@official")
        self.assertEqual(provenance["marketplace_name"], "official")
        self.assertEqual(provenance["source_type"], "github")

    def test_skill_outside_plugin_path_gets_standalone(self):
        """A skill not under any plugin path is tagged as 'standalone'."""
        plugin_lookup = {
            "/home/user/.claude/plugins/cache/official/slack/1.0.0": {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
        }
        provenance = _find_plugin_provenance(
            "/home/user/projects/myapp/.claude/skills/deploy/SKILL.md",
            plugin_lookup,
        )
        self.assertEqual(provenance["source"], "standalone")

    def test_no_plugin_lookup_returns_standalone(self):
        """When plugin_lookup is None, all skills are standalone."""
        provenance = _find_plugin_provenance(
            "/home/user/.claude/plugins/cache/official/slack/1.0.0/skills/send/SKILL.md",
            None,
        )
        self.assertEqual(provenance["source"], "standalone")

    def test_longest_prefix_match(self):
        """When multiple plugin paths match, the longest prefix wins."""
        plugin_lookup = {
            "/home/user/.claude/plugins/cache/official": {
                "plugin_id": "general@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
            "/home/user/.claude/plugins/cache/official/slack/1.0.0": {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
        }
        provenance = _find_plugin_provenance(
            "/home/user/.claude/plugins/cache/official/slack/1.0.0/skills/send/SKILL.md",
            plugin_lookup,
        )
        self.assertEqual(provenance["plugin_id"], "slack@official")


class TestClaudeCodePluginCapabilities(unittest.TestCase):
    """Tests for capability detection from both disk and manifest."""

    def test_capabilities_from_disk_only(self):
        """Capabilities are detected from disk contents even without manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            plugins_dir = Path(tmpdir) / ".claude" / "plugins"
            install_path = plugins_dir / "cache" / "claude-plugins-official" / "disk-plugin" / "1.0.0"
            install_path.mkdir(parents=True, exist_ok=True)

            # Create disk structures
            (install_path / "skills").mkdir()
            (install_path / "hooks").mkdir()
            _write_json(install_path / ".mcp.json", {"mcpServers": {"s": {"command": "x"}}})

            # No manifest at all
            installed = {
                "version": 2,
                "plugins": {
                    "disk-plugin@claude-plugins-official": [{
                        "scope": "user",
                        "installPath": str(install_path),
                        "version": "1.0.0",
                        "installedAt": "2026-01-01T00:00:00.000Z",
                        "gitCommitSha": "aaa",
                    }],
                },
            }
            _write_json(plugins_dir / "installed_plugins.json", installed)
            _write_json(plugins_dir / "known_marketplaces.json", {
                "claude-plugins-official": {
                    "source": {"source": "github", "repo": "anthropics/claude-plugins-official"},
                },
            })

            plugins = extract_claude_code_plugins(plugins_dir)
            self.assertEqual(len(plugins), 1)
            plugin = plugins[0]
            self.assertTrue(plugin["has_skills"])
            self.assertTrue(plugin["has_hooks"])
            self.assertTrue(plugin["has_mcp_servers"])


class TestMcpProvenanceTagging(unittest.TestCase):
    """Tests for MCP server provenance tagging via plugin_lookup."""

    def test_exact_path_match_for_mcp_provenance(self):
        """MCP provenance matches when path equals install_path exactly (no child path)."""
        from scripts.coding_discovery_tools.plugin_extraction_helpers import find_plugin_provenance_by_path

        plugin_lookup = {
            "/home/user/.claude/plugins/cache/official/slack/1.0.0": {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
        }
        # Exact match — this is how MCP extraction calls it
        result = find_plugin_provenance_by_path(
            "/home/user/.claude/plugins/cache/official/slack/1.0.0",
            plugin_lookup,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["plugin_id"], "slack@official")

    def test_mcp_from_plugin_json_gets_provenance(self):
        """A .claude-plugin/plugin.json server map is tagged with plugin provenance."""
        from scripts.coding_discovery_tools.mcp_extraction_helpers import (
            _append_plugin_project,
            _plugin_mcp_server_map,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            install_path = os.path.join(tmpdir, "cache", "official", "slack", "1.0.0")
            _write_json(Path(install_path) / ".claude-plugin" / "plugin.json", {
                "name": "slack",
                "mcpServers": {"slack-mcp": {"command": "npx", "args": ["slack-mcp"]}},
            })

            plugin_lookup = {
                install_path: {
                    "plugin_id": "slack@official",
                    "marketplace_name": "official",
                    "source_type": "github",
                    "is_official": True,
                },
            }

            projects = []
            servers = _plugin_mcp_server_map(Path(install_path))
            _append_plugin_project(
                Path(install_path), "slack", servers, projects, plugin_lookup=plugin_lookup,
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["source"], "plugin")
            self.assertEqual(projects[0]["plugin_id"], "slack@official")
            self.assertEqual(projects[0]["marketplace_name"], "official")

    def test_mcp_from_dot_mcp_json_gets_provenance(self):
        """A .mcp.json server map is tagged with plugin provenance."""
        from scripts.coding_discovery_tools.mcp_extraction_helpers import (
            _append_plugin_project,
            _plugin_mcp_server_map,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            install_path = os.path.join(tmpdir, "cache", "official", "slack", "1.0.0")
            _write_json(Path(install_path) / ".mcp.json", {
                "mcpServers": {"slack-mcp": {"command": "npx", "args": ["slack-mcp"]}},
            })

            plugin_lookup = {
                install_path: {
                    "plugin_id": "slack@official",
                    "marketplace_name": "official",
                    "source_type": "github",
                    "is_official": True,
                },
            }

            projects = []
            servers = _plugin_mcp_server_map(Path(install_path))
            _append_plugin_project(
                Path(install_path), "slack", servers, projects, plugin_lookup=plugin_lookup,
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["source"], "plugin")
            self.assertEqual(projects[0]["plugin_id"], "slack@official")


    def test_windows_path_separator_match(self):
        """Provenance matches even when install_path uses backslashes and path uses forward slashes."""
        from scripts.coding_discovery_tools.plugin_extraction_helpers import find_plugin_provenance_by_path

        plugin_lookup = {
            "C:\\Users\\user\\.claude\\plugins\\cache\\official\\slack\\1.0.0": {
                "plugin_id": "slack@official",
                "marketplace_name": "official",
                "source_type": "github",
                "is_official": True,
            },
        }
        # Forward-slash path against backslash lookup key
        result = find_plugin_provenance_by_path(
            "C:/Users/user/.claude/plugins/cache/official/slack/1.0.0/skills/send/SKILL.md",
            plugin_lookup,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result["plugin_id"], "slack@official")

    def test_non_cache_plugin_json_gets_provenance(self):
        """Non-cache plugin.json (plugins/<name>/plugin.json) gets correct plugin_root."""
        from scripts.coding_discovery_tools.mcp_extraction_helpers import (
            _append_plugin_project,
            _plugin_mcp_server_map,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            # Non-cache layout: plugins/<plugin>/plugin.json (no .claude-plugin subdir)
            plugin_dir = os.path.join(tmpdir, "plugins", "my-plugin")
            _write_json(Path(plugin_dir) / "plugin.json", {
                "name": "my-plugin",
                "mcpServers": {"my-mcp": {"command": "npx", "args": ["my-mcp"]}},
            })

            plugin_lookup = {
                plugin_dir: {
                    "plugin_id": "my-plugin@marketplace",
                    "marketplace_name": "marketplace",
                    "source_type": "github",
                    "is_official": False,
                },
            }

            projects = []
            servers = _plugin_mcp_server_map(Path(plugin_dir))
            _append_plugin_project(
                Path(plugin_dir), "my-plugin", servers, projects, plugin_lookup=plugin_lookup,
            )

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["path"], plugin_dir)
            self.assertEqual(projects[0]["source"], "plugin")
            self.assertEqual(projects[0]["plugin_id"], "my-plugin@marketplace")


class TestCodexPluginEnumeration(unittest.TestCase):
    """extract_codex_plugins: resilience, symlink/containment, version selection."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk_plugin(self, codex, marketplace, plugin, skill, version=None, hashname="h1"):
        base = codex / "plugins" / "cache" / marketplace / plugin / hashname
        (base / "skills" / skill).mkdir(parents=True)
        (base / "skills" / skill / "SKILL.md").write_text(f"# {skill}", encoding="utf-8")
        if version is not None:
            (base / ".codex-plugin").mkdir(parents=True, exist_ok=True)
            (base / ".codex-plugin" / "plugin.json").write_text(
                json.dumps({"name": plugin, "version": version}), encoding="utf-8")
        return base

    def _enable(self, codex, *ids):
        codex.mkdir(parents=True, exist_ok=True)
        (codex / "config.toml").write_text(
            "".join(f'[plugins."{i}"]\nenabled = true\n' for i in ids), encoding="utf-8")

    def test_selects_highest_version_not_newest_mtime(self):
        from scripts.coding_discovery_tools.plugin_extraction_helpers import extract_codex_plugins
        codex = self.tmp / ".codex"
        self._mk_plugin(codex, "openai-curated", "linear", "linear", version="0.0.9", hashname="hi")
        low = self._mk_plugin(codex, "openai-curated", "linear", "linear", version="0.0.3", hashname="lo")
        os.utime(low, None)  # low version is the newest by mtime -> version must still win
        self._enable(codex, "linear@openai-curated")
        plugins = extract_codex_plugins(codex)
        self.assertEqual(len(plugins), 1)
        self.assertTrue(plugins[0]["install_path"].endswith("hi"))

    def test_unreadable_marketplace_does_not_abort_the_rest(self):
        from scripts.coding_discovery_tools.plugin_extraction_helpers import extract_codex_plugins
        codex = self.tmp / ".codex"
        self._mk_plugin(codex, "good", "alpha", "alpha")
        bad = codex / "plugins" / "cache" / "bad"
        (bad / "beta").mkdir(parents=True)
        os.chmod(bad, 0o000)
        try:
            names = {p["plugin_name"] for p in extract_codex_plugins(codex)}
            self.assertIn("alpha", names)  # good marketplace still scanned
        finally:
            os.chmod(bad, 0o755)

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_symlinked_plugin_dir_escaping_home_is_rejected(self):
        from scripts.coding_discovery_tools.plugin_extraction_helpers import extract_codex_plugins
        codex = self.tmp / ".codex"
        outside = self.tmp / "evil"
        (outside / "skills" / "secret").mkdir(parents=True)
        (outside / "skills" / "secret" / "SKILL.md").write_text("# secret", encoding="utf-8")
        cache = codex / "plugins" / "cache" / "openai-curated"
        cache.mkdir(parents=True)
        os.symlink(outside, cache / "linear")
        self._enable(codex, "linear@openai-curated")
        self.assertEqual(extract_codex_plugins(codex), [])  # symlink escape rejected

    @unittest.skipUnless(os.name == "posix", "POSIX symlink semantics")
    def test_within_home_skill_symlink_is_not_read(self):
        """A crafted plugin whose skills/<name>/SKILL.md symlinks to another file under
        the SAME home (e.g. ~/.ssh/id_ed25519) must not be read or reported."""
        from scripts.coding_discovery_tools.codex_skills_helpers import (
            extract_codex_user_level_items, CODEX_ITEM_CONFIGS)
        from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file
        home = self.tmp / "alice"
        secret = home / ".ssh" / "id_ed25519"
        secret.parent.mkdir(parents=True)
        secret.write_text("PRIVATE KEY MATERIAL", encoding="utf-8")
        inst = home / ".codex" / "plugins" / "cache" / "openai-curated" / "evil" / "h" / "skills" / "x"
        inst.mkdir(parents=True)
        os.symlink(secret, inst / "SKILL.md")
        self._enable(home / ".codex", "evil@openai-curated")
        us = []
        extract_codex_user_level_items(home, us, extract_single_rule_file, CODEX_ITEM_CONFIGS)
        self.assertFalse([s for s in us if "PRIVATE KEY" in (s.get("content") or "")])
        self.assertNotIn("x", [s.get("skill_name") for s in us])

    def test_multiline_string_cannot_false_disable_plugin(self):
        """An `enabled = false` embedded in a TOML multiline string must not disable a
        real plugin (Codex is enabled-unless-disabled, so only genuine records count)."""
        from scripts.coding_discovery_tools.plugin_extraction_helpers import _codex_disabled_plugin_ids
        codex = self.tmp / ".codex"
        codex.mkdir(parents=True)
        (codex / "config.toml").write_text(
            'note = """\n'
            '[plugins."real@openai-curated"]\n'
            'enabled = false\n'
            '"""\n'
            '[plugins."reallyoff@openai-curated"]\n'
            'enabled = false\n',
            encoding="utf-8",
        )
        disabled = _codex_disabled_plugin_ids(codex)
        self.assertIn("reallyoff@openai-curated", disabled)     # genuine record honored
        self.assertNotIn("real@openai-curated", disabled)       # string-embedded one ignored

    def test_installed_plugin_enabled_by_default_and_comment_tolerant(self):
        """Codex reports an installed plugin unless it is explicitly turned off. A config
        with no plugin table, or `enabled = true # comment`, must still be reported."""
        from scripts.coding_discovery_tools.plugin_extraction_helpers import extract_codex_plugins
        codex = self.tmp / ".codex"
        self._mk_plugin(codex, "openai-curated", "linear", "linear")
        # MCP-only config, no [plugins."..."] table at all -> default enabled
        (codex / "config.toml").write_text('[mcp_servers.foo]\nurl = "x"\n', encoding="utf-8")
        self.assertIn("linear", {p["plugin_name"] for p in extract_codex_plugins(codex)})
        # explicit enable with a trailing comment -> still reported
        (codex / "config.toml").write_text(
            '[plugins."linear@openai-curated"]\nenabled = true  # on\n', encoding="utf-8")
        self.assertIn("linear", {p["plugin_name"] for p in extract_codex_plugins(codex)})
        # explicit disable (with comment / case) -> skipped
        (codex / "config.toml").write_text(
            '[plugins."linear@openai-curated"]\nenabled = False  # off\n', encoding="utf-8")
        self.assertEqual(extract_codex_plugins(codex), [])


if __name__ == "__main__":
    unittest.main()
