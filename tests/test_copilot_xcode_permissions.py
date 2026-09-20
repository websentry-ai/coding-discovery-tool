"""Tests for the GitHub Copilot for Xcode auto-approval permission extractor.

Covers the suite -> backend-record mapping and the real macOS extractor driven
over a planted Group Containers tree (prod + dev suites, binary and malformed
plists), plus the discovery wiring that attaches the ``permissions`` block to the
``GitHub Copilot (Xcode)`` tool row.
"""

import datetime
import importlib.util
import json
import logging
import os
import plistlib
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class TestGreptileRegressions(unittest.TestCase):
    """Prove-fail regressions for the four PR #356 review findings. Each asserts
    the fixed behaviour and fails on the pre-fix code."""

    _LOGGER = "coding_discovery_tools.macos.github_copilot_xcode.settings_extractor"

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-xcode-reg-"))

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    # Finding 1 — a withheld list-object approval must not become active.
    def test_list_object_withheld_approval_is_excluded(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {
            _MCP_KEY: [{"name": "x", "approve": False}, {"name": "y", "approve": True}],
        })
        rec = _extractor_over(self.home).extract_settings()
        # Pre-fix: _name_of ignored the verdict, so "x" leaked into the allowlist.
        self.assertEqual(rec["mcp_tool_allowlist"], ["y"])

    # Finding 2 — a symlinked container must not redirect the read out of the home.
    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_symlinked_container_escaping_home_is_refused(self):
        # A parent component (the group container) is a symlink to an out-of-home
        # tree that holds an attacker-owned plist. lstat follows parent symlinks,
        # so the pre-fix reader would open and report it.
        outside = Path(tempfile.mkdtemp(prefix="copilot-xcode-evil-"))
        try:
            evil_prefs = outside / "Library" / "Preferences"
            evil_prefs.mkdir(parents=True)
            with open(evil_prefs / f"{_PROD_GROUP}.{_AUTOAPPROVAL_SUFFIX}.plist", "wb") as fh:
                plistlib.dump({_MCP_KEY: ["attacker-mcp"]}, fh)
            containers = self.home / "Library" / "Group Containers"
            containers.mkdir(parents=True)
            os.symlink(outside, containers / _PROD_GROUP)  # <group> -> outside tree
            rec = _extractor_over(self.home).extract_settings()
            # Refused: no record, and certainly not the attacker's content.
            self.assertIsNone(rec)
        finally:
            shutil.rmtree(outside, ignore_errors=True)

    # Finding 3 — non-JSON-safe plist values must not break json.dumps(record).
    def test_raw_settings_with_data_and_date_is_json_serializable(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {
            _SENSITIVE_FILES_KEY: {
                "~/.env": {
                    "approve": True,
                    "cert": b"\x00\x01\x02\xff",              # plist <data> -> bytes
                    "approvedAt": datetime.datetime(2026, 1, 2, 3, 4, 5),  # <date>
                },
            },
        })
        rec = _extractor_over(self.home).extract_settings()
        # Pre-fix: raw_settings held raw bytes/datetime and this raised TypeError.
        json.dumps(rec)  # must not raise
        self.assertIn("Edit(~/.env)", rec["allow_rules"])

    # Finding 4 — per-user extraction failures must log a traceback (exc_info).
    def test_per_user_failure_logs_traceback(self):
        ex = CopilotXcodeSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)

        def boom(_):
            raise RuntimeError("kaboom")

        ex._extract_for_user = boom
        with self.assertLogs(self._LOGGER, level=logging.DEBUG) as cm:
            self.assertEqual(ex.extract_settings_by_user(), [])
        # Pre-fix: logged at debug with no stack trace (exc_info unset on every record).
        self.assertTrue(any(r.exc_info for r in cm.records),
                        "the failure must be logged with a traceback (exc_info=True)")

    # Finding 2 (round 2) — a hard link to another user's plist must be refused.
    @unittest.skipUnless(os.name == "posix", "hard-link semantics are POSIX-specific")
    def test_multiply_linked_plist_is_refused(self):
        path = _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["x"]})
        # A second name for the same inode: st_nlink becomes 2, so the plist is a
        # regular file that clears containment yet may point at another user's data.
        os.link(path, path.parent / "second-name.plist")
        self.assertEqual(os.stat(path).st_nlink, 2)
        # Pre-fix: no nlink check, so the multiply-linked plist was read.
        self.assertIsNone(_extractor_over(self.home).extract_settings())

    # Finding 2 (round 2) — a plist owned by a different uid must be refused.
    def test_foreign_owned_plist_is_refused(self):
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["x"]})
        ex = _extractor_over(self.home)

        # Same-owner control: an unpatched read of this user's own plist succeeds.
        self.assertEqual(ex.extract_settings()["mcp_tool_allowlist"], ["x"])

        # A real chown needs root, so forge a foreign st_uid on the descriptor.
        real_fstat = os.fstat
        home_uid = os.stat(self.home).st_uid

        def foreign_fstat(fd):
            vals = list(real_fstat(fd))
            vals[4] = home_uid + 1  # st_uid index -> a uid the home does not own
            return os.stat_result(vals)

        os.fstat = foreign_fstat
        try:
            # Pre-fix: no ownership check, so the foreign-owned plist was read.
            self.assertIsNone(ex.extract_settings())
        finally:
            os.fstat = real_fstat


def _plant_xcode_install(home: Path, approvals: dict) -> None:
    """A user-scope Copilot-for-Xcode install the real detector fires from: the
    app bundle under ~/Applications plus the group-container marker + plist."""
    bundle = home / "Applications" / "GitHub Copilot for Xcode.app" / "Contents"
    bundle.mkdir(parents=True)
    with open(bundle / "Info.plist", "wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": "1.2.3"}, fh)
    _write_suite(home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, approvals)


class TestRoutingRegression(unittest.TestCase):
    """The new exact-match Xcode branch sits in front of the VS Code Copilot
    substring branch; these guard that it captures only the Xcode row."""

    def _detector(self):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._github_copilot_rules_extractor = None   # branch guards None -> skipped
        det._github_copilot_mcp_extractor = None
        det._get_copilot_cli_skills = lambda: {"user_skills": [], "project_skills": []}
        return det

    def _spy_xcode(self, det):
        calls = []
        original = det._process_copilot_xcode_tool

        def spy(tool):
            calls.append(tool)
            return original(tool)

        det._process_copilot_xcode_tool = spy
        return calls

    # A1 — VS Code Copilot still takes the VS Code settings path, not the Xcode one.
    def test_a1_vscode_copilot_routes_to_vscode_handler(self):
        det = self._detector()
        det._canonical_vscode_copilot = "github copilot (vs code)"
        vs_rec = {"permission_mode": "bypassPermissions", "settings_source": "user",
                  "scope": "user", "settings_path": "/x", "raw_settings": {}}
        det._github_copilot_settings_extractor.extract_settings_by_user = lambda: [vs_rec]
        det._copilot_xcode_settings_extractor.extract_settings_by_user = lambda: [{
            "permission_mode": "default", "settings_source": "user", "scope": "user",
            "settings_path": "/y", "raw_settings": {}, "mcp_tool_allowlist": ["XCODE-ONLY"]}]
        xcode_calls = self._spy_xcode(det)
        row = det.process_single_tool(
            {"name": "GitHub Copilot (VS Code)", "version": "1", "install_path": "/a", "projects": []})
        self.assertEqual(xcode_calls, [], "VS Code Copilot must not hit the Xcode handler")
        self.assertIn("permissions", row)
        self.assertEqual(row["permissions"]["permission_mode"], "bypassPermissions")
        self.assertNotIn("mcp_tool_allowlist", row["permissions"], "must be the VS Code record")

    # A2 — sibling tools still route to their own handlers with the new branch present.
    def test_a2_sibling_tools_route_to_their_handlers(self):
        import coding_discovery_tools.ai_tools_discovery as aitd
        det = self._detector()
        xcode_calls = self._spy_xcode(det)
        cli_calls, claude_calls, cursor_calls = [], [], []
        det._process_copilot_cli_tool = lambda t: (cli_calls.append(t) or {"name": t["name"], "projects": []})
        det._process_claude_code_tool = lambda t: (claude_calls.append(t) or {})
        det._process_tool_with_rules_and_mcp = lambda tool, *a, **k: (cursor_calls.append(tool) or {})

        r_cli = det.process_single_tool(
            {"name": "GitHub Copilot CLI", "version": "1", "install_path": "/c", "projects": []})
        r_claude = det.process_single_tool(
            {"name": "Claude Code", "version": "1", "install_path": "/d", "projects": []})
        home = Path(tempfile.mkdtemp(prefix="copilot-xcode-route-"))
        try:
            with patch.dict(os.environ, {"HOME": str(home)}), \
                    patch.object(aitd, "get_all_users_macos", lambda: []):
                r_cursor = det.process_single_tool(
                    {"name": "Cursor", "version": "1", "install_path": "/e", "projects": []})
        finally:
            shutil.rmtree(home, ignore_errors=True)

        self.assertEqual(xcode_calls, [], "no sibling tool may reach the Xcode handler")
        self.assertEqual((len(cli_calls), len(claude_calls)), (1, 1))
        self.assertTrue(cursor_calls, "Cursor must route to its rules/MCP handler")
        self.assertEqual(r_cli["name"], "GitHub Copilot CLI")
        self.assertEqual(r_cursor["name"], "Cursor")

    # A3 — prove-fail guard: loosening the route to the substring re-hijacks VS Code.
    def test_a3_vscode_copilot_never_gets_xcode_permissions(self):
        det = self._detector()
        det._canonical_vscode_copilot = "github copilot (vs code)"
        det._github_copilot_settings_extractor.extract_settings_by_user = lambda: []  # VS Code path: none
        det._copilot_xcode_settings_extractor.extract_settings_by_user = lambda: [{
            "permission_mode": "default", "settings_source": "user", "scope": "user",
            "settings_path": "/y", "raw_settings": {}, "mcp_tool_allowlist": ["XCODE-ONLY-SENTINEL"]}]
        row = det.process_single_tool(
            {"name": "GitHub Copilot (VS Code)", "version": "1", "install_path": "/a", "projects": []})
        self.assertNotEqual(row.get("permissions", {}).get("mcp_tool_allowlist"),
                            ["XCODE-ONLY-SENTINEL"],
                            "VS Code Copilot must never receive Xcode auto-approval permissions")
        self.assertNotIn("permissions", row)


@unittest.skipUnless(sys.platform == "darwin", "Copilot for Xcode is macOS-only")
class TestRealEntrypointE2E(unittest.TestCase):
    """Highest real assembly entrypoint: the real detector fires from a planted
    user-scope install, then the real extractor + real plist flow through
    process_single_tool with nothing about the extractor mocked.

    macOS-only: it drives the real ``MacOSGitHubCopilotXcodeDetector`` over
    macOS-shaped paths (``~/Applications/…app``, the group container, ``os.link``),
    so it is skipped off Darwin exactly like the sibling POSIX/macOS e2e tests.

    Not the ``python -m …ai_tools_discovery --payload`` module run: a full scan
    walks the whole real filesystem for every other tool (slow, non-hermetic),
    and the detector's bundle probe is satisfied by a ~/Applications bundle, so
    detect()+process_single_tool is driven directly against a controlled HOME."""

    def test_b1_real_detect_and_assemble_carry_planted_permissions(self):
        import coding_discovery_tools.macos.github_copilot_xcode.settings_extractor as sx
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        from coding_discovery_tools.coding_tool_factory import ToolDetectorFactory
        home = Path(tempfile.mkdtemp(prefix="copilot-xcode-e2e-real-"))
        try:
            _plant_xcode_install(home, {
                _MCP_KEY: ["github-mcp"],
                _TERMINAL_KEY: ["git status"],
            })
            # Deterministic regardless of privilege: under root the extractor's
            # privileged path would enumerate the host's real /Users and skip the
            # fixture, so pin the scan to the planted HOME (force non-root, and make
            # the all-users enumerator yield only the fixture). The extractor's own
            # plist read/parse/map stays real — nothing about it is mocked.
            with patch.dict(os.environ, {"HOME": str(home)}), \
                    patch.object(sx, "is_running_as_root", lambda: False), \
                    patch.object(sx, "scan_user_directories", lambda cb: cb(home)):
                det = AIToolsDetector(os_name="Darwin")  # real xcode extractor inside
                detector = ToolDetectorFactory.create_copilot_xcode_detector("Darwin")
                detector.user_home = home  # scan the planted install, not the real machine
                detected = detector.detect()
                self.assertIsNotNone(detected, "the real detector must fire from the planted bundle")
                self.assertEqual(detected["name"], "GitHub Copilot (Xcode)")
                row = det.process_single_tool(detected)
            self.assertIn("permissions", row)
            self.assertEqual(row["permissions"]["mcp_tool_allowlist"], ["github-mcp"])
            self.assertIn("Bash(git status *)", row["permissions"]["allow_rules"])
        finally:
            shutil.rmtree(home, ignore_errors=True)


class TestAbuseAndStress(unittest.TestCase):
    """Adversarial and outsized approval values stay bounded and well-formed."""

    def setUp(self):
        self.ex = MacOSCopilotXcodeSettingsExtractor()

    def _rec(self, approvals):
        return self.ex._build_record(approvals, {}, Path("/x/auto.plist"))

    # C2 — a command with shell metacharacters/newline/quotes cannot break the record.
    def test_c2_shell_metacharacters_stay_inside_one_allow_rule(self):
        nasty = 'git commit -m "x"; rm -rf / & echo `whoami`\n$(id)'
        rec = self._rec({_TERMINAL_KEY: [nasty]})
        self.assertEqual(rec["allow_rules"], [f"Bash({nasty} *)"])
        # The raw value is preserved verbatim and nothing leaked into another field.
        self.assertEqual(rec["raw_settings"][_TERMINAL_KEY], [nasty])
        self.assertNotIn("mcp_tool_allowlist", rec)
        self.assertNotIn("deny_rules", rec)
        json.dumps(rec)  # structure intact and serializable

    # C3 — a bare-scalar approval value, and a very large list, degrade gracefully.
    def test_c3_scalar_value_is_ignored_not_thrown(self):
        rec = self._rec({_MCP_KEY: 42, _TERMINAL_KEY: "plainword-not-json"})
        self.assertNotIn("mcp_tool_allowlist", rec)
        self.assertNotIn("allow_rules", rec)
        self.assertEqual(rec["raw_settings"][_MCP_KEY], 42)
        json.dumps(rec)

    def test_c3_large_approval_list_is_bounded_and_serializable(self):
        cmds = [f"cmd{i}" for i in range(5000)]
        rec = self._rec({_TERMINAL_KEY: cmds})
        self.assertEqual(len(rec["allow_rules"]), 5000)
        self.assertEqual(rec["allow_rules"][0], "Bash(cmd0 *)")
        json.dumps(rec)


class TestCrossRepoShapeParity(unittest.TestCase):
    """gateway-data's AIToolPermissions ingest needs no change for this new tool:
    the Xcode record's keys are a subset of the very set the VS Code parity test
    pins. Reuses that existing fixture rather than a private copy."""

    @classmethod
    def setUpClass(cls):
        vs_path = os.path.join(os.path.dirname(__file__), "test_copilot_vscode_permissions.py")
        spec = importlib.util.spec_from_file_location("test_copilot_vscode_permissions", vs_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.PINNED_KEYS = module.TestBackendShapeParity.CURSOR_RECORD_KEYS

    def test_d1_record_keys_subset_of_backend_accepted(self):
        rec = MacOSCopilotXcodeSettingsExtractor()._build_record(
            {_MCP_KEY: ["a"], _TERMINAL_KEY: ["ls"], _SENSITIVE_FILES_KEY: ["~/.env"]},
            {"agentMode.autoApproval.enabled": True, "cveRemediatorAgent.enabled": True},
            Path("/x/auto.plist"),
        )
        self.assertTrue(set(rec).issubset(self.PINNED_KEYS),
                        f"keys outside the backend-accepted set: {set(rec) - self.PINNED_KEYS}")


if __name__ == "__main__":
    unittest.main()
