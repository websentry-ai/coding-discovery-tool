"""Tests for the GitHub Copilot for Xcode auto-approval permission extractor.

Covers the suite -> backend-record mapping and the real macOS extractor driven
over a planted Group Containers tree (prod + dev suites, binary and malformed
plists), plus the discovery wiring that attaches the ``permissions`` block to the
``GitHub Copilot (Xcode)`` tool row.
"""

import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.coding_tool_factory import (  # noqa: E402
    CopilotXcodeSettingsExtractorFactory,
)
from coding_discovery_tools.macos.github_copilot_xcode.settings_extractor import (  # noqa: E402
    MacOSCopilotXcodeSettingsExtractor,
    _AUTOAPPROVAL_SUFFIX,
    _DEV_GROUP,
    _GENERAL_SUFFIX,
    _MCP_KEY,
    _PROD_GROUP,
    _SENSITIVE_FILES_KEY,
    _TERMINAL_KEY,
)


def _prefs_dir(home: Path, group: str) -> Path:
    return home / "Library" / "Group Containers" / group / "Library" / "Preferences"


def _write_suite(home: Path, group: str, suffix: str, data: dict, *, in_group: bool = True) -> Path:
    """Write ``<group>.<suffix>.plist`` for ``home``, in the group container by
    default or the non-group ``~/Library/Preferences`` fallback when asked."""
    basename = f"{group}.{suffix}.plist"
    directory = _prefs_dir(home, group) if in_group else home / "Library" / "Preferences"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / basename
    with open(path, "wb") as fh:
        plistlib.dump(data, fh)
    return path


def _extractor_over(home: Path) -> MacOSCopilotXcodeSettingsExtractor:
    ex = CopilotXcodeSettingsExtractorFactory.create("Darwin")
    ex._scan_users = lambda cb: cb(home)  # constrain to the fixture
    return ex


class TestFactory(unittest.TestCase):
    def test_macos_only(self):
        self.assertIsInstance(
            CopilotXcodeSettingsExtractorFactory.create("Darwin"),
            MacOSCopilotXcodeSettingsExtractor,
        )
        self.assertIsNone(CopilotXcodeSettingsExtractorFactory.create("Windows"))
        self.assertIsNone(CopilotXcodeSettingsExtractorFactory.create("Linux"))


class TestMapping(unittest.TestCase):
    """Suite values -> backend record, without touching the filesystem."""

    def setUp(self):
        self.ex = MacOSCopilotXcodeSettingsExtractor()

    def _rec(self, approvals, toggles=None):
        return self.ex._build_record(approvals, toggles or {}, Path("/x/auto.plist"))

    def test_mcp_list_becomes_allowlist(self):
        rec = self._rec({_MCP_KEY: ["github-mcp", "filesystem"]})
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp", "filesystem"])

    def test_terminal_list_becomes_bash_allow_rules(self):
        rec = self._rec({_TERMINAL_KEY: ["git status", "npm run build"]})
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Bash(npm run build *)", rec["allow_rules"])

    def test_sensitive_files_become_edit_rules(self):
        rec = self._rec({_SENSITIVE_FILES_KEY: ["~/.ssh/id_rsa"]})
        self.assertIn("Edit(~/.ssh/id_rsa)", rec["allow_rules"])

    def test_json_encoded_string_value_is_decoded(self):
        rec = self._rec({_TERMINAL_KEY: '["git status", "ls"]'})
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Bash(ls *)", rec["allow_rules"])

    def test_dict_keyed_approvals_use_keys_and_skip_withheld(self):
        rec = self._rec({
            _MCP_KEY: {"github-mcp": True, "shady": False},
            _SENSITIVE_FILES_KEY: {"/etc/hosts": {"approve": False}, "~/.env": {"approve": True}},
        })
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp"])
        self.assertIn("Edit(~/.env)", rec["allow_rules"])
        self.assertNotIn("Edit(/etc/hosts)", rec["allow_rules"])

    def test_list_of_named_objects(self):
        rec = self._rec({_MCP_KEY: [{"name": "github-mcp"}, {"server": "fs"}]})
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp", "fs"])

    def test_toggles_kept_in_raw_settings(self):
        rec = self._rec(
            {_MCP_KEY: ["x"]},
            {"agentMode.autoApproval.enabled": True, "cveRemediatorAgent.enabled": False},
        )
        self.assertIs(rec["raw_settings"]["agentMode.autoApproval.enabled"], True)
        self.assertIs(rec["raw_settings"]["cveRemediatorAgent.enabled"], False)

    def test_raw_approval_values_kept_for_audit(self):
        rec = self._rec({_TERMINAL_KEY: ["git status"]})
        self.assertEqual(rec["raw_settings"][_TERMINAL_KEY], ["git status"])

    def test_mode_stays_default_and_sandbox_none(self):
        rec = self._rec({_TERMINAL_KEY: ["git status"]})
        self.assertEqual(rec["permission_mode"], "default")
        self.assertIsNone(rec["sandbox_enabled"])

    def test_duplicate_rules_deduped(self):
        rec = self._rec({_TERMINAL_KEY: ["ls", "ls"]})
        self.assertEqual(rec["allow_rules"].count("Bash(ls *)"), 1)

    def test_empty_approvals_have_no_rule_fields(self):
        rec = self._rec({}, {"agentMode.autoApproval.enabled": True})
        self.assertNotIn("allow_rules", rec)
        self.assertNotIn("mcp_tool_allowlist", rec)


class TestBackendShapeParity(unittest.TestCase):
    """The record must stay within the Cursor/Copilot record vocabulary that
    gateway-data's AIToolPermissions ingest and the frontend already accept, so
    the Xcode permissions need no backend/frontend change."""

    CURSOR_RECORD_KEYS = {
        "settings_source", "scope", "settings_path", "raw_settings", "permission_mode",
        "sandbox_enabled", "allow_rules", "deny_rules", "mcp_tool_allowlist",
        "mcp_servers", "mcp_policies",
    }
    REQUIRED = {"permission_mode", "settings_source", "settings_path"}

    def test_record_shape_within_backend_vocabulary(self):
        rec = MacOSCopilotXcodeSettingsExtractor()._build_record(
            {_MCP_KEY: ["a"], _TERMINAL_KEY: ["ls"], _SENSITIVE_FILES_KEY: ["~/.env"]},
            {"agentMode.autoApproval.enabled": True},
            Path("/x/auto.plist"),
        )
        extra = set(rec) - self.CURSOR_RECORD_KEYS
        self.assertEqual(extra, set(), f"keys outside the backend-accepted shape: {extra}")
        self.assertTrue(self.REQUIRED.issubset(set(rec)))


class TestExtractorOverFixture(unittest.TestCase):
    """Drive the real extractor over a planted Group Containers tree."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-xcode-e2e-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_present_and_populated_prod(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {
            _MCP_KEY: ["github-mcp"],
            _TERMINAL_KEY: ["git status", "rm -rf"],
            _SENSITIVE_FILES_KEY: ["~/.aws/credentials"],
        })
        _write_suite(self.home, _PROD_GROUP, _GENERAL_SUFFIX, {
            "agentMode.autoApproval.enabled": True,
            "cveRemediatorAgent.enabled": True,
        })
        rec = _extractor_over(self.home).extract_settings()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp"])
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Edit(~/.aws/credentials)", rec["allow_rules"])
        self.assertIs(rec["raw_settings"]["agentMode.autoApproval.enabled"], True)
        self.assertIs(rec["raw_settings"]["cveRemediatorAgent.enabled"], True)
        self.assertIn(_PROD_GROUP, rec["settings_path"])

    def test_binary_plist_is_read(self):
        # plistlib.dump writes XML by default; force the binary wire format.
        path = _prefs_dir(self.home, _PROD_GROUP) / f"{_PROD_GROUP}.{_AUTOAPPROVAL_SUFFIX}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as fh:
            plistlib.dump({_MCP_KEY: ["github-mcp"]}, fh, fmt=plistlib.FMT_BINARY)
        rec = _extractor_over(self.home).extract_settings()
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp"])

    def test_suite_absent_returns_none(self):
        self.assertIsNone(_extractor_over(self.home).extract_settings())

    def test_malformed_plist_is_graceful(self):
        path = _prefs_dir(self.home, _PROD_GROUP) / f"{_PROD_GROUP}.{_AUTOAPPROVAL_SUFFIX}.plist"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x00 not a valid plist \xff\xfe garbage")
        # No parseable approvals and no toggles anywhere -> no row, no throw.
        self.assertIsNone(_extractor_over(self.home).extract_settings())

    def test_dev_suite_used_when_prod_absent(self):
        _write_suite(self.home, _DEV_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["dev-mcp"]})
        rec = _extractor_over(self.home).extract_settings()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["mcp_tool_allowlist"], ["dev-mcp"])
        self.assertIn(_DEV_GROUP, rec["settings_path"])

    def test_prod_preferred_over_dev(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["prod-mcp"]})
        _write_suite(self.home, _DEV_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["dev-mcp"]})
        rec = _extractor_over(self.home).extract_settings()
        self.assertEqual(rec["mcp_tool_allowlist"], ["prod-mcp"])
        self.assertIn(_PROD_GROUP, rec["settings_path"])

    def test_non_group_fallback_only_when_group_absent(self):
        # Group container missing -> the ~/Library/Preferences copy is read.
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["fallback"]},
                     in_group=False)
        rec = _extractor_over(self.home).extract_settings()
        self.assertEqual(rec["mcp_tool_allowlist"], ["fallback"])

    def test_group_container_wins_over_fallback(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["group"]})
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["fallback"]},
                     in_group=False)
        rec = _extractor_over(self.home).extract_settings()
        self.assertEqual(rec["mcp_tool_allowlist"], ["group"])

    def test_toggles_only_still_yields_a_row(self):
        # An armed master toggle with no global approvals is still a posture worth
        # reporting (auto-approval is on, even if nothing is pre-approved yet).
        _write_suite(self.home, _PROD_GROUP, _GENERAL_SUFFIX,
                     {"agentMode.autoApproval.enabled": True})
        rec = _extractor_over(self.home).extract_settings()
        self.assertIsNotNone(rec)
        self.assertIs(rec["raw_settings"]["agentMode.autoApproval.enabled"], True)

    def test_multi_user_scan_surfaces_riskiest_user(self):
        homes = []
        try:
            for name, armed, mcp in (("benign", False, []), ("armed", True, ["x", "y"])):
                h = Path(tempfile.mkdtemp(prefix=f"copilot-xcode-{name}-"))
                _write_suite(h, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: mcp})
                _write_suite(h, _PROD_GROUP, _GENERAL_SUFFIX,
                             {"agentMode.autoApproval.enabled": armed})
                homes.append(h)
            ex = CopilotXcodeSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: [cb(h) for h in homes]
            rec = ex.extract_settings()
            self.assertIs(rec["raw_settings"]["agentMode.autoApproval.enabled"], True)
            self.assertIn("armed", rec["settings_path"])
        finally:
            for h in homes:
                shutil.rmtree(h, ignore_errors=True)


class TestDiscoveryWiring(unittest.TestCase):
    """The GitHub Copilot (Xcode) row carries the extracted permissions block."""

    def _detector(self, by_user):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._copilot_xcode_settings_extractor.extract_settings_by_user = lambda: by_user
        return det

    def test_xcode_row_gets_permissions(self):
        det = self._detector([{
            "permission_mode": "default", "settings_source": "user", "scope": "user",
            "settings_path": "/x", "raw_settings": {}, "mcp_tool_allowlist": ["github-mcp"],
        }])
        row = det.process_single_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a", "projects": []})
        self.assertIn("permissions", row)
        self.assertEqual(row["permissions"]["mcp_tool_allowlist"], ["github-mcp"])
        self.assertEqual(row["projects"], [])

    def test_xcode_row_without_permissions_is_still_a_row(self):
        det = self._detector([])
        row = det.process_single_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a", "projects": []})
        self.assertNotIn("permissions", row)
        self.assertEqual(row["name"], "GitHub Copilot (Xcode)")


if __name__ == "__main__":
    unittest.main()
