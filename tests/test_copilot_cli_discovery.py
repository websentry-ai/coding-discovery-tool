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
)
from scripts.coding_discovery_tools.macos.copilot_cli.mcp_config_extractor import (
    MacOSCopilotCliMCPConfigExtractor,
    _extract_servers_obj,
    _strip_jsonc_comments,
    _strip_trailing_commas,
)
from scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli import (
    WindowsCopilotCliDetector,
)
from scripts.coding_discovery_tools.windows.copilot_cli.mcp_config_extractor import (
    WindowsCopilotCliMCPConfigExtractor,
)

# Module path for patching the detector's root-scan helpers.
_DETECTOR_MOD = "scripts.coding_discovery_tools.macos.copilot_cli.copilot_cli"
# Module path for patching the Windows detector's admin/all-users scan.
_WIN_DETECTOR_MOD = "scripts.coding_discovery_tools.windows.copilot_cli.copilot_cli"


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

    def test_get_version_uses_shell_true_for_npm_shim(self):
        """Windows overrides get_version with shell=True (npm .cmd shim) and parses output."""
        fake = MagicMock(returncode=0, stdout="GitHub Copilot CLI 0.0.399.\n", stderr="")
        with patch(f"{_WIN_DETECTOR_MOD}.subprocess.run", return_value=fake) as run:
            version = WindowsCopilotCliDetector().get_version()
        self.assertEqual(version, "GitHub Copilot CLI 0.0.399.")
        self.assertIs(run.call_args.kwargs.get("shell"), True)

    def test_get_version_returns_none_on_failure(self):
        """A failed/absent binary yields None (caller falls back to 'unknown'), never raises."""
        with patch(f"{_WIN_DETECTOR_MOD}.subprocess.run", side_effect=FileNotFoundError()):
            self.assertIsNone(WindowsCopilotCliDetector().get_version())


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


if __name__ == "__main__":
    unittest.main()
