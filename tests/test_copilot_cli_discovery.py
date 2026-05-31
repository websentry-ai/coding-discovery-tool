"""
Integration tests for GitHub Copilot CLI discovery (macOS).

The GitHub Copilot CLI (``@github/copilot``) is its own product, distinct from
the GitHub Copilot VS Code extension / JetBrains plugin. These tests exercise
the outermost surfaces:

  - The detector's ``detect()`` / ``detect_all_tools()`` (the live ``main()``
    path scopes detection per-user by setting ``detector.user_home``).
  - The MCP extractor's per-user extraction from ``~/.copilot/mcp-config.json``.
  - ``AIToolsDetector.process_single_tool`` routing — the exact-match CLI branch
    must win over the ``"github copilot"`` substring (IDE) branch.

Conventions mirror the existing suite: temp HOME dirs, the globally-stubbed MCP
scanner (``tests/__init__.py`` patches ``_scan_servers_in_mapping`` -> {}), and
``_SENTRY_DSN`` forced empty to prevent real Sentry calls.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.coding_tool_factory import (
    CopilotCliMCPConfigExtractorFactory,
    ToolDetectorFactory,
)
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli import (
    MacOSCopilotCliDetector,
    _parse_cli_version,
    _resolve_copilot_dir,
)
from scripts.coding_discovery_tools.macos.copilot_cli.mcp_config_extractor import (
    MacOSCopilotCliMCPConfigExtractor,
    _extract_servers_obj,
    _strip_jsonc_comments,
    _strip_trailing_commas,
)
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_rules_extractor import (
    MacOSCopilotCliRulesExtractor,
)
from scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli import (
    WindowsCopilotCliDetector,
)
from scripts.coding_discovery_tools.windows.copilot_cli.mcp_config_extractor import (
    WindowsCopilotCliMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli_rules_extractor import (
    WindowsCopilotCliRulesExtractor,
)
from scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_settings_extractor import (
    MacOSCopilotCliSettingsExtractor,
)
from scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli_settings_extractor import (
    WindowsCopilotCliSettingsExtractor,
)

# Module path for patching the detector's root-scan helpers.
_DETECTOR_MOD = "scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli"
# Module path for patching the Windows detector's admin/all-users scan.
_WIN_DETECTOR_MOD = "scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli"
# Module path for patching the rules extractor's shared helpers.
_RULES_MOD = "scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_rules_extractor"
# Module path for patching the Windows rules extractor's OS-specific seams.
_WIN_RULES_MOD = "scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli_rules_extractor"
# Module paths for patching the settings extractor's seams.
_SETTINGS_MOD = "scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli_settings_extractor"
_WIN_SETTINGS_MOD = "scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli_settings_extractor"

# The backend's ALLOWED_RULE_FIELDS — any rule dict carrying a key outside this
# set is silently dropped whole by ingestion, so every rule we emit MUST be a
# subset of these. Notably, no frontmatter keys (applyTo / excludeAgent).
_ALLOWED_RULE_FIELDS = {
    "file_path", "file_name", "content", "size",
    "last_modified", "truncated", "scope", "project_path",
}


# ---------------------------------------------------------------------------
# 1. Detection: marker variants + negatives
# ---------------------------------------------------------------------------

class TestCopilotCliDetection(unittest.TestCase):
    """Per-user detection of ~/.copilot via the union marker set."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = MacOSCopilotCliDetector()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.user_home.mkdir(parents=True)
        # Scope detection to this single user (the live per-user path).
        self.detector.user_home = self.user_home

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_copilot_dir(self) -> Path:
        copilot_dir = self.user_home / ".copilot"
        copilot_dir.mkdir(parents=True)
        return copilot_dir

    def test_no_copilot_dir_not_detected(self):
        """No ~/.copilot at all -> not detected."""
        self.assertIsNone(self.detector.detect())

    def test_empty_copilot_dir_not_detected(self):
        """~/.copilot present but empty -> not detected (no bare-dir gating)."""
        self._make_copilot_dir()
        self.assertIsNone(self.detector.detect())

    def test_settings_json_marker_detected(self):
        """Current CLI layout uses settings.json."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "GitHub Copilot CLI")
        self.assertEqual(result["publisher"], "GitHub")
        self.assertEqual(result["install_path"], str(copilot_dir))

    def test_config_json_marker_detected(self):
        """Older CLI layout uses config.json."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "config.json").write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.detector.detect())

    def test_mcp_config_marker_detected(self):
        """mcp-config.json alone is a sufficient signal."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "mcp-config.json").write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.detector.detect())

    def test_lsp_config_marker_detected(self):
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "lsp-config.json").write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.detector.detect())

    def test_session_state_dir_marker_detected(self):
        """A marker directory (session-state) is a sufficient signal."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "session-state").mkdir()
        self.assertIsNotNone(self.detector.detect())

    def test_logs_dir_marker_detected(self):
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "logs").mkdir()
        self.assertIsNotNone(self.detector.detect())

    def test_installed_plugins_dir_marker_detected(self):
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "installed-plugins").mkdir()
        self.assertIsNotNone(self.detector.detect())

    def test_command_history_state_dir_marker_detected(self):
        """Documented dir name is 'command-history-state' (not 'history-session-state')."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "command-history-state").mkdir()
        self.assertIsNotNone(self.detector.detect())

    def test_skills_dir_marker_detected(self):
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "skills").mkdir()
        self.assertIsNotNone(self.detector.detect())

    def test_permissions_config_marker_detected(self):
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "permissions-config.json").write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.detector.detect())

    def test_unrelated_file_not_detected(self):
        """A ~/.copilot holding only unknown junk is not a CLI install."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "random.txt").write_text("hello", encoding="utf-8")
        self.assertIsNone(self.detector.detect())

    def test_marker_file_present_as_dir_does_not_count(self):
        """A marker *file* name present as a directory is not a file signal."""
        copilot_dir = self._make_copilot_dir()
        # settings.json as a directory should not satisfy the file marker, and
        # it is not in the dir-marker set either -> not detected.
        (copilot_dir / "settings.json").mkdir()
        self.assertIsNone(self.detector.detect())

    def test_detect_returns_unknown_version_when_binary_absent(self):
        """version falls back to 'unknown' when `copilot --version` yields nothing."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        with patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertEqual(result["version"], "unknown")

    def test_detect_all_tools_with_user_home_arg(self):
        """detect_all_tools(user_home=...) scopes to that user and returns a list."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        fresh = MacOSCopilotCliDetector()
        results = fresh.detect_all_tools(user_home=str(self.user_home))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["install_path"], str(copilot_dir))


# ---------------------------------------------------------------------------
# 2. Root all-users scan -> per-user rows with distinct install_path
# ---------------------------------------------------------------------------

class TestCopilotCliRootAllUsers(unittest.TestCase):
    """When running as root, every qualifying user yields a distinct row."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.users_dir = Path(self.tmp_dir) / "Users"
        self.users_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _add_user(self, name: str, with_marker: bool = True) -> Path:
        copilot_dir = self.users_dir / name / ".copilot"
        copilot_dir.mkdir(parents=True)
        if with_marker:
            (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        return copilot_dir

    def _run_root_scan(self) -> list:
        """Detect as root with /Users redirected to our temp tree."""
        real_path = Path
        users_dir = self.users_dir

        def _path_side_effect(arg):
            if str(arg) == "/Users":
                return users_dir
            return real_path(arg)

        detector = MacOSCopilotCliDetector()
        detector.user_home = None  # force the all-users branch
        with patch(f"{_DETECTOR_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_DETECTOR_MOD}.Path") as mock_path:
            mock_path.side_effect = _path_side_effect
            return detector._detect_all_users()

    def test_two_users_yield_two_distinct_rows(self):
        alice = self._add_user("alice")
        bob = self._add_user("bob")
        results = self._run_root_scan()
        install_paths = sorted(r["install_path"] for r in results)
        self.assertEqual(install_paths, sorted([str(alice), str(bob)]))
        # Distinct install_path is what keeps main()'s "name:path" dedup key unique.
        self.assertEqual(len(set(install_paths)), 2)

    def test_empty_user_dir_excluded_from_root_scan(self):
        self._add_user("alice")
        self._add_user("carol", with_marker=False)  # empty ~/.copilot -> excluded
        results = self._run_root_scan()
        names = {Path(r["install_path"]).parent.name for r in results}
        self.assertIn("alice", names)
        self.assertNotIn("carol", names)

    def test_hidden_user_dir_skipped(self):
        self._add_user("alice")
        self._add_user(".hidden")  # dotted dir -> skipped by the scanner
        results = self._run_root_scan()
        parents = {Path(r["install_path"]).parent.name for r in results}
        self.assertNotIn(".hidden", parents)

    def test_all_rows_named_github_copilot_cli(self):
        self._add_user("alice")
        self._add_user("bob")
        results = self._run_root_scan()
        self.assertTrue(all(r["name"] == "GitHub Copilot CLI" for r in results))


# ---------------------------------------------------------------------------
# 3. MCP extraction: wrapped / flat / JSONC + malformed / empty (no phantom)
# ---------------------------------------------------------------------------

class TestCopilotCliMcpExtraction(unittest.TestCase):
    """_extract_cli_configs_for_user reads ~/.copilot/mcp-config.json."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliMCPConfigExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.copilot_dir = self.user_home / ".copilot"
        self.copilot_dir.mkdir(parents=True)
        self.config_path = self.copilot_dir / "mcp-config.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _server_names(self, configs) -> set:
        self.assertEqual(len(configs), 1)
        return {s["name"] for s in configs[0]["mcpServers"]}

    def test_wrapped_mcpServers_surfaces_serena(self):
        self.config_path.write_text(json.dumps({
            "mcpServers": {
                "serena": {"command": "uvx", "args": ["--from", "serena", "serena"]},
            }
        }), encoding="utf-8")
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))
        self.assertEqual(configs[0]["path"], str(self.copilot_dir))

    def test_servers_key_surfaces_serena(self):
        """mcp-config.json may use the 'servers' key (VS Code-style)."""
        self.config_path.write_text(json.dumps({
            "servers": {"serena": {"command": "uvx"}},
        }), encoding="utf-8")
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_flat_top_level_surfaces_serena(self):
        """GitHub CLI accepts the unwrapped Claude-style form (flat top-level)."""
        self.config_path.write_text(json.dumps({
            "serena": {"command": "uvx", "args": ["serena"]},
        }), encoding="utf-8")
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_jsonc_comments_stripped_and_serena_surfaces(self):
        """Comments (// and /* */) must be tolerated."""
        self.config_path.write_text(
            "{\n"
            "  // global MCP servers for the Copilot CLI\n"
            '  "mcpServers": {\n'
            '    "serena": { "command": "uvx", "args": ["serena"] } /* the serena server */\n'
            "  }\n"
            "}\n",
            encoding="utf-8",
        )
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_object_trailing_comma_surfaces_serena(self):
        """A hand-edited trailing comma after the mcpServers object must parse (P1)."""
        self.config_path.write_text(
            '{"mcpServers":{"serena":{"command":"uvx"}},}',
            encoding="utf-8",
        )
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_array_trailing_comma_in_args_parses(self):
        """A trailing comma inside an args array must parse (P1)."""
        self.config_path.write_text(
            '{"mcpServers":{"serena":{"command":"uvx","args":["--from","serena",]}}}',
            encoding="utf-8",
        )
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_comment_and_trailing_comma_combined_surfaces_serena(self):
        """Comments AND a trailing comma together (the real-world hand-edit) parse."""
        self.config_path.write_text(
            "{\n"
            '  "mcpServers": {\n'
            '    "serena": { "command": "uvx" }, // serena\n'
            "  },\n"
            "}\n",
            encoding="utf-8",
        )
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_multiple_servers_all_surface(self):
        self.config_path.write_text(json.dumps({
            "mcpServers": {
                "serena": {"command": "uvx"},
                "github": {"url": "https://api.githubcopilot.com/mcp/"},
            }
        }), encoding="utf-8")
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertEqual(self._server_names(configs), {"serena", "github"})

    def test_malformed_json_no_crash_no_results(self):
        self.config_path.write_text("{ this is not valid json {{{", encoding="utf-8")
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])

    def test_empty_servers_no_phantom_project(self):
        """A parseable-but-serverless config must not surface a project."""
        self.config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])

    def test_missing_config_file_no_results(self):
        self.config_path.unlink(missing_ok=True)
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])

    def test_non_object_json_no_results(self):
        """A top-level JSON array (not an object) must not crash."""
        self.config_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])


class TestCopilotCliMcpHelpers(unittest.TestCase):
    """Direct checks on the JSONC stripper and server-object resolver."""

    def test_jsonc_preserves_url_with_double_slash_in_string(self):
        raw = '{"servers": {"s": {"url": "https://host//path"}}}'
        parsed = json.loads(_strip_jsonc_comments(raw))
        self.assertEqual(parsed["servers"]["s"]["url"], "https://host//path")

    def test_jsonc_strips_line_and_block_comments(self):
        raw = '{ // line\n "a": 1, /* block\n spanning */ "b": 2 }'
        parsed = json.loads(_strip_jsonc_comments(raw))
        self.assertEqual(parsed, {"a": 1, "b": 2})

    def test_extract_servers_prefers_mcpServers(self):
        obj = {"mcpServers": {"a": {}}, "servers": {"b": {}}, "c": {}}
        self.assertEqual(set(_extract_servers_obj(obj)), {"a"})

    def test_extract_servers_falls_back_to_servers(self):
        self.assertEqual(set(_extract_servers_obj({"servers": {"b": {}}})), {"b"})

    def test_extract_servers_flat_ignores_scalars(self):
        obj = {"serena": {"command": "x"}, "version": 1, "enabled": True}
        self.assertEqual(set(_extract_servers_obj(obj)), {"serena"})

    def test_extract_servers_flat_ignores_non_server_objects(self):
        """Flat-form dict values without command/url (e.g. a VS Code 'inputs' block) aren't servers."""
        obj = {
            "serena": {"command": "uvx"},
            "inputs": {"id": "token", "type": "promptString"},
        }
        self.assertEqual(set(_extract_servers_obj(obj)), {"serena"})

    def test_strip_trailing_commas_object(self):
        """Trailing comma before } is removed and the result parses."""
        parsed = json.loads(_strip_trailing_commas('{"mcpServers":{"serena":{"command":"uvx"}},}'))
        self.assertEqual(parsed, {"mcpServers": {"serena": {"command": "uvx"}}})

    def test_strip_trailing_commas_array(self):
        """Trailing comma before ] is removed and the result parses."""
        parsed = json.loads(_strip_trailing_commas('{"args":["a","b",]}'))
        self.assertEqual(parsed["args"], ["a", "b"])

    def test_strip_trailing_commas_preserves_comma_inside_string(self):
        """CRITICAL regression: a comma inside a string value must NOT be dropped (P1)."""
        raw = '{"mcpServers":{"s":{"command":"x","args":["a,"]}}}'
        parsed = json.loads(_strip_trailing_commas(raw))
        self.assertEqual(parsed["mcpServers"]["s"]["args"], ["a,"])

    def test_strip_trailing_commas_preserves_closer_inside_string(self):
        """A } or ] inside a string must not be mistaken for a real closer."""
        raw = '{"mcpServers":{"s":{"command":"echo","args":["},"]}}}'
        parsed = json.loads(_strip_trailing_commas(raw))
        self.assertEqual(parsed["mcpServers"]["s"]["args"], ["},"])

    def test_strip_trailing_commas_noop_on_valid_json(self):
        """Valid JSON without trailing commas is returned byte-identical."""
        raw = '{"mcpServers":{"serena":{"command":"uvx","args":["x"]}}}'
        self.assertEqual(_strip_trailing_commas(raw), raw)


# ---------------------------------------------------------------------------
# 4. Routing-order regression guard (process_single_tool)
# ---------------------------------------------------------------------------

class TestCopilotCliRouting(unittest.TestCase):
    """The exact-match CLI branch must win over the 'github copilot' substring."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")

    def test_cli_tool_routed_to_cli_branch_not_ide(self):
        tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }
        sentinel = {"routed": "copilot-cli"}
        with patch.object(
            self.detector, "_process_copilot_cli_tool", return_value=sentinel
        ) as cli_branch, \
             patch.object(self.detector, "_github_copilot_mcp_extractor") as ide_mcp, \
             patch.object(self.detector, "_github_copilot_rules_extractor") as ide_rules:
            result = self.detector.process_single_tool(tool)

        self.assertEqual(result, sentinel)
        self.assertEqual(cli_branch.call_count, 1)
        # The IDE Copilot extractors must NOT be touched for the CLI tool.
        self.assertEqual(ide_mcp.extract_mcp_config.call_count, 0)
        self.assertEqual(ide_rules.extract_all_github_copilot_rules.call_count, 0)

    def test_ide_copilot_not_routed_to_cli_branch(self):
        """An IDE Copilot row must still take the substring (IDE) branch."""
        tool = {
            "name": "GitHub Copilot (VS Code)",
            "version": "1.0",
            "install_path": "/x",
        }
        self.detector._github_copilot_rules_extractor = MagicMock()
        self.detector._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = []
        self.detector._github_copilot_mcp_extractor = MagicMock()
        self.detector._github_copilot_mcp_extractor.extract_mcp_config.return_value = None

        with patch.object(
            self.detector, "_process_copilot_cli_tool", return_value={"routed": "cli"}
        ) as cli_branch:
            result = self.detector.process_single_tool(tool)

        self.assertEqual(cli_branch.call_count, 0)
        self.assertEqual(result["name"], "GitHub Copilot (VS Code)")

    def test_cli_branch_surfaces_servers_into_projects(self):
        """End-to-end: a real server config yields one project on the tool dict."""
        tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = {
            "projects": [
                {"path": "/Users/x/.copilot", "mcpServers": [{"name": "serena"}]},
            ]
        }
        # Isolate the MCP-only assertion from the real on-disk rules walk.
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        result = self.detector.process_single_tool(tool)
        self.assertEqual(len(result["projects"]), 1)
        self.assertEqual(result["projects"][0]["mcpServers"][0]["name"], "serena")
        self.assertEqual(result["name"], "GitHub Copilot CLI")

    def test_cli_branch_filters_empty_project(self):
        """A serverless extraction must not emit a phantom project row."""
        tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        # Even if a project dict with empty servers leaks through, it is dropped.
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = {
            "projects": [{"path": "/Users/x/.copilot", "mcpServers": []}]
        }
        # Isolate the empty-project assertion from the real on-disk rules walk.
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        result = self.detector.process_single_tool(tool)
        self.assertEqual(result["projects"], [])
        self.assertEqual(result["version"], "0.0.1")

    def test_cli_branch_no_mcp_config_yields_no_projects(self):
        tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        # Isolate the no-projects assertion from the real on-disk rules walk.
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        result = self.detector.process_single_tool(tool)
        self.assertEqual(result["projects"], [])

    def test_windows_origin_cli_tool_routed_to_cli_branch(self):
        """Routing keys on the tool NAME, which the Windows detector shares, so a
        Windows-origin row (same name, C:\\Users path) takes the CLI branch too —
        no OS-specific routing change is needed."""
        win_detector = AIToolsDetector(os_name="Windows")
        tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "C:\\Users\\x\\.copilot",
        }
        sentinel = {"routed": "copilot-cli"}
        with patch.object(
            win_detector, "_process_copilot_cli_tool", return_value=sentinel
        ) as cli_branch:
            result = win_detector.process_single_tool(tool)
        self.assertEqual(result, sentinel)
        self.assertEqual(cli_branch.call_count, 1)


# ---------------------------------------------------------------------------
# 5. Factory wiring
# ---------------------------------------------------------------------------

class TestCopilotCliFactoryWiring(unittest.TestCase):
    """Factory produces the CLI detector/extractor on macOS and None elsewhere."""

    def test_detector_created_on_darwin(self):
        det = ToolDetectorFactory.create_copilot_cli_detector("Darwin")
        self.assertIsInstance(det, MacOSCopilotCliDetector)
        self.assertEqual(det.tool_name, "GitHub Copilot CLI")

    def test_detector_created_on_windows(self):
        det = ToolDetectorFactory.create_copilot_cli_detector("Windows")
        self.assertIsInstance(det, WindowsCopilotCliDetector)
        self.assertEqual(det.tool_name, "GitHub Copilot CLI")

    def test_detector_none_on_non_supported_os(self):
        self.assertIsNone(ToolDetectorFactory.create_copilot_cli_detector("Linux"))

    def test_extractor_created_on_darwin(self):
        ext = CopilotCliMCPConfigExtractorFactory.create("Darwin")
        self.assertIsInstance(ext, MacOSCopilotCliMCPConfigExtractor)

    def test_extractor_created_on_windows(self):
        ext = CopilotCliMCPConfigExtractorFactory.create("Windows")
        self.assertIsInstance(ext, WindowsCopilotCliMCPConfigExtractor)
        # DRY: the Windows extractor IS-A macOS extractor (shared parser).
        self.assertIsInstance(ext, MacOSCopilotCliMCPConfigExtractor)

    def test_extractor_none_on_non_supported_os(self):
        self.assertIsNone(CopilotCliMCPConfigExtractorFactory.create("Linux"))

    def test_cli_detector_registered_in_all_detectors_darwin(self):
        detectors = ToolDetectorFactory.create_all_tool_detectors("Darwin")
        names = [d.tool_name for d in detectors]
        self.assertIn("GitHub Copilot CLI", names)
        # The IDE Copilot detector remains a separate row.
        self.assertIn("GitHub Copilot", names)

    def test_cli_detector_registered_in_all_detectors_windows(self):
        detectors = ToolDetectorFactory.create_all_tool_detectors("Windows")
        cli = [d for d in detectors if d.tool_name == "GitHub Copilot CLI"]
        self.assertEqual(len(cli), 1)
        self.assertIsInstance(cli[0], WindowsCopilotCliDetector)
        # The IDE Copilot detector remains a separate row.
        self.assertIn("GitHub Copilot", [d.tool_name for d in detectors])


# ---------------------------------------------------------------------------
# 6. Windows detection: per-user marker gate (mirrors the macOS detection tests)
# ---------------------------------------------------------------------------

class TestWindowsCopilotCliDetection(unittest.TestCase):
    """Per-user detection of ~/.copilot on Windows via the inherited marker set.

    The Windows detector subclasses the macOS one and only overrides the
    all-users scan, so the per-user path (marker gate, _detect_for_user, version
    fallback) is exercised here to prove the inheritance wiring holds.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = WindowsCopilotCliDetector()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.user_home.mkdir(parents=True)
        # Scope detection to this single user (the live per-user path).
        self.detector.user_home = self.user_home

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_copilot_dir(self) -> Path:
        copilot_dir = self.user_home / ".copilot"
        copilot_dir.mkdir(parents=True)
        return copilot_dir

    def test_no_copilot_dir_not_detected(self):
        """No ~/.copilot at all -> not detected."""
        self.assertIsNone(self.detector.detect())

    def test_empty_copilot_dir_not_detected(self):
        """~/.copilot present but empty -> not detected (no bare-dir gating)."""
        self._make_copilot_dir()
        self.assertIsNone(self.detector.detect())

    def test_settings_json_marker_detected(self):
        """Current CLI layout uses settings.json; row carries the GitHub publisher."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "GitHub Copilot CLI")
        self.assertEqual(result["publisher"], "GitHub")
        self.assertEqual(result["install_path"], str(copilot_dir))

    def test_unrelated_file_not_detected(self):
        """A ~/.copilot holding only unknown junk is not a CLI install."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "random.txt").write_text("hello", encoding="utf-8")
        self.assertIsNone(self.detector.detect())

    def test_detect_returns_unknown_version_when_binary_absent(self):
        """version falls back to 'unknown' when `copilot --version` yields nothing."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        with patch.object(self.detector, "get_version", return_value=None):
            result = self.detector.detect()
        self.assertEqual(result["version"], "unknown")

    def test_detect_all_tools_with_user_home_arg(self):
        """detect_all_tools(user_home=...) scopes to that user and returns a list."""
        copilot_dir = self._make_copilot_dir()
        (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        fresh = WindowsCopilotCliDetector()
        results = fresh.detect_all_tools(user_home=str(self.user_home))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["install_path"], str(copilot_dir))

    def test_get_version_uses_shell_true_and_parses_semver(self):
        """Windows get_version uses shell=True (npm .cmd shim) AND parses the bare
        semver out of the multi-line banner — not the raw ~70-char string that
        would overflow the backend version column."""
        banner = "GitHub Copilot CLI 0.0.399.\nRun 'copilot update' to check for updates."
        fake = MagicMock(returncode=0, stdout=banner, stderr="")
        with patch(f"{_WIN_DETECTOR_MOD}.subprocess.run", return_value=fake) as run:
            version = WindowsCopilotCliDetector().get_version()
        self.assertEqual(version, "0.0.399")
        self.assertIs(run.call_args.kwargs.get("shell"), True)

    def test_get_version_returns_none_on_failure(self):
        """A failed/absent binary yields None (caller falls back to 'unknown'), never raises."""
        with patch(f"{_WIN_DETECTOR_MOD}.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(WindowsCopilotCliDetector().get_version())


class TestCopilotCliConfigDirResolution(unittest.TestCase):
    """_resolve_copilot_dir honors COPILOT_HOME for the running user, else defaults."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_default_when_copilot_home_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COPILOT_HOME", None)
            home = Path.home()
            self.assertEqual(_resolve_copilot_dir(home), home / ".copilot")

    def test_copilot_home_used_for_current_user(self):
        with patch.dict(os.environ, {"COPILOT_HOME": "/custom/copilot-cfg"}, clear=False):
            self.assertEqual(_resolve_copilot_dir(Path.home()), Path("/custom/copilot-cfg"))

    def test_copilot_home_ignored_for_other_user(self):
        """COPILOT_HOME reflects only the running user's env — don't apply it to others."""
        with patch.dict(os.environ, {"COPILOT_HOME": "/custom/copilot-cfg"}, clear=False):
            other = Path("/Users/some-other-user")
            self.assertEqual(_resolve_copilot_dir(other), other / ".copilot")

    def test_blank_copilot_home_falls_back_to_default(self):
        with patch.dict(os.environ, {"COPILOT_HOME": "   "}, clear=False):
            home = Path.home()
            self.assertEqual(_resolve_copilot_dir(home), home / ".copilot")


class TestCopilotCliVersionParse(unittest.TestCase):
    """_parse_cli_version turns the raw `copilot --version` banner into a clean semver.

    Regression for the backend `varchar(50)` overflow: the raw multi-line banner
    is ~70 chars and breaks ingestion; the parsed value must be a short semver.
    """

    def test_parses_semver_from_multiline_banner(self):
        raw = "GitHub Copilot CLI 0.0.399.\nRun 'copilot update' to check for updates."
        self.assertEqual(_parse_cli_version(raw), "0.0.399")

    def test_parses_prerelease_suffix(self):
        self.assertEqual(_parse_cli_version("copilot 1.2.3-beta.4"), "1.2.3-beta.4")

    def test_none_and_empty_return_none(self):
        self.assertIsNone(_parse_cli_version(None))
        self.assertIsNone(_parse_cli_version(""))
        self.assertIsNone(_parse_cli_version("   \n  "))

    def test_fallback_first_line_capped_when_no_semver(self):
        result = _parse_cli_version("x" * 80 + "\nsecond line")
        self.assertEqual(result, "x" * 50)

    def test_parsed_value_fits_backend_version_column(self):
        raw = "GitHub Copilot CLI 0.0.399.\nRun 'copilot update' to check for updates."
        self.assertLessEqual(len(_parse_cli_version(raw)), 50)

    def test_macos_get_version_parses_via_run_command(self):
        banner = "GitHub Copilot CLI 0.0.399.\nRun 'copilot update' to check for updates."
        with patch(f"{_DETECTOR_MOD}.run_command", return_value=banner):
            self.assertEqual(MacOSCopilotCliDetector().get_version(), "0.0.399")


# ---------------------------------------------------------------------------
# 7. Windows admin all-users scan -> per-user rows with distinct install_path
# ---------------------------------------------------------------------------

class TestWindowsCopilotCliAdminAllUsers(unittest.TestCase):
    """When running as admin, every qualifying C:\\Users user yields a distinct row.

    Mirrors the macOS root all-users test, but patches the Windows module's
    ``is_running_as_admin`` and the ``C:\\Users`` ``Path`` lookup so it runs on
    this macOS dev box.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.users_dir = Path(self.tmp_dir) / "Users"
        self.users_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _add_user(self, name: str, with_marker: bool = True) -> Path:
        copilot_dir = self.users_dir / name / ".copilot"
        copilot_dir.mkdir(parents=True)
        if with_marker:
            (copilot_dir / "settings.json").write_text("{}", encoding="utf-8")
        return copilot_dir

    def _run_admin_scan(self) -> list:
        """Detect as admin with C:\\Users redirected to our temp tree."""
        real_path = Path
        users_dir = self.users_dir

        def _path_side_effect(arg):
            if str(arg) == "C:\\Users":
                return users_dir
            return real_path(arg)

        detector = WindowsCopilotCliDetector()
        detector.user_home = None  # force the all-users branch
        with patch(f"{_WIN_DETECTOR_MOD}.is_running_as_admin", return_value=True), \
             patch(f"{_WIN_DETECTOR_MOD}.Path") as mock_path:
            mock_path.side_effect = _path_side_effect
            return detector._detect_all_users()

    def test_two_users_yield_two_distinct_rows(self):
        alice = self._add_user("alice")
        bob = self._add_user("bob")
        results = self._run_admin_scan()
        install_paths = sorted(r["install_path"] for r in results)
        self.assertEqual(install_paths, sorted([str(alice), str(bob)]))
        # Distinct install_path is what keeps main()'s "name:path" dedup key unique.
        self.assertEqual(len(set(install_paths)), 2)

    def test_empty_user_dir_excluded_from_admin_scan(self):
        self._add_user("alice")
        self._add_user("carol", with_marker=False)  # empty ~/.copilot -> excluded
        results = self._run_admin_scan()
        names = {Path(r["install_path"]).parent.name for r in results}
        self.assertIn("alice", names)
        self.assertNotIn("carol", names)

    def test_hidden_user_dir_skipped(self):
        self._add_user("alice")
        self._add_user(".hidden")  # dotted dir -> skipped by the scanner
        results = self._run_admin_scan()
        parents = {Path(r["install_path"]).parent.name for r in results}
        self.assertNotIn(".hidden", parents)

    def test_all_rows_named_github_copilot_cli(self):
        self._add_user("alice")
        self._add_user("bob")
        results = self._run_admin_scan()
        self.assertTrue(all(r["name"] == "GitHub Copilot CLI" for r in results))

    def test_non_admin_checks_only_current_user(self):
        """Non-admin must NOT scan C:\\Users; it checks the current user's home."""
        self._add_user("alice")
        self._add_user("bob")
        with patch(f"{_WIN_DETECTOR_MOD}.is_running_as_admin", return_value=False):
            detector = WindowsCopilotCliDetector()
            detector.user_home = None
            results = detector._detect_all_users()
        # The temp C:\Users tree is never consulted (not patched in); the real
        # current-user home almost certainly has no ~/.copilot in CI -> empty.
        admin_parents = {Path(r["install_path"]).parent.name for r in results}
        self.assertNotIn("alice", admin_parents)
        self.assertNotIn("bob", admin_parents)


# ---------------------------------------------------------------------------
# 8. Windows MCP extraction via the factory (OS-agnostic parser reused)
# ---------------------------------------------------------------------------

class TestWindowsCopilotCliMcpExtraction(unittest.TestCase):
    """The Windows extractor (factory-produced) reuses the shared parser.

    Proves the DRY subclass works end-to-end: wrapped form, the trailing-comma
    fix (shared from the macOS parser), and the no-phantom-project guard all
    behave identically through the Windows type.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = CopilotCliMCPConfigExtractorFactory.create("Windows")
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.copilot_dir = self.user_home / ".copilot"
        self.copilot_dir.mkdir(parents=True)
        self.config_path = self.copilot_dir / "mcp-config.json"

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _server_names(self, configs) -> set:
        self.assertEqual(len(configs), 1)
        return {s["name"] for s in configs[0]["mcpServers"]}

    def test_factory_returns_windows_extractor(self):
        self.assertIsInstance(self.extractor, WindowsCopilotCliMCPConfigExtractor)

    def test_wrapped_mcpServers_surfaces_serena(self):
        self.config_path.write_text(json.dumps({
            "mcpServers": {"serena": {"command": "uvx", "args": ["serena"]}},
        }), encoding="utf-8")
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))
        self.assertEqual(configs[0]["path"], str(self.copilot_dir))

    def test_trailing_comma_shared_fix_applies(self):
        """The P1 trailing-comma fix is inherited, not forked, on Windows."""
        self.config_path.write_text(
            '{"mcpServers":{"serena":{"command":"uvx"}},}', encoding="utf-8"
        )
        configs = self.extractor._extract_cli_configs_for_user(self.user_home)
        self.assertIn("serena", self._server_names(configs))

    def test_empty_servers_no_phantom_project(self):
        self.config_path.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])

    def test_malformed_json_no_crash_no_results(self):
        self.config_path.write_text("{ not valid json {{{", encoding="utf-8")
        self.assertEqual(self.extractor._extract_cli_configs_for_user(self.user_home), [])


# ---------------------------------------------------------------------------
# 9. Rules: global (user-scope) — G1, G2, COPILOT_HOME relocation
# ---------------------------------------------------------------------------

class TestCopilotCliRulesGlobal(unittest.TestCase):
    """Global (user-scope) rules under the resolved config dir.

    Exercises ``_extract_global_rules`` with ``Path.home`` redirected to a temp
    home (the current-user, non-root path). Asserts the backend field allowlist
    and that frontmatter is left verbatim inside ``content`` (never parsed out).
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.home = Path(self.tmp_dir) / "home"
        self.home.mkdir(parents=True)
        self.config_dir = self.home / ".copilot"
        self.config_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_global(self) -> dict:
        """Run global extraction as the current (non-root) user -> projects_by_root."""
        projects_by_root: dict = {}
        with patch(f"{_RULES_MOD}.Path.home", return_value=self.home), \
             patch(f"{_RULES_MOD}.is_running_as_root", return_value=False):
            self.extractor._extract_global_rules(projects_by_root)
        return projects_by_root

    def _all_rules(self, projects_by_root: dict) -> list:
        rules = []
        for items in projects_by_root.values():
            rules.extend(items)
        return rules

    def _assert_allowlisted(self, rules: list) -> None:
        for rule in rules:
            extra = set(rule.keys()) - _ALLOWED_RULE_FIELDS
            self.assertEqual(extra, set(), f"rule has non-allowlisted keys: {extra}")
            self.assertNotIn("applyTo", rule)
            self.assertNotIn("excludeAgent", rule)

    def test_g1_global_copilot_instructions_detected(self):
        """G1: <config_dir>/copilot-instructions.md is a user-scope rule."""
        (self.config_dir / "copilot-instructions.md").write_text("G1 body", encoding="utf-8")
        pbr = self._run_global()
        rules = self._all_rules(pbr)
        names = {r["file_name"] for r in rules}
        self.assertIn("copilot-instructions.md", names)
        self.assertTrue(all(r["scope"] == "user" for r in rules))
        self._assert_allowlisted(rules)

    def test_g1_grouped_under_config_dir(self):
        """Global rules group under the config dir so they coalesce with CLI MCP servers."""
        (self.config_dir / "copilot-instructions.md").write_text("G1", encoding="utf-8")
        pbr = self._run_global()
        self.assertIn(str(self.config_dir), pbr)

    def test_g1_frontmatter_left_verbatim_in_content(self):
        """CRITICAL: applyTo/excludeAgent frontmatter stays INSIDE content, not as keys."""
        body = '---\napplyTo: "**/*.py"\nexcludeAgent: ["foo"]\n---\nUse functional style.'
        (self.config_dir / "copilot-instructions.md").write_text(body, encoding="utf-8")
        rules = self._all_rules(self._run_global())
        self.assertEqual(len(rules), 1)
        rule = rules[0]
        self.assertIn("applyTo", rule["content"])  # preserved in content
        self.assertNotIn("applyTo", rule)           # NOT a dict key
        self.assertNotIn("excludeAgent", rule)
        self._assert_allowlisted(rules)

    def test_g2_instructions_tree_recursive_detected(self):
        """G2: <config_dir>/instructions/**/*.instructions.md (recursive), user scope."""
        inst = self.config_dir / "instructions"
        (inst / "sub").mkdir(parents=True)
        (inst / "top.instructions.md").write_text("top", encoding="utf-8")
        (inst / "sub" / "nested.instructions.md").write_text("nested", encoding="utf-8")
        rules = self._all_rules(self._run_global())
        names = {r["file_name"] for r in rules}
        self.assertIn("top.instructions.md", names)
        self.assertIn("nested.instructions.md", names)
        self.assertTrue(all(r["scope"] == "user" for r in rules))
        self._assert_allowlisted(rules)

    def test_g2_non_instructions_files_ignored(self):
        """Only *.instructions.md under instructions/ — a plain .md is not collected."""
        inst = self.config_dir / "instructions"
        inst.mkdir(parents=True)
        (inst / "README.md").write_text("readme", encoding="utf-8")
        (inst / "x.instructions.md").write_text("x", encoding="utf-8")
        names = {r["file_name"] for r in self._all_rules(self._run_global())}
        self.assertIn("x.instructions.md", names)
        self.assertNotIn("README.md", names)

    def test_copilot_home_relocated_global_file_found(self):
        """COPILOT_HOME relocates the whole config dir; G1 there is still found."""
        relocated = Path(self.tmp_dir) / "relocated-cfg"
        relocated.mkdir(parents=True)
        (relocated / "copilot-instructions.md").write_text("relocated G1", encoding="utf-8")
        projects_by_root: dict = {}
        with patch(f"{_RULES_MOD}.Path.home", return_value=self.home), \
             patch(f"{_RULES_MOD}.is_running_as_root", return_value=False), \
             patch.dict(os.environ, {"COPILOT_HOME": str(relocated)}, clear=False):
            self.extractor._extract_global_rules(projects_by_root)
        self.assertIn(str(relocated), projects_by_root)
        rules = self._all_rules(projects_by_root)
        self.assertEqual({r["file_name"] for r in rules}, {"copilot-instructions.md"})
        self.assertTrue(all(r["scope"] == "user" for r in rules))

    def test_no_global_rules_yields_empty(self):
        """An empty config dir contributes no rules (no phantom project)."""
        self.assertEqual(self._run_global(), {})


# ---------------------------------------------------------------------------
# 10. Rules: project (project-scope) — P1, P2, P3 (+ nested exclusion)
# ---------------------------------------------------------------------------

class TestCopilotCliRulesProject(unittest.TestCase):
    """Project-scope rules via the directory walk.

    Calls ``_walk_for_project_rules`` directly on a temp tree and stubs
    ``should_skip_system_path`` (the temp dir lives under /var, which the real
    system-skip would prune) — mirrors the established pattern in
    ``tests/test_scanning_enhancements.py``.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.root = Path(self.tmp_dir)
        self.repo = self.root / "repo"
        self.repo.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _walk(self) -> dict:
        projects_by_root: dict = {}
        with patch(f"{_RULES_MOD}.should_skip_system_path", return_value=False):
            self.extractor._walk_for_project_rules(
                self.root, self.root, projects_by_root, current_depth=0
            )
        return projects_by_root

    def _rules_for(self, projects_by_root: dict, root: Path) -> list:
        return projects_by_root.get(str(root), [])

    def _assert_allowlisted(self, projects_by_root: dict) -> None:
        for items in projects_by_root.values():
            for rule in items:
                extra = set(rule.keys()) - _ALLOWED_RULE_FIELDS
                self.assertEqual(extra, set(), f"non-allowlisted keys: {extra}")
                self.assertNotIn("applyTo", rule)
                self.assertNotIn("excludeAgent", rule)

    def test_p1_github_copilot_instructions_detected(self):
        """P1: repo .github/copilot-instructions.md -> grouped under the repo root."""
        github = self.repo / ".github"
        github.mkdir(parents=True)
        (github / "copilot-instructions.md").write_text("P1", encoding="utf-8")
        pbr = self._walk()
        names = {r["file_name"] for r in self._rules_for(pbr, self.repo)}
        self.assertIn("copilot-instructions.md", names)
        self.assertTrue(all(r["scope"] == "project" for r in self._rules_for(pbr, self.repo)))
        self._assert_allowlisted(pbr)

    def test_p2_github_instructions_tree_detected(self):
        """P2: .github/instructions/**/*.instructions.md (recursive), project scope.

        NOTE the path is .github/instructions/ — NOT .github/copilot/.
        """
        inst = self.repo / ".github" / "instructions" / "deep"
        inst.mkdir(parents=True)
        (self.repo / ".github" / "instructions" / "x.instructions.md").write_text("P2", encoding="utf-8")
        (inst / "y.instructions.md").write_text("P2 deep", encoding="utf-8")
        pbr = self._walk()
        names = {r["file_name"] for r in self._rules_for(pbr, self.repo)}
        self.assertIn("x.instructions.md", names)
        self.assertIn("y.instructions.md", names)
        self.assertTrue(all(r["scope"] == "project" for r in self._rules_for(pbr, self.repo)))
        self._assert_allowlisted(pbr)

    def test_p3_root_agent_files_detected_at_repo_root(self):
        """P3: repo-root AGENTS.md / CLAUDE.md / GEMINI.md (repo root has .git)."""
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "AGENTS.md").write_text("agents", encoding="utf-8")
        (self.repo / "CLAUDE.md").write_text("claude", encoding="utf-8")
        (self.repo / "GEMINI.md").write_text("gemini", encoding="utf-8")
        pbr = self._walk()
        names = {r["file_name"] for r in self._rules_for(pbr, self.repo)}
        self.assertEqual(names, {"AGENTS.md", "CLAUDE.md", "GEMINI.md"})
        self.assertTrue(all(r["scope"] == "project" for r in self._rules_for(pbr, self.repo)))
        self._assert_allowlisted(pbr)

    def test_p3_nested_agents_md_not_picked_up(self):
        """P3 is repo-root ONLY: a NESTED AGENTS.md (subdir, no .git) is excluded."""
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "AGENTS.md").write_text("root agents", encoding="utf-8")
        nested = self.repo / "pkg" / "sub"
        nested.mkdir(parents=True)
        (nested / "AGENTS.md").write_text("nested - must NOT appear", encoding="utf-8")
        pbr = self._walk()
        # Only the repo root contributes; the nested dir is never a project key.
        self.assertIn(str(self.repo), pbr)
        self.assertNotIn(str(nested), pbr)
        self.assertNotIn(str(self.repo / "pkg"), pbr)
        all_paths = {r["file_path"] for items in pbr.values() for r in items}
        self.assertNotIn(str(nested / "AGENTS.md"), all_paths)

    def test_p3_root_files_ignored_without_git_marker(self):
        """A bare dir holding AGENTS.md but no .git is not treated as a repo root."""
        (self.repo / "AGENTS.md").write_text("no git here", encoding="utf-8")
        pbr = self._walk()
        self.assertNotIn(str(self.repo), pbr)

    def test_p1_p2_p3_coalesce_into_single_repo_project(self):
        """All project-scope sources for one repo land under a single project_root."""
        (self.repo / ".git").mkdir(parents=True)
        github = self.repo / ".github" / "instructions"
        github.mkdir(parents=True)
        (self.repo / ".github" / "copilot-instructions.md").write_text("P1", encoding="utf-8")
        (github / "x.instructions.md").write_text("P2", encoding="utf-8")
        (self.repo / "AGENTS.md").write_text("P3", encoding="utf-8")
        pbr = self._walk()
        repo_roots = [k for k in pbr if k == str(self.repo)]
        self.assertEqual(len(repo_roots), 1)
        names = {r["file_name"] for r in self._rules_for(pbr, self.repo)}
        self.assertEqual(names, {"copilot-instructions.md", "x.instructions.md", "AGENTS.md"})

    def test_symlinked_directory_skipped_during_walk(self):
        """A symlinked subdirectory must be skipped (loop/perf guard)."""
        (self.repo / ".git").mkdir(parents=True)
        (self.repo / "AGENTS.md").write_text("root", encoding="utf-8")
        # A real sibling repo whose root files we DON'T want reached via a symlink.
        other = self.root / "other"
        (other / ".git").mkdir(parents=True)
        (other / "AGENTS.md").write_text("other", encoding="utf-8")
        link = self.repo / "linked"
        try:
            link.symlink_to(other, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported on this platform")
        pbr = self._walk()
        # The symlink under repo/ is not traversed; 'other' is still reached as a
        # top-level sibling, but never via repo/linked.
        self.assertNotIn(str(self.repo / "linked"), pbr)


# ---------------------------------------------------------------------------
# 11. Rules: env-listed dirs (E1, current user only)
# ---------------------------------------------------------------------------

class TestCopilotCliRulesEnv(unittest.TestCase):
    """COPILOT_CUSTOM_INSTRUCTIONS_DIRS contributes user-scope rules (current user)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.custom = Path(self.tmp_dir) / "team-instructions"
        (self.custom / ".github" / "instructions").mkdir(parents=True)
        (self.custom / "AGENTS.md").write_text("E1 agents", encoding="utf-8")
        (self.custom / ".github" / "instructions" / "e1.instructions.md").write_text("E1 inst", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run_env(self, value: str, as_root: bool = False) -> dict:
        projects_by_root: dict = {}
        with patch(f"{_RULES_MOD}.is_running_as_root", return_value=as_root), \
             patch.dict(os.environ, {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS": value}, clear=False):
            self.extractor._extract_env_custom_instructions(projects_by_root)
        return projects_by_root

    def _all_rules(self, pbr: dict) -> list:
        out = []
        for items in pbr.values():
            out.extend(items)
        return out

    def test_e1_dir_contributes_agents_and_instructions(self):
        pbr = self._run_env(str(self.custom))
        self.assertIn(str(self.custom), pbr)
        names = {r["file_name"] for r in self._rules_for_root(pbr)}
        self.assertEqual(names, {"AGENTS.md", "e1.instructions.md"})

    def test_e1_rules_are_user_scope_and_allowlisted(self):
        rules = self._all_rules(self._run_env(str(self.custom)))
        self.assertTrue(rules)
        for rule in rules:
            self.assertEqual(rule["scope"], "user")
            self.assertEqual(set(rule.keys()) - _ALLOWED_RULE_FIELDS, set())
            self.assertNotIn("applyTo", rule)
            self.assertNotIn("excludeAgent", rule)

    def test_e1_multiple_dirs_comma_split_and_blanks_dropped(self):
        second = Path(self.tmp_dir) / "second"
        second.mkdir(parents=True)
        (second / "AGENTS.md").write_text("second", encoding="utf-8")
        value = f" {self.custom} , , {second} "  # spaces + empty entry
        pbr = self._run_env(value)
        self.assertIn(str(self.custom), pbr)
        self.assertIn(str(second), pbr)

    def test_e1_skipped_entirely_when_running_as_root(self):
        """Another user's env isn't visible during a root scan -> E1 contributes nothing."""
        pbr = self._run_env(str(self.custom), as_root=True)
        self.assertEqual(pbr, {})

    def test_e1_unset_env_yields_nothing(self):
        projects_by_root: dict = {}
        with patch(f"{_RULES_MOD}.is_running_as_root", return_value=False), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", None)
            self.extractor._extract_env_custom_instructions(projects_by_root)
        self.assertEqual(projects_by_root, {})

    def _rules_for_root(self, pbr: dict) -> list:
        return pbr.get(str(self.custom), [])


# ---------------------------------------------------------------------------
# 12. Rules routing: rules merge into projects[].rules[] via process_single_tool
# ---------------------------------------------------------------------------

class TestCopilotCliRulesRouting(unittest.TestCase):
    """The CLI branch merges extracted rules into the tool dict's projects."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")
        self.tool = {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }

    def test_rules_surface_into_project_rules(self):
        """A rules-only project (no MCP) surfaces with its rules on the tool dict."""
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = [
            {
                "project_root": "/Users/x/.copilot",
                "rules": [{
                    "file_path": "/Users/x/.copilot/copilot-instructions.md",
                    "file_name": "copilot-instructions.md",
                    "content": "be nice",
                    "size": 7,
                    "last_modified": "2026-01-01T00:00:00Z",
                    "truncated": False,
                    "scope": "user",
                }],
            }
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertEqual(result["name"], "GitHub Copilot CLI")
        self.assertEqual(len(result["projects"]), 1)
        project = result["projects"][0]
        self.assertEqual(project["path"], "/Users/x/.copilot")
        self.assertEqual(len(project["rules"]), 1)
        self.assertEqual(project["rules"][0]["scope"], "user")
        self.assertEqual(project["mcpServers"], [])

    def test_rules_and_mcp_coalesce_under_same_project_root(self):
        """Rules + MCP servers sharing a project_root end up in ONE project entry."""
        shared = "/Users/x/.copilot"
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = {
            "projects": [{"path": shared, "mcpServers": [{"name": "serena"}]}]
        }
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = [
            {
                "project_root": shared,
                "rules": [{
                    "file_path": f"{shared}/copilot-instructions.md",
                    "file_name": "copilot-instructions.md",
                    "content": "x", "size": 1,
                    "last_modified": "2026-01-01T00:00:00Z",
                    "truncated": False, "scope": "user",
                }],
            }
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertEqual(len(result["projects"]), 1)
        project = result["projects"][0]
        self.assertEqual(project["path"], shared)
        self.assertEqual(len(project["rules"]), 1)
        self.assertEqual(project["mcpServers"][0]["name"], "serena")

    def test_rules_failure_does_not_break_tool_processing(self):
        """A throwing rules extractor must not crash; MCP-only result still returns."""
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = {
            "projects": [{"path": "/Users/x/.copilot", "mcpServers": [{"name": "serena"}]}]
        }
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.side_effect = RuntimeError("boom")
        result = self.detector.process_single_tool(self.tool)
        self.assertEqual(len(result["projects"]), 1)
        self.assertEqual(result["projects"][0]["mcpServers"][0]["name"], "serena")

    def test_empty_rules_no_phantom_project(self):
        """No rules and no servers -> no phantom project row."""
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        result = self.detector.process_single_tool(self.tool)
        self.assertEqual(result["projects"], [])


# ---------------------------------------------------------------------------
# 13. Windows rules extraction: OS-specific seams + inherited walk
# ---------------------------------------------------------------------------

class TestWindowsCopilotCliRulesExtraction(unittest.TestCase):
    """The Windows extractor overrides only the OS seams; the 6-source walk is
    inherited from the macOS class. These guard the seams — they would fail if
    the Windows class regressed to a bare ``pass`` subclass over the macOS-only
    primitives (which silently scans only the current user and walks ``/`` with
    POSIX-only skip lists)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.ext = WindowsCopilotCliRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _all_rules(self, pbr: dict) -> list:
        rules = []
        for items in pbr.values():
            rules.extend(items)
        return rules

    def test_admin_scan_collects_global_rules_from_every_user(self):
        """C1 regression: global G1 is collected for ALL C:\\Users users, not just
        the running one. Fails if ``_scan_all_user_homes`` isn't overridden (the
        macOS base uses is_running_as_root/scan_user_directories, no-ops on Windows)."""
        users = Path(self.tmp_dir) / "Users"
        for name in ("alice", "bob"):
            cfg = users / name / ".copilot"
            cfg.mkdir(parents=True)
            (cfg / "copilot-instructions.md").write_text(f"{name} G1", encoding="utf-8")

        def fake_scan(callback):
            for name in ("alice", "bob"):
                callback(users / name)

        pbr: dict = {}
        with patch(f"{_WIN_RULES_MOD}.scan_windows_user_directories", side_effect=fake_scan):
            self.ext._extract_global_rules(pbr)
        roots = set(pbr.keys())
        self.assertIn(str(users / "alice" / ".copilot"), roots)
        self.assertIn(str(users / "bob" / ".copilot"), roots)
        rules = self._all_rules(pbr)
        self.assertEqual(len(rules), 2)
        self.assertTrue(all(r["scope"] == "user" for r in rules))

    def test_is_privileged_wired_to_admin_check(self):
        """E1 gating uses the Windows admin check, not the POSIX root check."""
        with patch(f"{_WIN_RULES_MOD}.is_running_as_admin", return_value=True):
            self.assertTrue(self.ext._is_privileged())
        with patch(f"{_WIN_RULES_MOD}.is_running_as_admin", return_value=False):
            self.assertFalse(self.ext._is_privileged())

    def test_filesystem_root_is_drive_anchor(self):
        """The project walk starts at the drive anchor, not a hardcoded POSIX root."""
        self.assertEqual(self.ext._filesystem_root(), Path(Path.home().anchor))

    def test_all_os_seams_overridden_on_windows(self):
        """Host-independent C1 guard: every OS seam must be overridden on the
        Windows class itself. Catches a regression to a bare ``pass`` subclass
        even on a POSIX CI host, where the drive-anchor assertion alone can't."""
        own = vars(WindowsCopilotCliRulesExtractor)
        for seam in (
            "_is_privileged",
            "_scan_all_user_homes",
            "_filesystem_root",
            "_iter_top_level_dirs",
            "_should_skip",
        ):
            self.assertIn(seam, own, f"Windows must override {seam} (else macOS-only behaviour leaks)")

    def test_should_skip_targets_windows_system_dirs(self):
        """The skip predicate excludes Windows system dirs (so the walk doesn't
        recurse the entire OS tree). The macOS extractor does NOT skip these —
        proving the seam genuinely changed behaviour. Synthetic non-system paths
        are used so macOS's own /var-based system-path skip doesn't confound it."""
        win = self.ext
        mac = MacOSCopilotCliRulesExtractor()
        for sysname in ("Windows", "Program Files", "ProgramData"):
            p = Path("/data/projects") / sysname
            self.assertTrue(win._should_skip(p), f"Windows must skip {sysname}")
            self.assertFalse(mac._should_skip(p), f"macOS must NOT skip {sysname}")
        self.assertFalse(win._should_skip(Path("/data/projects/myproject")))

    def test_inherited_project_walk_detects_p1_p2_p3(self):
        """The inherited P1/P2/P3 walk runs end-to-end through the Windows subclass
        (scoped to a temp repo via the non-root walk branch)."""
        repos = Path(self.tmp_dir) / "repos"
        repo = repos / "proj"
        (repo / ".git").mkdir(parents=True)
        (repo / ".github" / "instructions").mkdir(parents=True)
        (repo / ".github" / "copilot-instructions.md").write_text("P1", encoding="utf-8")
        (repo / ".github" / "instructions" / "sec.instructions.md").write_text("P2", encoding="utf-8")
        (repo / "AGENTS.md").write_text("P3", encoding="utf-8")
        pbr: dict = {}
        with patch.object(self.ext, "_should_skip", return_value=False):
            self.ext._extract_project_level_rules(repos, pbr)
        names = {r["file_name"] for r in self._all_rules(pbr)}
        self.assertIn("copilot-instructions.md", names)  # P1
        self.assertIn("sec.instructions.md", names)       # P2
        self.assertIn("AGENTS.md", names)                 # P3
        self.assertTrue(all(r["scope"] == "project" for r in self._all_rules(pbr)))


# ---------------------------------------------------------------------------
# 14. Settings/permissions: durable config (trusted_folders, allowed/denied URLs)
# ---------------------------------------------------------------------------

class TestCopilotCliSettingsGlobal(unittest.TestCase):
    """The settings extractor reads the CLI's durable on-disk permission keys
    from config.json/settings.json and maps them to the Claude-style nested
    ``permissions`` shape (routed through transform_settings_to_backend_format)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSCopilotCliSettingsExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.home = Path(self.tmp_dir) / "home"
        self.config_dir = self.home / ".copilot"
        self.config_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _run(self) -> list:
        with patch(f"{_SETTINGS_MOD}.Path.home", return_value=self.home), \
             patch(f"{_SETTINGS_MOD}.is_running_as_root", return_value=False):
            return self.extractor.extract_settings()

    def test_trusted_folders_and_urls_snake_case(self):
        (self.config_dir / "config.json").write_text(
            '{"trusted_folders": ["/a", "/b"], "allowed_urls": ["https://x"], "denied_urls": ["https://y"]}',
            encoding="utf-8")
        recs = self._run()
        self.assertEqual(len(recs), 1)
        perms = recs[0]["permissions"]
        self.assertEqual(perms["additionalDirectories"], ["/a", "/b"])
        self.assertEqual(perms["allow"], ["https://x"])
        self.assertEqual(perms["deny"], ["https://y"])
        self.assertEqual(recs[0]["scope"], "user")
        self.assertEqual(set(recs[0]["raw_settings"]), {"trusted_folders", "allowed_urls", "denied_urls"})

    def test_camelcase_keys_tolerated(self):
        (self.config_dir / "config.json").write_text(
            '{"trustedFolders": ["/c"], "allowedUrls": ["https://z"]}', encoding="utf-8")
        perms = self._run()[0]["permissions"]
        self.assertEqual(perms["additionalDirectories"], ["/c"])
        self.assertEqual(perms["allow"], ["https://z"])

    def test_jsonc_comments_and_trailing_commas_tolerated(self):
        (self.config_dir / "config.json").write_text(
            '{\n  // auto-managed\n  "trusted_folders": ["/a"],\n}\n', encoding="utf-8")
        self.assertEqual(self._run()[0]["permissions"]["additionalDirectories"], ["/a"])

    def test_settings_json_overrides_config_json(self):
        (self.config_dir / "config.json").write_text('{"allowed_urls": ["https://old"]}', encoding="utf-8")
        (self.config_dir / "settings.json").write_text('{"allowed_urls": ["https://new"]}', encoding="utf-8")
        self.assertEqual(self._run()[0]["permissions"]["allow"], ["https://new"])

    def test_settings_json_explicit_empty_wins_over_config(self):
        # Presence (not truthiness) decides: an explicit empty list in the
        # migration-target settings.json overrides config.json.
        (self.config_dir / "config.json").write_text('{"trusted_folders": ["/a"]}', encoding="utf-8")
        (self.config_dir / "settings.json").write_text('{"trusted_folders": []}', encoding="utf-8")
        self.assertEqual(self._run()[0]["permissions"]["additionalDirectories"], [])

    def test_empty_posture_record_when_config_has_no_permission_keys(self):
        (self.config_dir / "config.json").write_text('{"model": "x", "theme": "auto"}', encoding="utf-8")
        recs = self._run()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["raw_settings"], {"trusted_folders": [], "allowed_urls": [], "denied_urls": []})

    def test_no_config_files_yields_no_record(self):
        # Empty ~/.copilot (no config.json/settings.json) -> no phantom row.
        self.assertEqual(self._run(), [])

    def test_settings_path_under_config_dir(self):
        (self.config_dir / "config.json").write_text('{"trusted_folders": ["/a"]}', encoding="utf-8")
        self.assertTrue(self._run()[0]["settings_path"].startswith(str(self.config_dir)))

    def test_raw_settings_always_truthy_avoids_strict_reread(self):
        # raw_settings must be a non-empty 3-key dict so the transformer never
        # re-reads the JSONC file with strict json.loads.
        (self.config_dir / "config.json").write_text('{\n  // c\n  "model":"x",\n}\n', encoding="utf-8")
        r = self._run()[0]
        self.assertTrue(r["raw_settings"])
        self.assertEqual(set(r["raw_settings"]), {"trusted_folders", "allowed_urls", "denied_urls"})

    def test_permissions_config_json_future_probe(self):
        # Documented-but-unshipped permissions-config.json: parsed into raw_settings if present.
        (self.config_dir / "config.json").write_text('{"trusted_folders": ["/a"]}', encoding="utf-8")
        (self.config_dir / "permissions-config.json").write_text('{"some": "future"}', encoding="utf-8")
        self.assertEqual(self._run()[0]["raw_settings"].get("permissions_config"), {"some": "future"})

    def test_corrupt_config_does_not_crash(self):
        (self.config_dir / "config.json").write_text("{not valid json", encoding="utf-8")
        self.assertEqual(self._run(), [])  # unparseable + no settings.json -> no record, no crash

    def test_full_settings_json_flows_into_raw_settings(self):
        # Gap 2: autonomy/security flags in settings.json reach the risk classifier
        # via raw_settings (not just the 3 permission keys).
        (self.config_dir / "config.json").write_text('{"trusted_folders": ["/a"]}', encoding="utf-8")
        (self.config_dir / "settings.json").write_text(
            '{"continueOnAutoMode": true, "storeTokenPlaintext": true, "askUser": false, "model": "x"}',
            encoding="utf-8")
        raw = self._run()[0]["raw_settings"]
        self.assertEqual(raw["continueOnAutoMode"], True)
        self.assertEqual(raw["storeTokenPlaintext"], True)
        self.assertIn("askUser", raw)
        self.assertEqual(raw["trusted_folders"], ["/a"])  # resolved permission key still canonical

    def test_config_json_auth_state_never_leaks_into_raw_settings(self):
        # SECURITY: config.json holds auth/internal state — it must NOT be dumped;
        # only its permission keys (trusted_folders) are lifted.
        (self.config_dir / "config.json").write_text(
            '{"trusted_folders": ["/a"], "github_oauth_token": "SECRET", '
            '"expAssignmentsCache": {"big": 1}}', encoding="utf-8")
        raw = self._run()[0]["raw_settings"]
        self.assertEqual(raw["trusted_folders"], ["/a"])  # permission key lifted
        self.assertNotIn("github_oauth_token", raw)        # auth NOT leaked
        self.assertNotIn("expAssignmentsCache", raw)       # internal noise excluded


# ---------------------------------------------------------------------------
# 15. Settings routing: permissions attaches to the tool dict (per-user isolated)
# ---------------------------------------------------------------------------

class TestCopilotCliSettingsRouting(unittest.TestCase):
    """``_process_copilot_cli_tool`` attaches tool-level ``permissions``, isolating
    each user-install via the install_path filter (the early-return branch bypasses
    the shared _settings merge, so this must hold on its own)."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")
        self.detector._copilot_cli_mcp_extractor = MagicMock()
        self.detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        self.detector._copilot_cli_rules_extractor = MagicMock()
        self.detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        self.tool = {"name": "GitHub Copilot CLI", "version": "1.0.55", "install_path": "/Users/x/.copilot"}

    @staticmethod
    def _record(settings_path, trusted=None, allow=None, deny=None):
        return {
            "tool_name": "GitHub Copilot CLI", "scope": "user", "settings_path": settings_path,
            "raw_settings": {"trusted_folders": trusted or [], "allowed_urls": allow or [], "denied_urls": deny or []},
            "permissions": {"additionalDirectories": trusted or [], "allow": allow or [], "deny": deny or []},
        }

    def test_permissions_attached_to_tool_dict(self):
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.return_value = [
            self._record("/Users/x/.copilot/config.json", trusted=["/Users/x/app"], allow=["https://gh"])
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertIn("permissions", result)
        p = result["permissions"]
        self.assertEqual(p["settings_source"], "user")
        self.assertEqual(p["additional_directories"], ["/Users/x/app"])
        self.assertEqual(p["allow_rules"], ["https://gh"])

    def test_install_path_isolates_other_users(self):
        # Records for THIS install (x) and another user (y); only x is attached,
        # and y's data must not leak onto x's row.
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.return_value = [
            self._record("/Users/y/.copilot/config.json", trusted=["/Users/y/secret"]),
            self._record("/Users/x/.copilot/config.json", trusted=["/Users/x/app"]),
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertEqual(result["permissions"]["additional_directories"], ["/Users/x/app"])
        self.assertNotIn("/Users/y/secret", result["permissions"]["raw_settings"]["trusted_folders"])

    def test_empty_posture_record_attaches(self):
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.return_value = [
            self._record("/Users/x/.copilot/config.json")  # all empty
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertIn("permissions", result)
        self.assertEqual(result["permissions"]["settings_source"], "user")
        self.assertNotIn("allow_rules", result["permissions"])  # empty allow omitted by transformer

    def test_no_matching_install_yields_no_permissions(self):
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.return_value = [
            self._record("/Users/other/.copilot/config.json", trusted=["/o"])
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertNotIn("permissions", result)

    def test_sibling_dir_prefix_collision_excluded(self):
        # A sibling config dir that string-prefixes install_path (".copilot-old")
        # must NOT match — the filter is boundary-safe (matches by parent dir).
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.return_value = [
            self._record("/Users/x/.copilot-old/config.json", trusted=["/stale"])
        ]
        result = self.detector.process_single_tool(self.tool)
        self.assertNotIn("permissions", result)

    def test_settings_failure_does_not_break_tool(self):
        self.detector._copilot_cli_settings_extractor = MagicMock()
        self.detector._copilot_cli_settings_extractor.extract_settings.side_effect = RuntimeError("boom")
        result = self.detector.process_single_tool(self.tool)
        self.assertNotIn("permissions", result)
        self.assertEqual(result["name"], "GitHub Copilot CLI")  # tool still returns

    def test_no_settings_extractor_no_permissions(self):
        self.detector._copilot_cli_settings_extractor = None
        result = self.detector.process_single_tool(self.tool)
        self.assertNotIn("permissions", result)


# ---------------------------------------------------------------------------
# 16. Windows settings extraction: the single OS seam
# ---------------------------------------------------------------------------

class TestWindowsCopilotCliSettingsExtraction(unittest.TestCase):
    """The Windows settings extractor overrides ONLY the all-users scan seam; the
    config parsing/mapping is inherited from the macOS class."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.ext = WindowsCopilotCliSettingsExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_admin_scan_collects_settings_from_every_user(self):
        users = Path(self.tmp_dir) / "Users"
        for name in ("alice", "bob"):
            cfg = users / name / ".copilot"
            cfg.mkdir(parents=True)
            (cfg / "config.json").write_text(f'{{"trusted_folders": ["/{name}"]}}', encoding="utf-8")

        def fake_scan(callback):
            for name in ("alice", "bob"):
                callback(users / name)

        with patch(f"{_WIN_SETTINGS_MOD}.scan_windows_user_directories", side_effect=fake_scan):
            recs = self.ext.extract_settings()
        dirs = sorted(r["permissions"]["additionalDirectories"][0] for r in recs)
        self.assertEqual(dirs, ["/alice", "/bob"])
        self.assertEqual(len(recs), 2)

    def test_only_scan_seam_overridden(self):
        own = vars(WindowsCopilotCliSettingsExtractor)
        self.assertIn("_scan_all_user_homes", own)
        # Parsing logic is inherited, not duplicated.
        for inherited in ("extract_settings", "_extract_for_user"):
            self.assertNotIn(inherited, own)


if __name__ == "__main__":
    unittest.main()
