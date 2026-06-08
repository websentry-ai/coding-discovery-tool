"""
JSONC + dead-fallback tests for the GitHub Copilot VS Code MCP extractors
(WEB-4703, fixes #1 and #3).

Fix #1: the VS Code ``Code/User/mcp.json`` is JSONC in practice (VS Code lets
users add // and /* */ comments and trailing commas). The github_copilot
extractors previously called ``json.loads`` on the raw text, so any commented or
trailing-comma config raised JSONDecodeError and surfaced ZERO servers. They now
strip JSONC comments and trailing commas first (the same proven path the Copilot
CLI extractor already used).

Fix #3: the dead ``globalStorage/ms-vscode.vscode-github-copilot/mcp.json``
fallback branch was removed. VS Code Copilot only ever reads the primary
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

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_primary(self, text: str) -> None:
        self.primary_path.write_text(text, encoding="utf-8")

    def _extract(self):
        return self.extractor._extract_vscode_configs_for_user(self.user_home)

    def _server_names(self, configs) -> set:
        self.assertEqual(len(configs), 1)
        return {s["name"] for s in configs[0]["mcpServers"]}

    # -- Fix #1: JSONC tolerance -------------------------------------------

    def test_commented_mcp_json_surfaces_server(self):
        """// and /* */ comments must be stripped (was 0 servers before Fix #1)."""
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
        """A hand-edited trailing comma must parse (was 0 servers before Fix #1)."""
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

    # -- Fix #3: primary works, dead fallback gone -------------------------

    def test_primary_only_surfaces_and_returns_code_user_base(self):
        """Only the primary Code/User/mcp.json present → servers surface and the
        returned path is the Code/User base (Fix #3 primary still works)."""
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
