"""
Unit tests for scanning enhancements: settings transformers, managed drop-in
settings, plist extraction, path-specific Copilot instructions, workspace
MCP configs, and Codex project-level MCP configs.
"""

import json
import os
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

from scripts.coding_discovery_tools.settings_transformers import (
    DEFAULT_PRECEDENCE,
    SETTINGS_PRECEDENCE,
    _get_highest_precedence_setting,
    _get_precedence,
    _get_scope_value,
    _has_permissions,
    _read_raw_settings_from_file,
    transform_settings_to_backend_format,
)
from scripts.coding_discovery_tools.macos.claude_code.settings_extractor import (
    MacOSClaudeSettingsExtractor,
)
from scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor import (
    MacOSGitHubCopilotRulesExtractor,
    find_github_copilot_project_root,
)
from scripts.coding_discovery_tools.macos.github_copilot.mcp_config_extractor import (
    MacOSGitHubCopilotMCPConfigExtractor,
)
from scripts.coding_discovery_tools.macos.codex.mcp_config_extractor import (
    MacOSCodexMCPConfigExtractor,
    parse_toml_mcp_servers,
    read_codex_toml_mcp_config,
)


# ---------------------------------------------------------------------------
# 1. Settings Transformer Tests
# ---------------------------------------------------------------------------

class TestSettingsPrecedenceOrdering(unittest.TestCase):
    """Verify SETTINGS_PRECEDENCE constants have correct ordering."""

    def test_managed_plist_is_highest(self):
        self.assertEqual(SETTINGS_PRECEDENCE["managed_plist"], 6)

    def test_managed_dropin_is_second(self):
        self.assertEqual(SETTINGS_PRECEDENCE["managed_dropin"], 5)

    def test_managed_is_third(self):
        self.assertEqual(SETTINGS_PRECEDENCE["managed"], 4)

    def test_local_is_fourth(self):
        self.assertEqual(SETTINGS_PRECEDENCE["local"], 3)

    def test_project_is_fifth(self):
        self.assertEqual(SETTINGS_PRECEDENCE["project"], 2)

    def test_user_is_lowest(self):
        self.assertEqual(SETTINGS_PRECEDENCE["user"], 1)

    def test_relative_ordering(self):
        """All six tiers must be strictly ordered."""
        self.assertGreater(
            SETTINGS_PRECEDENCE["managed_plist"],
            SETTINGS_PRECEDENCE["managed_dropin"],
        )
        self.assertGreater(
            SETTINGS_PRECEDENCE["managed_dropin"],
            SETTINGS_PRECEDENCE["managed"],
        )
        self.assertGreater(
            SETTINGS_PRECEDENCE["managed"],
            SETTINGS_PRECEDENCE["local"],
        )
        self.assertGreater(
            SETTINGS_PRECEDENCE["local"],
            SETTINGS_PRECEDENCE["project"],
        )
        self.assertGreater(
            SETTINGS_PRECEDENCE["project"],
            SETTINGS_PRECEDENCE["user"],
        )

    def test_unknown_scope_returns_default(self):
        self.assertEqual(_get_precedence("unknown_scope"), DEFAULT_PRECEDENCE)


class TestTransformSettingsToBackendFormat(unittest.TestCase):
    """Tests for transform_settings_to_backend_format."""

    def test_empty_list_returns_none(self):
        self.assertIsNone(transform_settings_to_backend_format([]))

    def test_managed_plist_maps_to_managed_settings_source(self):
        settings = [
            {
                "scope": "managed_plist",
                "settings_path": "plist:com.anthropic.claudecode",
                "raw_settings": {"permissions": {"allow": ["Read"]}},
                "permissions": {"allow": ["Read"], "defaultMode": "allowedTools"},
                "sandbox": {},
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["settings_source"], "managed")
        self.assertEqual(result["scope"], "managed_plist")

    def test_managed_dropin_maps_to_managed_settings_source(self):
        settings = [
            {
                "scope": "managed_dropin",
                "settings_path": "/Library/Application Support/ClaudeCode/managed-settings.d/override.json",
                "raw_settings": {"permissions": {"deny": ["Write"]}},
                "permissions": {"deny": ["Write"], "defaultMode": "deny"},
                "sandbox": {},
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["settings_source"], "managed")
        self.assertEqual(result["scope"], "managed_dropin")

    def test_local_maps_to_user_settings_source(self):
        """Regression: local scope must map to settings_source='user'."""
        settings = [
            {
                "scope": "local",
                "settings_path": "/project/.claude/settings.local.json",
                "raw_settings": {"permissions": {"allow": ["Bash"]}},
                "permissions": {"allow": ["Bash"], "defaultMode": "default"},
                "sandbox": {},
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["settings_source"], "user")
        self.assertEqual(result["scope"], "local")

    def test_managed_plist_wins_when_all_scopes_present(self):
        """managed_plist should win over every other scope."""
        settings = [
            {
                "scope": "user",
                "settings_path": "/home/.claude/settings.json",
                "permissions": {"allow": ["Read"], "defaultMode": "default"},
                "sandbox": {},
            },
            {
                "scope": "project",
                "settings_path": "/project/.claude/settings.json",
                "permissions": {"allow": ["Write"], "defaultMode": "default"},
                "sandbox": {},
            },
            {
                "scope": "local",
                "settings_path": "/project/.claude/settings.local.json",
                "permissions": {"allow": ["Bash"], "defaultMode": "default"},
                "sandbox": {},
            },
            {
                "scope": "managed",
                "settings_path": "/Library/managed-settings.json",
                "permissions": {"allow": ["All"], "defaultMode": "allowedTools"},
                "sandbox": {},
            },
            {
                "scope": "managed_dropin",
                "settings_path": "/Library/managed-settings.d/a.json",
                "permissions": {"deny": ["Delete"], "defaultMode": "deny"},
                "sandbox": {},
            },
            {
                "scope": "managed_plist",
                "settings_path": "plist:com.anthropic.claudecode",
                "raw_settings": {"permissions": {"allow": ["Plist"]}},
                "permissions": {"allow": ["Plist"], "defaultMode": "plist_mode"},
                "sandbox": {"enabled": True},
            },
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertIsNotNone(result)
        self.assertEqual(result["scope"], "managed_plist")
        self.assertEqual(result["permission_mode"], "plist_mode")

    def test_managed_dropin_wins_over_managed_but_loses_to_plist(self):
        settings = [
            {
                "scope": "managed",
                "settings_path": "/Library/managed-settings.json",
                "permissions": {"allow": ["All"], "defaultMode": "managed_mode"},
                "sandbox": {},
            },
            {
                "scope": "managed_dropin",
                "settings_path": "/Library/managed-settings.d/a.json",
                "permissions": {"deny": ["Delete"], "defaultMode": "dropin_mode"},
                "sandbox": {},
            },
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertEqual(result["scope"], "managed_dropin")
        self.assertEqual(result["permission_mode"], "dropin_mode")

    def test_mcp_servers_included_when_present(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/home/.claude/settings.json",
                "permissions": {"allow": ["Read"], "defaultMode": "default"},
                "sandbox": {},
                "mcp_servers": ["server1", "server2"],
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertEqual(result["mcp_servers"], ["server1", "server2"])

    def test_mcp_policies_included_when_present(self):
        settings = [
            {
                "scope": "managed",
                "settings_path": "/Library/managed-settings.json",
                "permissions": {"defaultMode": "default"},
                "sandbox": {},
                "mcp_policies": {"allowedMcpServers": ["a"], "deniedMcpServers": ["b"]},
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertEqual(result["mcp_policies"]["allowedMcpServers"], ["a"])
        self.assertEqual(result["mcp_policies"]["deniedMcpServers"], ["b"])

    def test_sandbox_enabled_mapped(self):
        settings = [
            {
                "scope": "user",
                "settings_path": "/home/.claude/settings.json",
                "permissions": {"defaultMode": "default"},
                "sandbox": {"enabled": True},
            }
        ]
        result = transform_settings_to_backend_format(settings)
        self.assertTrue(result["sandbox_enabled"])

    def test_raw_settings_read_from_file_when_missing(self):
        """When raw_settings is empty, transformer falls back to reading the file."""
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        try:
            json.dump({"permissions": {"allow": ["Read"]}}, tmp)
            tmp.close()
            settings = [
                {
                    "scope": "user",
                    "settings_path": tmp.name,
                    "raw_settings": {},
                    "permissions": {"allow": ["Read"], "defaultMode": "default"},
                    "sandbox": {},
                }
            ]
            result = transform_settings_to_backend_format(settings)
            self.assertIsNotNone(result)
            self.assertEqual(result["raw_settings"]["permissions"]["allow"], ["Read"])
        finally:
            os.unlink(tmp.name)


class TestGetScopeValue(unittest.TestCase):
    """Tests for _get_scope_value helper."""

    def test_prefers_scope_field(self):
        self.assertEqual(_get_scope_value({"scope": "managed_plist", "settings_source": "user"}), "managed_plist")

    def test_falls_back_to_settings_source(self):
        self.assertEqual(_get_scope_value({"settings_source": "managed"}), "managed")

    def test_defaults_to_user(self):
        self.assertEqual(_get_scope_value({}), "user")


class TestHasPermissions(unittest.TestCase):
    """Tests for _has_permissions helper."""

    def test_returns_true_when_allow_present(self):
        self.assertTrue(_has_permissions({"permissions": {"allow": ["Read"]}}))

    def test_returns_true_when_deny_present(self):
        self.assertTrue(_has_permissions({"permissions": {"deny": ["Write"]}}))

    def test_returns_true_when_default_mode_present(self):
        self.assertTrue(_has_permissions({"permissions": {"defaultMode": "default"}}))

    def test_returns_false_for_empty_permissions(self):
        self.assertFalse(_has_permissions({"permissions": {}}))

    def test_returns_false_when_no_permissions_key(self):
        self.assertFalse(_has_permissions({}))


class TestReadRawSettingsFromFile(unittest.TestCase):
    """Tests for _read_raw_settings_from_file."""

    def test_reads_valid_json(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        try:
            json.dump({"key": "value"}, tmp)
            tmp.close()
            result = _read_raw_settings_from_file(Path(tmp.name))
            self.assertEqual(result, {"key": "value"})
        finally:
            os.unlink(tmp.name)

    def test_returns_empty_dict_for_nonexistent_file(self):
        result = _read_raw_settings_from_file(Path("/nonexistent/path/settings.json"))
        self.assertEqual(result, {})

    def test_returns_empty_dict_for_invalid_json(self):
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
        try:
            tmp.write("not valid json {{{")
            tmp.close()
            result = _read_raw_settings_from_file(Path(tmp.name))
            self.assertEqual(result, {})
        finally:
            os.unlink(tmp.name)


# ---------------------------------------------------------------------------
# 2. Claude Code Managed Drop-in Tests
# ---------------------------------------------------------------------------

class TestClaudeCodeManagedDropinSettings(unittest.TestCase):
    """Tests for MacOSClaudeSettingsExtractor._extract_managed_dropin_settings."""

    def setUp(self):
        self.extractor = MacOSClaudeSettingsExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _make_dropin(self, filename, data):
        """Write a JSON drop-in file inside self.tmp_dir."""
        path = Path(self.tmp_dir) / filename
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_finds_json_files_in_dropin_directory(self):
        self._make_dropin("override.json", {
            "permissions": {"allow": ["Read"], "defaultMode": "default"},
        })
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["scope"], "managed_dropin")

    def test_assigns_managed_dropin_scope(self):
        self._make_dropin("a.json", {"permissions": {"allow": ["X"]}})
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        self.assertTrue(all(s["scope"] == "managed_dropin" for s in result))

    def test_empty_directory_returns_empty_list(self):
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        self.assertEqual(result, [])

    def test_nonexistent_directory_returns_empty_list(self):
        missing = Path(self.tmp_dir) / "nonexistent"
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=missing,
        ):
            result = self.extractor._extract_managed_dropin_settings()
        self.assertEqual(result, [])

    def test_bad_json_skipped_without_crash(self):
        (Path(self.tmp_dir) / "bad.json").write_text("not json {{", encoding="utf-8")
        self._make_dropin("good.json", {"permissions": {"allow": ["Read"]}})
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        # Only the good file should produce a result
        self.assertEqual(len(result), 1)
        self.assertIn("good.json", result[0]["settings_path"])

    def test_permission_denied_does_not_crash(self):
        """If the directory cannot be listed, return empty list without error."""
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            # Simulate a PermissionError when trying to glob
            with patch.object(Path, "glob", side_effect=PermissionError("denied")):
                result = self.extractor._extract_managed_dropin_settings()
        self.assertEqual(result, [])

    def test_multiple_dropin_files_all_extracted(self):
        self._make_dropin("01-first.json", {"permissions": {"allow": ["A"]}})
        self._make_dropin("02-second.json", {"permissions": {"deny": ["B"]}})
        self._make_dropin("03-third.json", {"permissions": {"ask": ["C"]}})
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        self.assertEqual(len(result), 3)

    def test_files_processed_in_sorted_order(self):
        self._make_dropin("z-last.json", {"permissions": {"allow": ["Z"]}})
        self._make_dropin("a-first.json", {"permissions": {"allow": ["A"]}})
        self._make_dropin("m-middle.json", {"permissions": {"allow": ["M"]}})
        with patch.object(
            MacOSClaudeSettingsExtractor,
            "MANAGED_DROPIN_DIR",
            new_callable=PropertyMock,
            return_value=Path(self.tmp_dir),
        ):
            result = self.extractor._extract_managed_dropin_settings()
        paths = [r["settings_path"] for r in result]
        self.assertIn("a-first.json", paths[0])
        self.assertIn("m-middle.json", paths[1])
        self.assertIn("z-last.json", paths[2])


# ---------------------------------------------------------------------------
# 3. Claude Code Plist Tests
# ---------------------------------------------------------------------------

class TestClaudeCodePlistSettings(unittest.TestCase):
    """Tests for MacOSClaudeSettingsExtractor._extract_plist_settings."""

    def setUp(self):
        self.extractor = MacOSClaudeSettingsExtractor()

    def _make_plist_bytes(self, data):
        """Generate binary plist from a Python dict."""
        return plistlib.dumps(data)

    def test_extracts_settings_from_valid_plist(self):
        plist_data = {
            "permissions": {
                "defaultMode": "allowedTools",
                "allow": ["Read"],
                "deny": ["Write"],
                "ask": ["Bash"],
            },
            "sandbox": {"enabled": True},
            "mcpServers": {"server1": {"command": "npx"}},
            "allowedMcpServers": ["server1"],
            "deniedMcpServers": ["evil-server"],
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes(plist_data), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()

        self.assertEqual(len(result), 1)
        s = result[0]
        self.assertEqual(s["scope"], "managed_plist")
        self.assertEqual(s["settings_path"], "plist:com.anthropic.claudecode")
        self.assertEqual(s["permissions"]["defaultMode"], "allowedTools")
        self.assertEqual(s["permissions"]["allow"], ["Read"])
        self.assertEqual(s["permissions"]["deny"], ["Write"])
        self.assertEqual(s["permissions"]["ask"], ["Bash"])
        self.assertEqual(s["mcp_servers"], ["server1"])
        self.assertEqual(s["mcp_policies"]["allowedMcpServers"], ["server1"])
        self.assertEqual(s["mcp_policies"]["deniedMcpServers"], ["evil-server"])
        self.assertTrue(s["sandbox"]["enabled"])

    def test_scope_is_managed_plist(self):
        plist_data = {"permissions": {"allow": ["Read"]}}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes(plist_data), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result[0]["scope"], "managed_plist")

    def test_settings_path_is_plist_domain(self):
        plist_data = {"permissions": {"allow": ["Read"]}}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes(plist_data), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result[0]["settings_path"], "plist:com.anthropic.claudecode")

    def test_raw_settings_populated_directly(self):
        """raw_settings should be populated from plist data, not from file fallback."""
        plist_data = {"permissions": {"allow": ["Read"]}, "custom_key": "custom_value"}
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes(plist_data), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result[0]["raw_settings"]["custom_key"], "custom_value")
        self.assertIn("permissions", result[0]["raw_settings"])

    def test_defaults_export_failure_returns_empty(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=1, stdout=b"", stderr=b"Domain not found"
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result, [])

    def test_subprocess_timeout_returns_empty(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="defaults", timeout=5)):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result, [])

    def test_invalid_plist_data_returns_empty(self):
        """Non-dict plist data should be treated as empty."""
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes("just a string"), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result, [])

    def test_empty_plist_data_returns_empty(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes({}), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result, [])

    def test_extracts_permissions_mcp_servers_and_policies(self):
        plist_data = {
            "permissions": {
                "defaultMode": "default",
                "allow": ["Read", "Write"],
                "deny": ["Delete"],
                "ask": ["Bash(rm *)"],
                "additionalDirectories": ["/extra"],
            },
            "mcpServers": {
                "linear": {"command": "npx"},
                "sentry": {"url": "https://sentry.io"},
            },
            "allowedMcpServers": ["linear"],
            "deniedMcpServers": ["sentry"],
        }
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=self._make_plist_bytes(plist_data), stderr=b""
        )
        with patch("subprocess.run", return_value=completed):
            result = self.extractor._extract_plist_settings()

        s = result[0]
        self.assertEqual(s["permissions"]["allow"], ["Read", "Write"])
        self.assertEqual(s["permissions"]["deny"], ["Delete"])
        self.assertEqual(s["permissions"]["ask"], ["Bash(rm *)"])
        self.assertEqual(s["permissions"]["additionalDirectories"], ["/extra"])
        self.assertIn("linear", s["mcp_servers"])
        self.assertIn("sentry", s["mcp_servers"])
        self.assertEqual(s["mcp_policies"]["allowedMcpServers"], ["linear"])
        self.assertEqual(s["mcp_policies"]["deniedMcpServers"], ["sentry"])

    def test_generic_exception_returns_empty(self):
        with patch("subprocess.run", side_effect=OSError("unexpected")):
            result = self.extractor._extract_plist_settings()
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 4. GitHub Copilot Path-Specific Instructions Tests
# ---------------------------------------------------------------------------

class TestFindGitHubCopilotProjectRoot(unittest.TestCase):
    """Tests for find_github_copilot_project_root resolution logic."""

    def test_copilot_subdir_resolves_to_project_root(self):
        """A file in .github/copilot/foo.md should resolve to parent of .github."""
        rule_file = Path("/projects/myapp/.github/copilot/frontend.md")
        self.assertEqual(
            find_github_copilot_project_root(rule_file),
            Path("/projects/myapp"),
        )

    def test_agents_md_resolves_to_parent_directory(self):
        rule_file = Path("/projects/myapp/AGENTS.md")
        self.assertEqual(
            find_github_copilot_project_root(rule_file),
            Path("/projects/myapp"),
        )

    def test_github_dir_copilot_instructions(self):
        """copilot-instructions.md in .github/ should resolve to parent of .github."""
        rule_file = Path("/projects/myapp/.github/copilot-instructions.md")
        self.assertEqual(
            find_github_copilot_project_root(rule_file),
            Path("/projects/myapp"),
        )


class TestGitHubCopilotPathSpecificInstructions(unittest.TestCase):
    """Tests for _extract_path_specific_instructions."""

    def setUp(self):
        self.extractor = MacOSGitHubCopilotRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.github_dir = Path(self.tmp_dir) / ".github"
        self.github_dir.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_finds_md_files_in_copilot_dir(self):
        copilot_dir = self.github_dir / "copilot"
        copilot_dir.mkdir()
        (copilot_dir / "backend.md").write_text("# Backend rules", encoding="utf-8")
        (copilot_dir / "frontend.md").write_text("# Frontend rules", encoding="utf-8")

        projects_by_root = {}
        self.extractor._extract_path_specific_instructions(self.github_dir, projects_by_root)

        self.assertTrue(len(projects_by_root) > 0)
        # Both files should be found
        all_rules = []
        for rules in projects_by_root.values():
            all_rules.extend(rules)
        file_names = {r["file_name"] for r in all_rules}
        self.assertIn("backend.md", file_names)
        self.assertIn("frontend.md", file_names)

    def test_assigns_project_scope(self):
        copilot_dir = self.github_dir / "copilot"
        copilot_dir.mkdir()
        (copilot_dir / "rules.md").write_text("# Project rules", encoding="utf-8")

        projects_by_root = {}
        self.extractor._extract_path_specific_instructions(self.github_dir, projects_by_root)

        for rules in projects_by_root.values():
            for rule in rules:
                self.assertEqual(rule["scope"], "project")

    def test_missing_copilot_dir_no_error(self):
        """If .github/copilot/ does not exist, the method should return without error."""
        projects_by_root = {}
        self.extractor._extract_path_specific_instructions(self.github_dir, projects_by_root)
        self.assertEqual(projects_by_root, {})

    def test_empty_copilot_dir_no_results(self):
        copilot_dir = self.github_dir / "copilot"
        copilot_dir.mkdir()
        projects_by_root = {}
        self.extractor._extract_path_specific_instructions(self.github_dir, projects_by_root)
        self.assertEqual(projects_by_root, {})


class TestAgentsMdDetection(unittest.TestCase):
    """Tests for AGENTS.md detection in _walk_for_github_directories."""

    def setUp(self):
        self.extractor = MacOSGitHubCopilotRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    @patch(
        "scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor.should_skip_system_path",
        return_value=False,
    )
    def test_agents_md_detected_via_walk(self, _mock_skip):
        project_dir = Path(self.tmp_dir) / "myproject"
        project_dir.mkdir()
        agents_file = project_dir / "AGENTS.md"
        agents_file.write_text("# Agents instructions", encoding="utf-8")

        projects_by_root = {}
        self.extractor._walk_for_github_directories(
            Path(self.tmp_dir), Path(self.tmp_dir), projects_by_root, current_depth=0
        )

        all_rules = []
        for rules in projects_by_root.values():
            all_rules.extend(rules)
        file_names = {r["file_name"] for r in all_rules}
        self.assertIn("AGENTS.md", file_names)

    @patch(
        "scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor.should_skip_system_path",
        return_value=False,
    )
    def test_agents_md_scope_is_project(self, _mock_skip):
        project_dir = Path(self.tmp_dir) / "myproject"
        project_dir.mkdir()
        (project_dir / "AGENTS.md").write_text("# Agents", encoding="utf-8")

        projects_by_root = {}
        self.extractor._walk_for_github_directories(
            Path(self.tmp_dir), Path(self.tmp_dir), projects_by_root, current_depth=0
        )

        for rules in projects_by_root.values():
            for rule in rules:
                if rule["file_name"] == "AGENTS.md":
                    self.assertEqual(rule["scope"], "project")


# ---------------------------------------------------------------------------
# 5. GitHub Copilot Workspace MCP Tests
# ---------------------------------------------------------------------------

class TestGitHubCopilotWorkspaceMCP(unittest.TestCase):
    """Tests for _check_vscode_mcp and workspace MCP extraction."""

    def setUp(self):
        self.extractor = MacOSGitHubCopilotMCPConfigExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_check_vscode_mcp_finds_and_parses_mcp_json(self):
        vscode_dir = Path(self.tmp_dir) / "myproject" / ".vscode"
        vscode_dir.mkdir(parents=True)
        mcp_json = vscode_dir / "mcp.json"
        mcp_json.write_text(json.dumps({
            "servers": {
                "my-server": {"command": "npx", "args": ["-y", "my-mcp"]},
            }
        }), encoding="utf-8")

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)

        self.assertEqual(len(configs), 1)
        config = configs[0]
        server_names = [s["name"] for s in config["mcpServers"]]
        self.assertIn("my-server", server_names)

    def test_path_resolves_to_project_root(self):
        """The path in the config should be parent of .vscode (the project root)."""
        project_dir = Path(self.tmp_dir) / "myproject"
        vscode_dir = project_dir / ".vscode"
        vscode_dir.mkdir(parents=True)
        (vscode_dir / "mcp.json").write_text(json.dumps({
            "servers": {"s1": {"command": "cmd"}}
        }), encoding="utf-8")

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)

        self.assertEqual(configs[0]["path"], str(project_dir))

    def test_missing_mcp_json_no_results(self):
        vscode_dir = Path(self.tmp_dir) / "myproject" / ".vscode"
        vscode_dir.mkdir(parents=True)

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)
        self.assertEqual(configs, [])

    def test_invalid_json_in_mcp_json_no_crash(self):
        vscode_dir = Path(self.tmp_dir) / "myproject" / ".vscode"
        vscode_dir.mkdir(parents=True)
        (vscode_dir / "mcp.json").write_text("not valid json {{{", encoding="utf-8")

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)
        self.assertEqual(configs, [])

    def test_extracts_mcp_server_names(self):
        vscode_dir = Path(self.tmp_dir) / "myproject" / ".vscode"
        vscode_dir.mkdir(parents=True)
        (vscode_dir / "mcp.json").write_text(json.dumps({
            "servers": {
                "linear": {"command": "npx", "args": ["mcp-linear"]},
                "sentry": {"url": "https://sentry.io/mcp"},
            }
        }), encoding="utf-8")

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)

        server_names = {s["name"] for s in configs[0]["mcpServers"]}
        self.assertEqual(server_names, {"linear", "sentry"})

    def test_mcpServers_key_also_works(self):
        """mcp.json may use 'mcpServers' instead of 'servers'."""
        vscode_dir = Path(self.tmp_dir) / "myproject" / ".vscode"
        vscode_dir.mkdir(parents=True)
        (vscode_dir / "mcp.json").write_text(json.dumps({
            "mcpServers": {
                "my-tool": {"command": "npx", "args": ["-y", "my-mcp"]},
            }
        }), encoding="utf-8")

        configs = []
        self.extractor._check_vscode_mcp(vscode_dir, configs)
        self.assertEqual(len(configs), 1)
        server_names = {s["name"] for s in configs[0]["mcpServers"]}
        self.assertIn("my-tool", server_names)


# ---------------------------------------------------------------------------
# 6. Codex Project-Level MCP Tests
# ---------------------------------------------------------------------------

class TestCodexProjectLevelMCP(unittest.TestCase):
    """Tests for MacOSCodexMCPConfigExtractor project-level extraction."""

    def setUp(self):
        self.extractor = MacOSCodexMCPConfigExtractor()
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_extract_config_from_codex_dir_parses_toml(self):
        project_dir = Path(self.tmp_dir) / "myproject"
        codex_dir = project_dir / ".codex"
        codex_dir.mkdir(parents=True)
        config_toml = codex_dir / "config.toml"
        config_toml.write_text(
            '[mcpServers.linear]\n'
            'command = "npx"\n'
            'args = ["-y", "mcp-remote", "https://mcp.linear.app/sse"]\n',
            encoding="utf-8",
        )

        configs = []
        self.extractor._extract_config_from_codex_dir(codex_dir, configs)

        self.assertEqual(len(configs), 1)
        server_names = [s["name"] for s in configs[0]["mcpServers"]]
        self.assertIn("linear", server_names)

    def test_parent_levels_2_resolves_to_project_root(self):
        """path should resolve to project root (2 levels up from config.toml)."""
        project_dir = Path(self.tmp_dir) / "myproject"
        codex_dir = project_dir / ".codex"
        codex_dir.mkdir(parents=True)
        config_toml = codex_dir / "config.toml"
        config_toml.write_text(
            '[mcpServers.test]\ncommand = "test"\n',
            encoding="utf-8",
        )

        configs = []
        self.extractor._extract_config_from_codex_dir(codex_dir, configs)

        self.assertEqual(configs[0]["path"], str(project_dir))

    def test_missing_config_toml_no_results(self):
        codex_dir = Path(self.tmp_dir) / "myproject" / ".codex"
        codex_dir.mkdir(parents=True)

        configs = []
        self.extractor._extract_config_from_codex_dir(codex_dir, configs)
        self.assertEqual(configs, [])

    def test_invalid_toml_no_results(self):
        codex_dir = Path(self.tmp_dir) / "myproject" / ".codex"
        codex_dir.mkdir(parents=True)
        (codex_dir / "config.toml").write_text("this is not [[valid toml", encoding="utf-8")

        configs = []
        self.extractor._extract_config_from_codex_dir(codex_dir, configs)
        self.assertEqual(configs, [])

    @patch(
        "scripts.coding_discovery_tools.macos.codex.mcp_config_extractor.should_skip_system_path",
        return_value=False,
    )
    def test_walk_skips_global_codex_directory(self, _mock_skip):
        """_walk_for_codex_configs must skip the user's global ~/.codex."""
        global_codex = Path(self.tmp_dir) / ".codex"
        global_codex.mkdir()
        (global_codex / "config.toml").write_text(
            '[mcpServers.global]\ncommand = "global"\n',
            encoding="utf-8",
        )
        project_codex = Path(self.tmp_dir) / "projects" / "myapp" / ".codex"
        project_codex.mkdir(parents=True)
        (project_codex / "config.toml").write_text(
            '[mcpServers.project]\ncommand = "project"\n',
            encoding="utf-8",
        )

        configs = []
        self.extractor._walk_for_codex_configs(
            root_path=Path(self.tmp_dir),
            current_dir=Path(self.tmp_dir),
            configs=configs,
            global_codex_dir=global_codex,
            current_depth=0,
        )

        # Only the project-level config should appear, not the global one
        server_names = []
        for config in configs:
            for s in config.get("mcpServers", []):
                server_names.append(s["name"])
        self.assertIn("project", server_names)
        self.assertNotIn("global", server_names)


class TestParseTomlMcpServers(unittest.TestCase):
    """Tests for the parse_toml_mcp_servers function."""

    def test_camel_case_section(self):
        content = (
            '[mcpServers.linear]\n'
            'command = "npx"\n'
            'args = ["-y", "mcp-remote"]\n'
        )
        result = parse_toml_mcp_servers(content)
        self.assertIsNotNone(result)
        self.assertIn("linear", result)

    def test_snake_case_section(self):
        content = (
            '[mcp_servers.linear]\n'
            'type = "http"\n'
            'url = "https://mcp.linear.app/mcp"\n'
        )
        result = parse_toml_mcp_servers(content)
        self.assertIsNotNone(result)
        self.assertIn("linear", result)

    def test_no_mcp_servers_returns_none(self):
        content = "[general]\nkey = 'value'\n"
        result = parse_toml_mcp_servers(content)
        self.assertIsNone(result)

    def test_empty_content_returns_none(self):
        result = parse_toml_mcp_servers("")
        self.assertIsNone(result)


class TestReadCodexTomlMcpConfig(unittest.TestCase):
    """Tests for read_codex_toml_mcp_config."""

    def setUp(self):
        self.tmp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_reads_valid_toml(self):
        config_path = Path(self.tmp_dir) / "config.toml"
        config_path.write_text(
            '[mcpServers.test]\ncommand = "test-cmd"\n',
            encoding="utf-8",
        )
        result = read_codex_toml_mcp_config(config_path, parent_levels=1)
        self.assertIsNotNone(result)
        self.assertEqual(result["path"], self.tmp_dir)
        server_names = [s["name"] for s in result["mcpServers"]]
        self.assertIn("test", server_names)

    def test_returns_none_for_empty_toml(self):
        config_path = Path(self.tmp_dir) / "config.toml"
        config_path.write_text("# no mcp servers\n", encoding="utf-8")
        result = read_codex_toml_mcp_config(config_path)
        self.assertIsNone(result)

    def test_returns_none_on_permission_error(self):
        config_path = Path(self.tmp_dir) / "config.toml"
        config_path.write_text('[mcpServers.x]\ncmd = "y"\n', encoding="utf-8")
        with patch.object(Path, "read_text", side_effect=PermissionError("denied")):
            result = read_codex_toml_mcp_config(config_path)
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
