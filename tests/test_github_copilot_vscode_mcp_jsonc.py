"""
JSONC + dead-fallback tests for the GitHub Copilot VS Code MCP extractors.

The VS Code ``Code/User/mcp.json`` is JSONC in practice (VS Code lets users add
// and /* */ comments and trailing commas). The github_copilot extractors strip
JSONC comments and trailing commas before ``json.loads``; without that, any
commented or trailing-comma config raises JSONDecodeError and surfaces ZERO
servers. They reuse the same strippers the Copilot CLI extractor uses.

The dead ``globalStorage/ms-vscode.vscode-github-copilot/mcp.json`` fallback
branch was removed. VS Code Copilot only ever reads the primary
``Code/User/mcp.json``, so a server-bearing file at the old globalStorage path
must NOT be consulted.

These exercise the per-user method ``_extract_vscode_configs_for_user`` directly
(passing a temp home) to avoid full-filesystem scans. A shared mixin runs every
case against all three OS extractors, each with its correct per-OS Code/User
base path.

Conventions mirror the existing suite: temp HOME dirs, the globally-stubbed MCP
scanner (``tests/__init__.py`` patches ``_scan_servers_in_mapping`` -> {}), and
``_SENTRY_DSN`` forced empty to prevent real Sentry calls.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    enumerate_vscode_mcp_files,
)
from scripts.coding_discovery_tools.macos.github_copilot.mcp_config_extractor import (
    MacOSGitHubCopilotMCPConfigExtractor,
)
from scripts.coding_discovery_tools.linux.github_copilot.mcp_config_extractor import (
    LinuxGitHubCopilotMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.github_copilot.mcp_config_extractor import (
    WindowsGitHubCopilotMCPConfigExtractor,
)


class _GitHubCopilotVscodeMcpMixin:
    """Shared cases parametrized over the 3 OS extractors.

    Subclasses set ``extractor_cls`` and ``code_user_relpath`` (the per-OS path
    from the user home down to the ``Code/User`` dir that holds ``mcp.json``).
    """

    extractor_cls = None
    code_user_relpath = ()  # tuple of path segments under user_home

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = self.extractor_cls()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.code_user_base = self.user_home.joinpath(*self.code_user_relpath)
        self.code_user_base.mkdir(parents=True)
        self.primary_path = self.code_user_base / "mcp.json"
        # Insiders base is a sibling of the stable base with the trailing
        # "Code" segment swapped for "Code - Insiders": code_user_base ends in
        # ``.../Code/User`` so parent.parent strips ``User`` then ``Code``.
        self.insiders_base = (
            self.code_user_base.parent.parent / "Code - Insiders" / "User"
        )

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_primary(self, text: str) -> None:
        self.primary_path.write_text(text, encoding="utf-8")

    def _extract(self):
        return self.extractor._extract_vscode_configs_for_user(self.user_home)

    def _server_names(self, configs) -> set:
        self.assertEqual(len(configs), 1)
        return {s["name"] for s in configs[0]["mcpServers"]}

    # -- JSONC tolerance ---------------------------------------------------

    def test_commented_mcp_json_surfaces_server(self):
        """// and /* */ comments must be stripped (previously surfaced 0 servers)."""
        self._write_primary(
            "{\n"
            "  // VS Code Copilot MCP servers\n"
            '  "servers": {\n'
            '    "serena": { "command": "uvx", "args": ["serena"] } /* serena */\n'
            "  }\n"
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

    def test_trailing_comma_mcp_json_surfaces_server(self):
        """A hand-edited trailing comma must parse (previously surfaced 0 servers)."""
        self._write_primary('{"servers":{"serena":{"command":"uvx"}},}')
        self.assertIn("serena", self._server_names(self._extract()))

    def test_comment_and_trailing_comma_combined_surfaces_server(self):
        """Comments AND a trailing comma together (the real-world hand-edit)."""
        self._write_primary(
            "{\n"
            '  "servers": {\n'
            '    "serena": { "command": "uvx" }, // serena\n'
            "  },\n"
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

    def test_valid_clean_json_still_surfaces(self):
        """No regression: a plain valid JSON config still surfaces the server."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self.assertIn("serena", self._server_names(self._extract()))

    def test_both_top_level_keys_resolve_under_stripping(self):
        """Both ``servers`` and ``mcpServers`` top-level keys resolve, with JSONC."""
        # ``servers`` form (VS Code style) with a comment.
        self._write_primary(
            "{\n"
            '  "servers": { "serena": { "command": "uvx" } } // vscode style\n'
            "}\n"
        )
        self.assertIn("serena", self._server_names(self._extract()))

        # ``mcpServers`` form (Claude style) with a trailing comma.
        self._write_primary('{"mcpServers":{"github":{"url":"https://x/mcp"}},}')
        self.assertIn("github", self._server_names(self._extract()))

    # -- Primary path read; dead fallback removed --------------------------

    def test_primary_only_surfaces_and_returns_code_user_base(self):
        """Only the primary Code/User/mcp.json present → servers surface and the
        returned path is the Code/User base."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertIn("serena", self._server_names(configs))
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_old_globalstorage_fallback_never_consulted(self):
        """A server-bearing file at the OLD globalStorage path with NO primary
        must yield an empty result — the dead fallback branch is gone."""
        fallback_path = (
            self.code_user_base
            / "globalStorage"
            / "ms-vscode.vscode-github-copilot"
            / "mcp.json"
        )
        fallback_path.parent.mkdir(parents=True)
        fallback_path.write_text(
            json.dumps({"servers": {"serena": {"command": "uvx"}}}),
            encoding="utf-8",
        )
        self.assertEqual(self._extract(), [])

    # -- Customer-machine safety -------------------------------------------

    def test_irreparably_malformed_json_no_crash_empty(self):
        """JSON broken beyond comments/commas must not raise; returns empty."""
        self._write_primary("{ this is not valid json {{{")
        self.assertEqual(self._extract(), [])

    # -- Named profiles + Insiders (WEB-4703 fix #2, A+B) ------------------

    def _write_profile(self, base: Path, profile_id: str, servers: dict) -> None:
        """Write a profile-scoped mcp.json under ``base/profiles/<id>/``."""
        profile_dir = base / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        (profile_dir / "mcp.json").write_text(
            json.dumps({"servers": servers}), encoding="utf-8"
        )

    def _configs_by_path(self, configs) -> dict:
        """Map each returned config's path -> set of its server names."""
        return {
            cfg["path"]: {s["name"] for s in cfg["mcpServers"]}
            for cfg in configs
        }

    def test_default_only_unchanged_single_config_and_path(self):
        """Only the default mcp.json ⇒ exactly one config at the base path."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))
        self.assertIn("serena", {s["name"] for s in configs[0]["mcpServers"]})

    def test_named_profile_surfaces_with_profile_dir_path(self):
        """Default + one named profile ⇒ two configs; the profile config is
        attributed to its own ``profiles/<id>`` dir."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self._write_profile(self.code_user_base, "abc123", {"github": {"url": "https://x/mcp"}})

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(len(by_path), 2)
        self.assertEqual(by_path[str(self.code_user_base)], {"serena"})
        profile_path = str(self.code_user_base / "profiles" / "abc123")
        self.assertIn(profile_path, by_path)
        self.assertEqual(by_path[profile_path], {"github"})

    def test_multiple_profiles_each_separate_config(self):
        """Two named profiles each surface as a separate config keyed by their
        own profile-dir path (order-independent comparison)."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        self._write_profile(self.code_user_base, "p1", {"alpha": {"command": "a"}})
        self._write_profile(self.code_user_base, "p2", {"beta": {"command": "b"}})

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(
            by_path,
            {
                str(self.code_user_base): {"serena"},
                str(self.code_user_base / "profiles" / "p1"): {"alpha"},
                str(self.code_user_base / "profiles" / "p2"): {"beta"},
            },
        )

    def test_absent_profiles_dir_no_crash(self):
        """Default present, no profiles/ dir ⇒ no crash, single default config."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_profile_dir_exists_but_no_mcp_json_ignored(self):
        """An empty ``profiles/empty/`` dir is ignored; only the default surfaces."""
        self._write_primary(json.dumps({"servers": {"serena": {"command": "uvx"}}}))
        (self.code_user_base / "profiles" / "empty").mkdir(parents=True)
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.code_user_base))

    def test_insiders_default_mcp_surfaces_with_insiders_path(self):
        """Default mcp.json under the Insiders base ONLY ⇒ one config attributed
        to the Insiders base."""
        self.insiders_base.mkdir(parents=True)
        (self.insiders_base / "mcp.json").write_text(
            json.dumps({"servers": {"serena": {"command": "uvx"}}}), encoding="utf-8"
        )
        configs = self._extract()
        self.assertEqual(len(configs), 1)
        self.assertEqual(configs[0]["path"], str(self.insiders_base))
        self.assertIn("serena", {s["name"] for s in configs[0]["mcpServers"]})

    def test_stable_and_insiders_both_surface(self):
        """Default mcp.json in BOTH stable and Insiders ⇒ two configs, each
        attributed to its own base path."""
        self._write_primary(json.dumps({"servers": {"stable_srv": {"command": "s"}}}))
        self.insiders_base.mkdir(parents=True)
        (self.insiders_base / "mcp.json").write_text(
            json.dumps({"servers": {"insiders_srv": {"command": "i"}}}), encoding="utf-8"
        )

        by_path = self._configs_by_path(self._extract())
        self.assertEqual(
            by_path,
            {
                str(self.code_user_base): {"stable_srv"},
                str(self.insiders_base): {"insiders_srv"},
            },
        )

    def test_insiders_profile_surfaces(self):
        """A profile under the Insiders base surfaces with its Insiders
        profile-dir path."""
        self.insiders_base.mkdir(parents=True)
        self._write_profile(self.insiders_base, "x", {"gamma": {"command": "g"}})

        by_path = self._configs_by_path(self._extract())
        profile_path = str(self.insiders_base / "profiles" / "x")
        self.assertIn(profile_path, by_path)
        self.assertEqual(by_path[profile_path], {"gamma"})


class TestEnumerateVscodeMcpFiles(unittest.TestCase):
    """OS-agnostic contract tests for ``enumerate_vscode_mcp_files``."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()
        self.base = Path(self.tmp_dir) / "User"
        self.base.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_default(self) -> Path:
        default_file = self.base / "mcp.json"
        default_file.write_text("{}", encoding="utf-8")
        return default_file

    def _write_profile(self, profile_id: str) -> Path:
        profile_dir = self.base / "profiles" / profile_id
        profile_dir.mkdir(parents=True, exist_ok=True)
        profile_file = profile_dir / "mcp.json"
        profile_file.write_text("{}", encoding="utf-8")
        return profile_file

    def test_default_only_returns_default_file(self):
        default_file = self._write_default()
        self.assertEqual(enumerate_vscode_mcp_files(self.base), [default_file])

    def test_default_plus_two_profiles_sorted_order(self):
        default_file = self._write_default()
        # Write out of order to prove sorting.
        p_b = self._write_profile("bbb")
        p_a = self._write_profile("aaa")
        self.assertEqual(
            enumerate_vscode_mcp_files(self.base),
            [default_file, p_a, p_b],
        )

    def test_nonexistent_base_returns_empty(self):
        missing = Path(self.tmp_dir) / "does_not_exist" / "User"
        self.assertEqual(enumerate_vscode_mcp_files(missing), [])

    def test_empty_profiles_dir_returns_just_default(self):
        default_file = self._write_default()
        (self.base / "profiles").mkdir()
        self.assertEqual(enumerate_vscode_mcp_files(self.base), [default_file])


class TestMacOSGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = MacOSGitHubCopilotMCPConfigExtractor
    code_user_relpath = ("Library", "Application Support", "Code", "User")


class TestLinuxGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = LinuxGitHubCopilotMCPConfigExtractor
    code_user_relpath = (".config", "Code", "User")


class TestWindowsGitHubCopilotVscodeMcpJsonc(
    _GitHubCopilotVscodeMcpMixin, unittest.TestCase
):
    extractor_cls = WindowsGitHubCopilotMCPConfigExtractor
    code_user_relpath = ("AppData", "Roaming", "Code", "User")


if __name__ == "__main__":
    unittest.main()
