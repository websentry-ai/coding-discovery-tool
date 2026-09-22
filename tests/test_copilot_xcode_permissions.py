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
import coding_discovery_tools.macos.github_copilot_xcode.settings_extractor as sx  # noqa: E402
from coding_discovery_tools.macos.github_copilot_xcode.settings_extractor import (  # noqa: E402
    MacOSCopilotXcodeSettingsExtractor,
    _AUTOAPPROVAL_SUFFIX,
    _DEV_GROUP,
    _GENERAL_SUFFIX,
    _GLOBAL_INSTRUCTIONS_KEY,
    _MCP_JSON_RELATIVE,
    _MCP_KEY,
    _MCP_PREF_KEY,
    _PROD_GROUP,
    _SENSITIVE_FILES_KEY,
    _TERMINAL_KEY,
)


def _write_mcp_json(home: Path, obj) -> Path:
    """Write ~/.config/github-copilot/xcode/mcp.json for a home (raw text if str)."""
    path = home.joinpath(*_MCP_JSON_RELATIVE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
    return path


def _write_general_prefs(home: Path, data: dict, *, group: str = _PROD_GROUP) -> Path:
    """Write the general <group>.prefs.plist suite for a home."""
    return _write_suite(home, group, _GENERAL_SUFFIX, data)


def _fake_transform(obj, **kwargs):
    """Deterministic stand-in for transform_mcp_servers_to_array: preserves the
    name-keyed -> array shape (name added, env/headers dropped) without the live
    network scan the real helper performs."""
    out = []
    for name, cfg in (obj or {}).items():
        cfg = cfg if isinstance(cfg, dict) else {}
        out.append({"name": name, **{k: v for k, v in cfg.items() if k not in ("env", "headers")}})
    return out


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
            {"EnableAutoApproval": True, "TrustToolAnnotations": False},
        )
        self.assertIs(rec["raw_settings"]["EnableAutoApproval"], True)
        self.assertIs(rec["raw_settings"]["TrustToolAnnotations"], False)

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
        rec = self._rec({}, {"EnableAutoApproval": True})
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
            {"EnableAutoApproval": True},
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
            "EnableAutoApproval": True,
            "TrustToolAnnotations": True,
        })
        rec = _extractor_over(self.home).extract_settings()
        self.assertIsNotNone(rec)
        self.assertEqual(rec["mcp_tool_allowlist"], ["github-mcp"])
        self.assertIn("Bash(git status *)", rec["allow_rules"])
        self.assertIn("Edit(~/.aws/credentials)", rec["allow_rules"])
        self.assertIs(rec["raw_settings"]["EnableAutoApproval"], True)
        self.assertIs(rec["raw_settings"]["TrustToolAnnotations"], True)
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
                     {"EnableAutoApproval": True})
        rec = _extractor_over(self.home).extract_settings()
        self.assertIsNotNone(rec)
        self.assertIs(rec["raw_settings"]["EnableAutoApproval"], True)

    def test_multi_user_scan_surfaces_riskiest_user(self):
        homes = []
        try:
            for name, armed, mcp in (("benign", False, []), ("armed", True, ["x", "y"])):
                h = Path(tempfile.mkdtemp(prefix=f"copilot-xcode-{name}-"))
                _write_suite(h, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: mcp})
                _write_suite(h, _PROD_GROUP, _GENERAL_SUFFIX,
                             {"EnableAutoApproval": armed})
                homes.append(h)
            ex = CopilotXcodeSettingsExtractorFactory.create("Darwin")
            ex._scan_users = lambda cb: [cb(h) for h in homes]
            rec = ex.extract_settings()
            self.assertIs(rec["raw_settings"]["EnableAutoApproval"], True)
            self.assertIn("armed", rec["settings_path"])
        finally:
            for h in homes:
                shutil.rmtree(h, ignore_errors=True)


class TestDiscoveryWiring(unittest.TestCase):
    """The GitHub Copilot (Xcode) row carries the extracted permissions block."""

    def _detector(self, by_user):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._copilot_xcode_workspace_surfaces = lambda: {}  # don't walk the host .github tree
        ex = det._copilot_xcode_settings_extractor
        ex.extract_settings_by_user = lambda: by_user
        # This test isolates the permissions wiring; keep the other surfaces empty so
        # it never reads (or live-scans) a real Copilot-for-Xcode install on the host.
        ex.extract_mcp_projects = lambda: []
        ex.extract_rule_projects = lambda: []
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
    """Prove-fail regressions for the four auto-approval review findings. Each
    asserts the fixed behaviour and fails on the pre-fix code."""

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
                det._copilot_xcode_workspace_surfaces = lambda: {}  # don't walk the host .github tree
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
            {"EnableAutoApproval": True, "TrustToolAnnotations": True},
            Path("/x/auto.plist"),
        )
        self.assertTrue(set(rec).issubset(self.PINNED_KEYS),
                        f"keys outside the backend-accepted set: {set(rec) - self.PINNED_KEYS}")


class TestMcpServers(unittest.TestCase):
    """Configured MCP servers from ~/.config/github-copilot/xcode/mcp.json (with the
    GitHubCopilotMCPConfig .prefs mirror as fallback)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-xcode-mcp-"))
        self._transform = sx.transform_mcp_servers_to_array
        sx.transform_mcp_servers_to_array = _fake_transform  # avoid the live scan
        self.ex = MacOSCopilotXcodeSettingsExtractor()
        self.ex._scan_users = lambda cb: cb(self.home)

    def tearDown(self):
        sx.transform_mcp_servers_to_array = self._transform
        shutil.rmtree(self.home, ignore_errors=True)

    def test_happy_servers_from_mcp_json(self):
        _write_mcp_json(self.home, {"servers": {"github-mcp": {"command": "npx", "args": ["-y", "x"]}}})
        projects = self.ex.extract_mcp_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["path"], str(self.home))
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["github-mcp"])

    def test_mcpServers_key_also_accepted(self):
        _write_mcp_json(self.home, {"mcpServers": {"srv": {"url": "https://x"}}})
        projects = self.ex.extract_mcp_projects()
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["srv"])

    def test_pref_fallback_when_file_absent(self):
        _write_general_prefs(self.home, {_MCP_PREF_KEY: json.dumps({"servers": {"pref-mcp": {"url": "https://y"}}})})
        projects = self.ex.extract_mcp_projects()
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["pref-mcp"])

    def test_pref_fallback_bare_servers_map(self):
        # The pref can store the bare servers object (no "servers" wrapper).
        _write_general_prefs(self.home, {_MCP_PREF_KEY: json.dumps({"bare-mcp": {"command": "run"}})})
        projects = self.ex.extract_mcp_projects()
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["bare-mcp"])

    def test_file_wins_over_pref(self):
        _write_mcp_json(self.home, {"servers": {"file-mcp": {"command": "a"}}})
        _write_general_prefs(self.home, {_MCP_PREF_KEY: json.dumps({"servers": {"pref-mcp": {"command": "b"}}})})
        projects = self.ex.extract_mcp_projects()
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["file-mcp"])

    def test_missing_everything_omitted(self):
        self.assertEqual(self.ex.extract_mcp_projects(), [])

    def test_empty_servers_map_omitted(self):
        _write_mcp_json(self.home, {"servers": {}})
        self.assertEqual(self.ex.extract_mcp_projects(), [])

    def test_malformed_json_is_graceful(self):
        _write_mcp_json(self.home, "{ this is : not json ]")
        self.assertEqual(self.ex.extract_mcp_projects(), [])  # no throw

    def test_non_dict_json_is_graceful(self):
        _write_mcp_json(self.home, "[1, 2, 3]")
        self.assertEqual(self.ex.extract_mcp_projects(), [])

    def test_delegates_to_sibling_transform_for_shape_parity(self):
        # Shape parity with the VS Code Copilot / Cursor MCP extractors is by
        # construction: the same transform_mcp_servers_to_array normalizer is used.
        seen = {}
        sx.transform_mcp_servers_to_array = lambda obj, **k: seen.update(obj) or [{"name": n} for n in obj]
        try:
            _write_mcp_json(self.home, {"servers": {"github-mcp": {"command": "npx"}}})
            projects = self.ex.extract_mcp_projects()
        finally:
            sx.transform_mcp_servers_to_array = _fake_transform
        self.assertEqual(seen, {"github-mcp": {"command": "npx"}})
        self.assertEqual(set(projects[0].keys()), {"path", "mcpServers"})

    @unittest.skipUnless(os.name == "posix", "symlink semantics are POSIX-specific")
    def test_safe_read_symlinked_config_escaping_home_refused(self):
        outside = Path(tempfile.mkdtemp(prefix="copilot-xcode-mcp-evil-"))
        try:
            evil = outside / "github-copilot" / "xcode"
            evil.mkdir(parents=True)
            (evil / "mcp.json").write_text(json.dumps({"servers": {"attacker": {"command": "x"}}}))
            dotconfig = self.home / ".config"
            os.symlink(outside, dotconfig)  # ~/.config -> out-of-home tree
            self.assertEqual(self.ex.extract_mcp_projects(), [], "out-of-home config must be refused")
        finally:
            shutil.rmtree(outside, ignore_errors=True)


class TestGlobalInstructions(unittest.TestCase):
    """Global custom instruction (GlobalCopilotInstructions in the .prefs suite)."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-xcode-rules-"))
        self.ex = MacOSCopilotXcodeSettingsExtractor()
        self.ex._scan_users = lambda cb: cb(self.home)

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    RULE_KEYS = {"file_path", "file_name", "project_root", "content",
                 "size", "last_modified", "truncated", "scope"}

    def test_happy_global_instruction(self):
        _write_general_prefs(self.home, {_GLOBAL_INSTRUCTIONS_KEY: "Always write tests first."})
        projects = self.ex.extract_rule_projects()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["path"], str(self.home))
        rule = projects[0]["rules"][0]
        self.assertEqual(rule["content"], "Always write tests first.")
        self.assertEqual(rule["scope"], "user")
        self.assertEqual(rule["file_name"], _GLOBAL_INSTRUCTIONS_KEY)

    def test_rule_shape_matches_sibling_vocabulary(self):
        _write_general_prefs(self.home, {_GLOBAL_INSTRUCTIONS_KEY: "x"})
        rule = self.ex.extract_rule_projects()[0]["rules"][0]
        self.assertEqual(set(rule.keys()), self.RULE_KEYS)

    def test_empty_instruction_omitted(self):
        _write_general_prefs(self.home, {_GLOBAL_INSTRUCTIONS_KEY: "   "})
        self.assertEqual(self.ex.extract_rule_projects(), [])

    def test_missing_prefs_omitted(self):
        self.assertEqual(self.ex.extract_rule_projects(), [])

    def test_non_string_instruction_is_graceful(self):
        _write_general_prefs(self.home, {_GLOBAL_INSTRUCTIONS_KEY: {"unexpected": "dict"}})
        self.assertEqual(self.ex.extract_rule_projects(), [])

    def test_dev_suite_fallback(self):
        _write_general_prefs(self.home, {_GLOBAL_INSTRUCTIONS_KEY: "dev instruction"}, group=_DEV_GROUP)
        projects = self.ex.extract_rule_projects()
        self.assertEqual(projects[0]["rules"][0]["content"], "dev instruction")


class TestExpandedWiring(unittest.TestCase):
    """_process_copilot_xcode_tool attaches permissions + mcp servers + rules, each
    best-effort so one surface failing never drops the others or the row."""

    def _detector(self):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._copilot_xcode_workspace_surfaces = lambda: {}  # don't walk the host .github tree
        return det

    def test_all_three_surfaces_attached(self):
        det = self._detector()
        ex = det._copilot_xcode_settings_extractor
        ex.extract_settings_by_user = lambda: [{
            "permission_mode": "default", "settings_source": "user", "scope": "user",
            "settings_path": "/h", "raw_settings": {}, "mcp_tool_allowlist": ["appr"]}]
        ex.extract_mcp_projects = lambda: [{"path": "/h", "mcpServers": [{"name": "github-mcp"}]}]
        ex.extract_rule_projects = lambda: [{"path": "/h", "rules": [{"file_name": "GlobalCopilotInstructions", "scope": "user"}]}]
        row = det.process_single_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a", "projects": []})
        self.assertEqual(row["permissions"]["mcp_tool_allowlist"], ["appr"])
        self.assertEqual(len(row["projects"]), 1)
        proj = row["projects"][0]
        self.assertEqual(proj["path"], "/h")
        self.assertEqual([s["name"] for s in proj["mcpServers"]], ["github-mcp"])
        self.assertEqual(proj["rules"][0]["file_name"], "GlobalCopilotInstructions")

    def test_one_surface_failing_does_not_drop_the_others(self):
        det = self._detector()
        ex = det._copilot_xcode_settings_extractor

        def boom():
            raise RuntimeError("mcp exploded")

        ex.extract_settings_by_user = lambda: []
        ex.extract_mcp_projects = boom  # this surface throws
        ex.extract_rule_projects = lambda: [{"path": "/h", "rules": [{"file_name": "g", "scope": "user"}]}]
        row = det.process_single_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a", "projects": []})
        # The detection row survives, and the rules surface still lands.
        self.assertEqual(row["name"], "GitHub Copilot (Xcode)")
        self.assertEqual(len(row["projects"]), 1)
        self.assertEqual(row["projects"][0]["rules"][0]["file_name"], "g")


class TestGroupConsistency(unittest.TestCase):
    """A user with BOTH prod and dev groups must have every surface resolve to the
    same (prod-preferred) group — no prod permissions mixed with stale dev data."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="copilot-xcode-group-"))
        self._transform = sx.transform_mcp_servers_to_array
        sx.transform_mcp_servers_to_array = _fake_transform

    def tearDown(self):
        sx.transform_mcp_servers_to_array = self._transform
        shutil.rmtree(self.home, ignore_errors=True)

    def test_prod_active_group_never_mixes_dev_artifacts(self):
        # Prod is the real install: it has the autoApproval permission surface.
        _write_suite(self.home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["prod-approved"]})
        # Dev has a STALE .prefs with a different MCP-pref + instruction, and no
        # prod .prefs / mcp.json exist — so a per-surface prod->dev fallthrough
        # would pull these dev values into the prod row.
        _write_general_prefs(self.home, {
            _MCP_PREF_KEY: json.dumps({"servers": {"dev-mcp": {"command": "x"}}}),
            _GLOBAL_INSTRUCTIONS_KEY: "DEV-ONLY INSTRUCTION",
        }, group=_DEV_GROUP)

        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        det._copilot_xcode_workspace_surfaces = lambda: {}  # don't walk the host .github tree
        det._copilot_xcode_settings_extractor._scan_users = lambda cb: cb(self.home)
        row = det.process_single_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a", "projects": []})

        # Prod permissions are present…
        self.assertIn("permissions", row)
        self.assertEqual(row["permissions"]["mcp_tool_allowlist"], ["prod-approved"])
        # …and NO dev-group artifact leaks into any surface of the same row.
        blob = json.dumps(row)
        self.assertNotIn("dev-mcp", blob, "dev MCP pref must not leak into the prod row")
        self.assertNotIn("DEV-ONLY INSTRUCTION", blob, "dev instruction must not leak into the prod row")
        self.assertEqual(row["projects"], [], "prod has no MCP/instructions, so no projects")


class TestGroupHardening(unittest.TestCase):
    """Hardening battery for the single-active-group fix (581087e)."""

    def setUp(self):
        self._transform = sx.transform_mcp_servers_to_array
        sx.transform_mcp_servers_to_array = _fake_transform
        self._homes = []

    def tearDown(self):
        sx.transform_mcp_servers_to_array = self._transform
        for h in self._homes:
            shutil.rmtree(h, ignore_errors=True)

    def _home(self, prefix):
        h = Path(tempfile.mkdtemp(prefix=prefix))
        self._homes.append(h)
        return h

    @staticmethod
    def _mcp_names(projects, home):
        for p in projects:
            if p["path"] == str(home):
                return [s["name"] for s in p["mcpServers"]]
        return []

    @staticmethod
    def _rule_contents(projects, home):
        for p in projects:
            if p["path"] == str(home):
                return [r["content"] for r in p["rules"]]
        return []

    # G1 — multi-user scan: each user's surfaces resolve to that user's own active
    # group; a stray other-group suite in one user's home never leaks into their
    # row, and no user borrows another user's group. User A is a prod install with
    # a stray dev suite (the leak the fix closes; prod is preferred so dev is inert);
    # user B is a clean dev-only install.
    def test_g1_multi_user_mixed_group_isolation(self):
        # User A: PROD install (prod autoApproval permissions), plus a stray dev
        # .prefs whose values the pre-fix prod->dev fallthrough would surface.
        a = self._home("copilot-xcode-g1a-")
        _write_suite(a, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["A-prod-approved"]})
        _write_general_prefs(a, {
            _MCP_PREF_KEY: json.dumps({"servers": {"A-dev-mcp": {"command": "y"}}}),
            _GLOBAL_INSTRUCTIONS_KEY: "A DEV INSTRUCTION",
        }, group=_DEV_GROUP)

        # User B: a clean DEV-only install (dev autoApproval + dev .prefs).
        b = self._home("copilot-xcode-g1b-")
        _write_suite(b, _DEV_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["B-dev-approved"]})
        _write_general_prefs(b, {
            _MCP_PREF_KEY: json.dumps({"servers": {"B-dev-mcp": {"command": "x"}}}),
            _GLOBAL_INSTRUCTIONS_KEY: "B DEV INSTRUCTION",
        }, group=_DEV_GROUP)

        ex = MacOSCopilotXcodeSettingsExtractor()
        ex._scan_users = lambda cb: [cb(a), cb(b)]
        mcp = ex.extract_mcp_projects()
        rules = ex.extract_rule_projects()

        # A is prod-active: the stray dev suite is inert, so A has no MCP/instruction.
        self.assertEqual(self._mcp_names(mcp, a), [])
        self.assertEqual(self._rule_contents(rules, a), [])
        # B resolves entirely to its own dev install.
        self.assertEqual(self._mcp_names(mcp, b), ["B-dev-mcp"])
        self.assertEqual(self._rule_contents(rules, b), ["B DEV INSTRUCTION"])
        # A's inert dev-suite values never appear anywhere in the assembled surfaces.
        blob = json.dumps({"mcp": mcp, "rules": rules})
        self.assertNotIn("A-dev-mcp", blob)
        self.assertNotIn("A DEV INSTRUCTION", blob)
        # Permissions stay per-user (each record's settings_path is its own home).
        perms = {r["settings_path"]: r for r in ex.extract_settings_by_user()}
        a_perm = next(v for k, v in perms.items() if str(a) in k)
        b_perm = next(v for k, v in perms.items() if str(b) in k)
        self.assertEqual(a_perm["mcp_tool_allowlist"], ["A-prod-approved"])
        self.assertEqual(b_perm["mcp_tool_allowlist"], ["B-dev-approved"])

    # G2 — behavior-pinning (NOT prove-fail). Contract read from _read_mcp_servers:
    # ~/.config/github-copilot/xcode/mcp.json is group-INDEPENDENT (it belongs to the
    # install), so it is surfaced even when _active_group() is None (no group suite
    # at all). No exception is raised.
    def test_g2_stray_mcp_json_without_any_group_is_surfaced(self):
        home = self._home("copilot-xcode-g2-")
        _write_mcp_json(home, {"servers": {"stray-file-mcp": {"command": "x"}}})
        ex = MacOSCopilotXcodeSettingsExtractor()
        ex._scan_users = lambda cb: cb(home)
        # No group container exists, so _active_group is None.
        self.assertIsNone(ex._active_group(home))
        # …yet the shared file's servers are still surfaced, and nothing throws.
        self.assertEqual(self._mcp_names(ex.extract_mcp_projects(), home), ["stray-file-mcp"])
        # With no group and no file, instructions/permissions are simply empty.
        self.assertEqual(ex.extract_rule_projects(), [])
        self.assertEqual(ex.extract_settings_by_user(), [])

    # G4 — dev-active mirror: a dev-only install resolves every surface from dev, and
    # a stray prod artifact that is NOT a recognized suite (so _group_present(prod) is
    # False) never makes prod the active group nor appears in the row.
    def test_g4_dev_active_ignores_unrecognized_prod_artifact(self):
        home = self._home("copilot-xcode-g4-")
        _write_suite(home, _DEV_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["dev-approved"]})
        _write_general_prefs(home, {
            _MCP_PREF_KEY: json.dumps({"servers": {"dev-mcp": {"command": "x"}}}),
            _GLOBAL_INSTRUCTIONS_KEY: "DEV INSTRUCTION",
        }, group=_DEV_GROUP)
        # A stray prod file in the prod group container that is NOT one of the two
        # recognized suites (wrong basename), so it must not mark prod as present.
        stray = (home / "Library" / "Group Containers" / _PROD_GROUP
                 / "Library" / "Preferences")
        stray.mkdir(parents=True)
        with open(stray / f"{_PROD_GROUP}.unrelated.plist", "wb") as fh:
            plistlib.dump({_MCP_PREF_KEY: json.dumps({"servers": {"PROD-STRAY-MCP": {}}}),
                           _GLOBAL_INSTRUCTIONS_KEY: "PROD STRAY INSTRUCTION"}, fh)

        ex = MacOSCopilotXcodeSettingsExtractor()
        ex._scan_users = lambda cb: cb(home)
        self.assertEqual(ex._active_group(home), _DEV_GROUP)
        self.assertEqual(self._mcp_names(ex.extract_mcp_projects(), home), ["dev-mcp"])
        self.assertEqual(self._rule_contents(ex.extract_rule_projects(), home), ["DEV INSTRUCTION"])
        blob = json.dumps({
            "mcp": ex.extract_mcp_projects(),
            "rules": ex.extract_rule_projects(),
            "perms": ex.extract_settings_by_user(),
        })
        self.assertNotIn("PROD-STRAY-MCP", blob)
        self.assertNotIn("PROD STRAY INSTRUCTION", blob)


class TestToggleKeys(unittest.TestCase):
    """The local auto-approval master switch in the ``.prefs`` suite is
    ``EnableAutoApproval`` (a PreferenceKey), not the enterprise ``CopilotPolicy``
    keys — so it must be captured and it must drive the riskiest-user ranking."""

    def setUp(self):
        self._homes = []

    def tearDown(self):
        for h in self._homes:
            shutil.rmtree(h, ignore_errors=True)

    def _home(self, prefix):
        h = Path(tempfile.mkdtemp(prefix=prefix))
        self._homes.append(h)
        return h

    def test_enable_auto_approval_captured_and_ranks_riskiest_user_first(self):
        # Armed user: one approval + the master switch on.
        armed = self._home("copilot-xcode-armed-")
        _write_suite(armed, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["srv"]})
        _write_general_prefs(armed, {"EnableAutoApproval": True, "TrustToolAnnotations": False})
        # Unarmed user: the SAME single approval (equal rule count), switch off.
        unarmed = self._home("copilot-xcode-unarmed-")
        _write_suite(unarmed, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["srv"]})
        _write_general_prefs(unarmed, {"EnableAutoApproval": False})

        ex = MacOSCopilotXcodeSettingsExtractor()
        ex._scan_users = lambda cb: [cb(unarmed), cb(armed)]  # unarmed first on purpose
        records = ex.extract_settings_by_user()

        # The master switch is captured verbatim in the armed user's record…
        armed_rec = next(r for r in records if str(armed) in r["settings_path"])
        self.assertIs(armed_rec["raw_settings"].get("EnableAutoApproval"), True)
        # …and the armed user outranks the equal-rule-count unarmed user.
        self.assertIn(str(armed), records[0]["settings_path"],
                      "EnableAutoApproval=True must rank the riskiest user first")


@unittest.skipUnless(sys.platform == "darwin", "drives the real macOS Copilot rules walk")
class TestWorkspaceRulesAndSkills(unittest.TestCase):
    """_process_copilot_xcode_tool also captures the per-project ``.github`` config
    Copilot for Xcode reads: copilot-instructions.md + .github/instructions/* as
    rules, and .github/prompts/*.prompt.md as skills. Root-safe: the settings
    extractor is stubbed empty and the .github walk is rooted at a fixture."""

    def setUp(self):
        # Under the real home so the extractor's system-path skip (rejects /tmp)
        # does not drop the walk.
        self.root = Path(tempfile.mkdtemp(prefix="cx-ws-", dir=str(Path.home())))
        self.repo = self.root / "myrepo"
        gh = self.repo / ".github"
        gh.mkdir(parents=True)
        (gh / "copilot-instructions.md").write_text("# be kind", encoding="utf-8")
        (gh / "instructions").mkdir()
        (gh / "instructions" / "style.instructions.md").write_text("# style", encoding="utf-8")
        (gh / "prompts").mkdir()
        (gh / "prompts" / "refactor.prompt.md").write_text("# refactor", encoding="utf-8")
        # Surfaces Xcode does NOT read — must not appear on the row.
        (self.repo / "AGENTS.md").write_text("# agents", encoding="utf-8")
        (self.repo / ".claude" / "rules").mkdir(parents=True)
        (self.repo / ".claude" / "rules" / "c.md").write_text("# claude", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _row(self):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        det = AIToolsDetector(os_name="Darwin")
        ex = det._copilot_xcode_settings_extractor
        ex.extract_settings_by_user = lambda: []   # keep the row hermetic:
        ex.extract_mcp_projects = lambda: []        # no real host permissions/MCP/
        ex.extract_rule_projects = lambda: []       # global-instruction reads
        # Root the shared ".github" walk at the fixture instead of "/".
        rx = det._github_copilot_rules_extractor
        rx._extract_workspace_rules = (
            lambda root_path, pbr: rx._walk_for_github_directories(self.root, self.root, pbr, 0)
        )
        return det._process_copilot_xcode_tool(
            {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a"})

    def test_project_rules_and_prompt_skills_are_captured(self):
        row = self._row()
        proj = [p for p in row["projects"] if p["path"] == str(self.repo)]
        self.assertEqual(len(proj), 1, "the workspace project must appear on the Xcode row")
        rule_names = {r["file_name"] for r in proj[0]["rules"]}
        skill_names = {s["file_name"] for s in proj[0]["skills"]}
        # rules: copilot-instructions.md + path-specific instructions
        self.assertIn("copilot-instructions.md", rule_names)
        self.assertIn("style.instructions.md", rule_names)
        # skills: the prompt file (NOT lumped into rules)
        self.assertEqual(skill_names, {"refactor.prompt.md"})
        self.assertNotIn("refactor.prompt.md", rule_names)

    def test_non_xcode_github_surfaces_are_dropped(self):
        row = self._row()
        blob = json.dumps(row)
        # AGENTS.md and .claude/rules are collected by the shared walk but are not
        # Xcode surfaces, so they must not ride the Xcode row.
        self.assertNotIn("AGENTS.md", blob)
        self.assertNotIn("c.md", blob)


@unittest.skipUnless(sys.platform == "darwin", "Copilot for Xcode is macOS-only")
class TestGroupConsistencyE2E(unittest.TestCase):
    """G3: the prod-permissions + stray-dev-prefs fixture driven through the REAL
    tool-assembly function _process_copilot_xcode_tool, root-safe like B1."""

    def test_g3_assembly_row_never_mixes_dev_into_prod(self):
        import coding_discovery_tools.macos.github_copilot_xcode.settings_extractor as sxmod
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        home = Path(tempfile.mkdtemp(prefix="copilot-xcode-g3-"))
        transform = sxmod.transform_mcp_servers_to_array
        sxmod.transform_mcp_servers_to_array = _fake_transform
        try:
            _write_suite(home, _PROD_GROUP, _AUTOAPPROVAL_SUFFIX, {_MCP_KEY: ["prod-approved"]})
            _write_general_prefs(home, {
                _MCP_PREF_KEY: json.dumps({"servers": {"dev-mcp": {"command": "x"}}}),
                _GLOBAL_INSTRUCTIONS_KEY: "DEV-ONLY INSTRUCTION",
            }, group=_DEV_GROUP)
            # Root-safe (B1 pattern): force non-root and pin the scan to the fixture,
            # so the REAL extractor reads only this home regardless of privilege.
            with patch.dict(os.environ, {"HOME": str(home)}), \
                    patch.object(sxmod, "is_running_as_root", lambda: False), \
                    patch.object(sxmod, "scan_user_directories", lambda cb: cb(home)):
                det = AIToolsDetector(os_name="Darwin")
                det._copilot_xcode_workspace_surfaces = lambda: {}  # don't walk the host .github tree
                row = det._process_copilot_xcode_tool(
                    {"name": "GitHub Copilot (Xcode)", "version": "1", "install_path": "/a"})
            self.assertEqual(row["permissions"]["mcp_tool_allowlist"], ["prod-approved"])
            blob = json.dumps(row)
            self.assertNotIn("dev-mcp", blob, "dev MCP must not leak into the assembled prod row")
            self.assertNotIn("DEV-ONLY INSTRUCTION", blob, "dev instruction must not leak")
            self.assertEqual(row["projects"], [])
        finally:
            sxmod.transform_mcp_servers_to_array = transform
            shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
