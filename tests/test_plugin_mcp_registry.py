"""
Tests for registry-first Claude Code plugin MCP resolution.

Mirrors the hook's `_resolve_plugin_mcp_config`: installed_plugins.json plus
known_marketplaces.json are authoritative, and the plugin cache is only read
when that registry is unusable. Fixtures are real temp directories.

The live MCP scanner is patched out globally in ``tests/__init__.py``.
"""

import json
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    _authoritative_plugin_dirs,
    _extract_plugin_mcp_for_dir,
    _plugin_mcp_server_map,
    _select_plugin_version_dir,
)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _server(name: str = "srv") -> dict:
    return {"mcpServers": {name: {"command": "npx", "args": [name]}}}


def _registry(plugins_dir: Path, plugin_id: str, install_path: Path) -> None:
    _write_json(plugins_dir / "installed_plugins.json", {
        "version": 2,
        "plugins": {plugin_id: [{"installPath": str(install_path), "version": "1.0.0"}]},
    })


def _server_names(project: dict) -> set:
    return {s["name"] for s in project["mcpServers"]}


def _exposed(plugin: str, *keys: str) -> set:
    """The names Claude Code addresses a plugin's servers by."""
    return {"plugin_%s_%s" % (plugin, key) for key in keys}


class TestRegistryResolution(unittest.TestCase):
    """installed_plugins.json drives resolution; the cache is the fallback."""

    def test_install_path_outside_cache_is_found(self):
        """A plugin installed outside ~/.claude/plugins/cache still reports servers."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "my-plugin"
            _write_json(install_path / ".mcp.json", _server("local-srv"))
            _registry(plugins_dir, "my-plugin@local", install_path)

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["path"], str(install_path))
            self.assertEqual(projects[0]["scope"], "plugin")
            self.assertEqual(projects[0]["pluginName"], "my-plugin")
            self.assertEqual(_server_names(projects[0]), _exposed("my-plugin", "local-srv"))

    def test_directory_marketplace_install_location(self):
        """A directory-source marketplace resolves via installLocation/plugins/<name>."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            location = base / "marketplace"
            _write_json(location / "plugins" / "tools" / ".mcp.json", _server("mkt-srv"))
            _write_json(plugins_dir / "installed_plugins.json", {
                "version": 2,
                "plugins": {"tools@local-mkt": [{"version": "1.0.0"}]},
            })
            _write_json(plugins_dir / "known_marketplaces.json", {
                "local-mkt": {
                    "source": {"source": "directory"},
                    "installLocation": str(location),
                },
            })

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("tools", "mkt-srv"))

    def test_marketplace_manifest_source_path(self):
        """marketplace.json `source.path` locates a plugin in a non-standard subdir."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            location = base / "marketplace"
            _write_json(location / ".claude-plugin" / "marketplace.json", {
                "plugins": [{"name": "tools", "source": {"path": "packages/tools"}}],
            })
            _write_json(location / "packages" / "tools" / ".mcp.json", _server("nested-srv"))
            _write_json(plugins_dir / "installed_plugins.json", {
                "version": 2,
                "plugins": {"tools@local-mkt": [{"version": "1.0.0"}]},
            })
            _write_json(plugins_dir / "known_marketplaces.json", {
                "local-mkt": {
                    "source": {"source": "directory"},
                    "installLocation": str(location),
                },
            })

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("tools", "nested-srv"))

    def test_emits_once_when_two_dirs_define_the_plugin(self):
        """First authoritative dir wins; later candidates don't re-report."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            location = base / "marketplace"
            install_path = base / "dev" / "tools"
            _write_json(location / "plugins" / "tools" / ".mcp.json", _server("from-mkt"))
            _write_json(install_path / ".mcp.json", _server("from-install-path"))
            _write_json(plugins_dir / "installed_plugins.json", {
                "version": 2,
                "plugins": {"tools@local-mkt": [{"installPath": str(install_path)}]},
            })
            _write_json(plugins_dir / "known_marketplaces.json", {
                "local-mkt": {
                    "source": {"source": "directory"},
                    "installLocation": str(location),
                },
            })

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("tools", "from-mkt"))

    def test_registry_unusable_falls_back_to_cache(self):
        """No installed_plugins.json -> the cache scan still reports servers."""
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = Path(tmp) / ".claude" / "plugins"
            version_dir = plugins_dir / "cache" / "official" / "slack" / "1.0.0"
            _write_json(version_dir / ".mcp.json", _server("slack-srv"))

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(projects[0]["pluginName"], "slack")
            self.assertEqual(_server_names(projects[0]), _exposed("slack", "slack-srv"))

    def test_registry_present_skips_cache(self):
        """A usable registry is authoritative — stale cache copies aren't re-read."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "slack"
            _write_json(install_path / ".mcp.json", _server("current"))
            _write_json(
                plugins_dir / "cache" / "official" / "slack" / "0.9.0" / ".mcp.json",
                _server("stale"),
            )
            _registry(plugins_dir, "slack@official", install_path)

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("slack", "current"))

    def test_unreadable_registry_falls_back(self):
        """A malformed installed_plugins.json degrades to the cache scan."""
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = Path(tmp) / ".claude" / "plugins"
            plugins_dir.mkdir(parents=True)
            (plugins_dir / "installed_plugins.json").write_text("{not json", encoding="utf-8")
            _write_json(
                plugins_dir / "cache" / "official" / "slack" / "1.0.0" / ".mcp.json",
                _server("slack-srv"),
            )

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("slack", "slack-srv"))

    def test_version_field_is_not_gated_on(self):
        """An unrecognised installed_plugins.json version is still read."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "my-plugin"
            _write_json(install_path / ".mcp.json", _server("future-srv"))
            _write_json(plugins_dir / "installed_plugins.json", {
                "version": 99,
                "plugins": {"my-plugin@local": [{"installPath": str(install_path)}]},
            })

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("my-plugin", "future-srv"))


class TestVersionDirSelection(unittest.TestCase):
    """Only the live version dir of a cached plugin is read."""

    def test_in_use_marker_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "slack"
            old = plugin_dir / "0.9.0"
            new = plugin_dir / "1.0.0"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            (old / ".in_use").write_text("", encoding="utf-8")

            self.assertEqual(_select_plugin_version_dir(plugin_dir), old)

    def test_newest_wins_without_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "slack"
            old = plugin_dir / "0.9.0"
            new = plugin_dir / "1.0.0"
            old.mkdir(parents=True)
            new.mkdir(parents=True)
            import os
            os.utime(old, (1_000_000, 1_000_000))
            os.utime(new, (2_000_000, 2_000_000))

            self.assertEqual(_select_plugin_version_dir(plugin_dir), new)

    def test_only_live_version_reported(self):
        """Two cached versions produce one project entry, not two."""
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = Path(tmp) / ".claude" / "plugins"
            plugin_dir = plugins_dir / "cache" / "official" / "slack"
            _write_json(plugin_dir / "0.9.0" / ".mcp.json", _server("stale"))
            _write_json(plugin_dir / "1.0.0" / ".mcp.json", _server("current"))
            (plugin_dir / "1.0.0" / ".in_use").write_text("", encoding="utf-8")

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(len(projects), 1)
            self.assertEqual(_server_names(projects[0]), _exposed("slack", "current"))


class TestServerMapParsing(unittest.TestCase):
    """`.mcp.json` / `plugin.json` shapes the hook accepts."""

    def test_unwrapped_root_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / ".mcp.json", {
                "srv": {"command": "npx", "args": ["srv"]},
            })

            self.assertEqual(set(_plugin_mcp_server_map(plugin_dir)), {"srv"})

    def test_unwrapped_root_map_rejects_metadata_blocks(self):
        """Non-server objects must not become phantom servers."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / ".mcp.json", {
                "$schema": {"$ref": "https://example.com/schema.json"},
                "inputs": {"token": {"type": "promptString"}},
                "srv": {"command": "npx", "args": ["srv"]},
                "remote": {"url": "https://example.com/mcp"},
            })

            self.assertEqual(set(_plugin_mcp_server_map(plugin_dir)), {"srv", "remote"})

    def test_string_mcp_servers_path(self):
        """`mcpServers` naming another file inside the plugin is followed."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / ".mcp.json", {"mcpServers": "servers/mcp.json"})
            _write_json(plugin_dir / "servers" / "mcp.json", _server("nested"))

            self.assertEqual(set(_plugin_mcp_server_map(plugin_dir)), {"nested"})

    def test_string_mcp_servers_path_rejects_traversal(self):
        """A path escaping the plugin dir resolves to nothing."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugin_dir = base / "plugin"
            _write_json(plugin_dir / ".mcp.json", {"mcpServers": "../outside/mcp.json"})
            _write_json(base / "outside" / "mcp.json", _server("leaked"))

            self.assertEqual(_plugin_mcp_server_map(plugin_dir), {})

    def test_string_mcp_servers_path_rejects_absolute(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugin_dir = base / "plugin"
            outside = base / "outside" / "mcp.json"
            _write_json(outside, _server("leaked"))
            _write_json(plugin_dir / ".mcp.json", {"mcpServers": str(outside)})

            self.assertEqual(_plugin_mcp_server_map(plugin_dir), {})

    def test_dot_mcp_json_wins_over_plugin_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / ".mcp.json", {
                "mcpServers": {"srv": {"command": "from-mcp-json"}},
            })
            _write_json(plugin_dir / ".claude-plugin" / "plugin.json", {
                "mcpServers": {"srv": {"command": "from-plugin-json"}},
            })

            servers = _plugin_mcp_server_map(plugin_dir)
            self.assertEqual(servers["srv"]["command"], "from-mcp-json")

    def test_sources_are_merged(self):
        """Names unique to plugin.json survive alongside .mcp.json ones."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / ".mcp.json", {"mcpServers": {"a": {"command": "a"}}})
            _write_json(plugin_dir / ".claude-plugin" / "plugin.json", {
                "mcpServers": {"b": {"command": "b"}},
            })

            self.assertEqual(set(_plugin_mcp_server_map(plugin_dir)), {"a", "b"})

    def test_bare_plugin_json_at_root_is_read(self):
        """Third source the hook lacks, kept from the sweep's earlier behaviour."""
        with tempfile.TemporaryDirectory() as tmp:
            plugin_dir = Path(tmp) / "plugin"
            _write_json(plugin_dir / "plugin.json", {
                "mcpServers": {"root-level": {"command": "npx"}},
            })

            self.assertEqual(set(_plugin_mcp_server_map(plugin_dir)), {"root-level"})

    def test_missing_plugin_dir_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_plugin_mcp_server_map(Path(tmp) / "nope"), {})


class TestAuthoritativeDirOrder(unittest.TestCase):
    """Directory-marketplace layouts precede installPath."""

    def test_directory_layouts_come_first(self):
        dirs = _authoritative_plugin_dirs(
            "tools",
            {"source": {"source": "directory"}, "installLocation": "/mkt"},
            [{"installPath": "/installed/tools"}],
        )
        self.assertEqual(dirs[0], Path("/mkt/plugins/tools"))
        self.assertEqual(dirs[1], Path("/mkt/tools"))
        self.assertIn(Path("/installed/tools"), dirs)

    def test_github_marketplace_uses_install_path_only(self):
        dirs = _authoritative_plugin_dirs(
            "tools",
            {"source": {"source": "github", "repo": "org/repo"}},
            [{"installPath": "/installed/tools"}],
        )
        self.assertEqual(dirs, [Path("/installed/tools")])

    def test_no_duplicate_dirs(self):
        dirs = _authoritative_plugin_dirs(
            "tools",
            {"source": {"source": "directory"}, "installLocation": "/mkt"},
            [{"installPath": "/mkt/plugins/tools"}, {"installPath": "/mkt/plugins/tools"}],
        )
        self.assertEqual(len(dirs), len(set(dirs)))


class TestClaudeExposedServerNames(unittest.TestCase):
    """Servers are reported under the name Claude Code addresses them by.

    A plugin server is `plugin_<plugin>_<key>` at the tool-call layer, so that is
    what the hook reports for it. Reporting the bare key would file one server
    under two identities.
    """

    def test_opaque_numeric_plugin_id(self):
        """The rename case behind WEB-5335: plugin dir is an opaque numeric ID."""
        with tempfile.TemporaryDirectory() as tmp:
            plugins_dir = Path(tmp) / ".claude" / "plugins"
            _write_json(
                plugins_dir / "cache" / "official" / "1693077056" / "1.0.0" / ".mcp.json",
                _server("toolchain"),
            )

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(
                _server_names(projects[0]), {"plugin_1693077056_toolchain"}
            )

    def test_registry_plugin_name_is_the_prefix(self):
        """Through the registry the prefix is the plugin name, not the dir name."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "checkout-v2"
            _write_json(install_path / ".mcp.json", _server("api"))
            _registry(plugins_dir, "stripe@official", install_path)

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(_server_names(projects[0]), {"plugin_stripe_api"})

    def test_punctuation_is_mangled(self):
        """Anything outside [A-Za-z0-9_-] collapses to `_`, as Claude Code does."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "plugin"
            _write_json(install_path / ".mcp.json", _server("my.srv"))
            _registry(plugins_dir, "my.plugin@official", install_path)

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(_server_names(projects[0]), {"plugin_my_plugin_my_srv"})

    def test_colliding_keys_both_survive(self):
        """Two keys mangling to one name: the loser keeps its raw key."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            plugins_dir = base / ".claude" / "plugins"
            install_path = base / "dev" / "plugin"
            _write_json(install_path / ".mcp.json", {"mcpServers": {
                "a_b": {"command": "npx", "args": ["one"]},
                "a.b": {"command": "npx", "args": ["two"]},
            }})
            _registry(plugins_dir, "p@official", install_path)

            projects = []
            _extract_plugin_mcp_for_dir(plugins_dir, projects)

            self.assertEqual(_server_names(projects[0]), {"plugin_p_a_b", "a.b"})


if __name__ == "__main__":
    unittest.main()
