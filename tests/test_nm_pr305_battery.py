"""Battery for PR #305 — the default-posture record and the sandbox key mappings.

The cases that matter here are the ones where an EXISTING user's report changes:
a sandbox value that was unknown and is now false, a legacy key that granted a
bypass and no longer does, and a record where there was none.
"""
import importlib.util
import json
import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from coding_discovery_tools.coding_tool_base import BaseGitHubCopilotSettingsExtractor  # noqa: E402
from coding_discovery_tools.coding_tool_factory import GitHubCopilotSettingsExtractorFactory  # noqa: E402

GW = Path("/Users/aakashvelusamy/github/ai-gateway-data")


def _deriver():
    """Load the real backend deriver, stubbing only its Django-bound import."""
    if not GW.exists():
        return None
    pkg = types.ModuleType("webapp"); pkg.__path__ = []
    svc = types.ModuleType("webapp.services"); svc.__path__ = []
    tr = types.ModuleType("webapp.services.threat_rules")
    tr.severity_from_score = lambda s: (
        "critical" if float(s) >= 9 else "high" if float(s) >= 7
        else "medium" if float(s) >= 4 else "low")
    sys.modules.update({"webapp": pkg, "webapp.services": svc,
                        "webapp.services.threat_rules": tr})
    spec = importlib.util.spec_from_file_location(
        "prd", GW / "webapp" / "services" / "permissions_risk_deriver.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class S_SandboxKeys(unittest.TestCase):
    def _rec(self, os_name, data):
        ex = GitHubCopilotSettingsExtractorFactory.create(os_name)
        return ex._build_record(data, Path("/x/settings.json"), "user")

    def test_windows_reads_its_own_key(self):
        self.assertTrue(self._rec("Windows", {"chat.agent.sandbox.enabledWindows": "on"})["sandbox_enabled"])

    def test_windows_reads_the_pre_rename_spelling(self):
        self.assertTrue(self._rec("Windows", {"chat.agent.sandbox.enabled.windows": "on"})["sandbox_enabled"])

    def test_windows_ignores_the_generic_key(self):
        self.assertFalse(self._rec("Windows", {"chat.agent.sandbox.enabled": "on"})["sandbox_enabled"])

    def test_posix_ignores_the_windows_key(self):
        self.assertFalse(self._rec("Darwin", {"chat.agent.sandbox.enabledWindows": "on"})["sandbox_enabled"])

    def test_absent_key_is_off_not_unknown(self):
        self.assertIs(self._rec("Darwin", {"chat.agent.enabled": True})["sandbox_enabled"], False)

    def test_still_callable_unbound(self):
        # staticmethod -> instance method is a signature change; an existing
        # caller reaching it through the class must not break
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        self.assertTrue(
            BaseGitHubCopilotSettingsExtractor._sandbox_enabled(ex, {"chat.agent.sandbox.enabled": "on"}))


class S7_SandboxScoringShift(unittest.TestCase):
    """absent -> False is a fleet-wide change: the deriver only counts an
    explicit False, so this newly raises Sandbox Disabled for every record."""

    def setUp(self):
        self.m = _deriver()
        if self.m is None:
            self.skipTest("ai-gateway-data not on this machine")

    def test_false_raises_the_factor_and_none_does_not(self):
        unknown = self.m.derive_permission_risk(allow_rules=[], deny_rules=["Read(.env)"],
                                                permission_mode="default", sandbox_enabled=None)
        known_off = self.m.derive_permission_risk(allow_rules=[], deny_rules=["Read(.env)"],
                                                  permission_mode="default", sandbox_enabled=False)
        self.assertNotIn("Sandbox Disabled", unknown["risk_factors"])
        self.assertIn("Sandbox Disabled", known_off["risk_factors"])
        self.assertEqual(known_off["risk_score"] - unknown["risk_score"], 1,
                         "the shift must be exactly +1, not a compounding change")


class S8_LegacyKeyScoreDrop(unittest.TestCase):
    """Removing the dead legacy key LOWERS a user's score. That is the intent,
    but it must be a visible, bounded drop rather than a silent one."""

    def setUp(self):
        self.m = _deriver()
        if self.m is None:
            self.skipTest("ai-gateway-data not on this machine")
        self.ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")

    def test_legacy_key_no_longer_grants_a_bypass(self):
        rec = self.ex._build_record({"chat.tools.autoApprove": True}, Path("/x/s.json"), "user")
        self.assertEqual(rec["permission_mode"], "default")

    def test_the_stale_value_is_still_reported(self):
        rec = self.ex._build_record({"chat.tools.autoApprove": True}, Path("/x/s.json"), "user")
        self.assertIn("chat.tools.autoApprove", rec["raw_settings"])

    def test_score_drops_by_bypass_plus_its_dependent_penalty(self):
        """The drop is 5, not the bypass weight of 4. Losing the bypass also
        loses "No deny rules present", because that penalty only applies when
        the config grants some capability to protect — and without the bypass
        an empty config grants none. Pinning 5 pins that dependency."""
        before = self.m.derive_permission_risk(allow_rules=[], deny_rules=[],
                                               permission_mode="bypassPermissions", sandbox_enabled=False)
        after = self.m.derive_permission_risk(allow_rules=[], deny_rules=[],
                                              permission_mode="default", sandbox_enabled=False)
        self.assertEqual((before["risk_score"], before["risk_level"]), (7, "high"))
        self.assertEqual((after["risk_score"], after["risk_level"]), (2, "low"))
        self.assertIn("No Deny Rules", before["risk_factors"])
        self.assertNotIn("No Deny Rules", after["risk_factors"])
        # Sandbox Disabled survives the change, so it is not what moved
        self.assertIn("Sandbox Disabled", after["risk_factors"])


class D_DefaultPosture(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="nm305-", dir=str(Path.home())))
        self.ud = self.home / "Library" / "Application Support" / "Code" / "User"

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def _extract(self):
        ex = GitHubCopilotSettingsExtractorFactory.create("Darwin")
        ex._scan_users = lambda cb: cb(self.home)
        return ex.extract_settings()

    def test_no_settings_file_reports_the_defaults(self):
        self.ud.mkdir(parents=True)
        rec = self._extract()
        self.assertEqual(rec["permission_mode"], "default")
        self.assertEqual(rec["raw_settings"], {})
        self.assertNotIn("allow_rules", rec)

    def test_irrelevant_keys_only_reports_the_defaults(self):
        self.ud.mkdir(parents=True)
        (self.ud / "settings.json").write_text(json.dumps({"editor.fontSize": 13}), encoding="utf-8")
        self.assertEqual(self._extract()["permission_mode"], "default")

    def test_unreadable_file_stays_unknown(self):
        self.ud.mkdir(parents=True)
        (self.ud / "settings.json").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(self._extract())

    def test_no_vscode_reports_nothing(self):
        self.assertIsNone(self._extract())

    def test_configured_user_is_unchanged(self):
        self.ud.mkdir(parents=True)
        (self.ud / "settings.json").write_text(
            json.dumps({"chat.tools.global.autoApprove": True}), encoding="utf-8")
        self.assertEqual(self._extract()["permission_mode"], "bypassPermissions")

    def test_settings_path_names_the_file_even_when_absent(self):
        self.ud.mkdir(parents=True)
        rec = self._extract()
        self.assertEqual(rec["settings_path"], str(self.ud / "settings.json"))
        self.assertFalse(Path(rec["settings_path"]).exists(),
                         "this is the case the backend has to tolerate")

    def test_default_record_satisfies_the_backend_contract(self):
        self.ud.mkdir(parents=True)
        rec = self._extract()
        self.assertIn(rec["settings_source"], ("user", "project", "managed"))
        self.assertTrue(rec["settings_path"])
        self.assertIsInstance(rec["raw_settings"], dict)
        self.assertLessEqual(len(rec["permission_mode"]), 50)

    def test_default_record_is_kept_for_its_user_and_dropped_for_another(self):
        from coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        self.ud.mkdir(parents=True)
        det = AIToolsDetector(os_name="Darwin")
        tool = {"name": "GitHub Copilot Chat (VS Code)", "projects": [],
                "permissions": self._extract()}
        mine = det.filter_tool_projects_by_user(tool, self.home)
        other = det.filter_tool_projects_by_user(tool, self.home.parent / "someone-else")
        self.assertIn("permissions", mine)
        self.assertNotIn("permissions", other)


if __name__ == "__main__":
    unittest.main()
