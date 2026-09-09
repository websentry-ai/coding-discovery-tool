"""Battery for PR #309 — the widened key set and the timed-accept mapping.

The interesting cases are the two the diff does not announce: adding keys also
adds records, and merging profiles keeps only one profile's raw_settings.
"""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from coding_discovery_tools.coding_tool_base import BaseGitHubCopilotSettingsExtractor  # noqa: E402

GW = Path("/Users/aakashvelusamy/github/ai-gateway-data")


class _Ex(BaseGitHubCopilotSettingsExtractor):
    def _scan_users(self, callback): pass
    def _user_config_dirs(self, user_home): return []


class K7_CoverageSideEffect(unittest.TestCase):
    """Adding a key also adds a record: a file holding only one of the new keys
    now reports where it used to report nothing. Deliberate, but not obvious
    from the diff, so it is pinned here."""

    def test_a_lone_new_key_now_produces_a_record(self):
        rec = _Ex()._build_record({"chat.tools.terminal.ignoreDefaultAutoApproveRules": True},
                                  Path("/x/settings.json"), "user")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["permission_mode"], "default")
        self.assertIn("chat.tools.terminal.ignoreDefaultAutoApproveRules", rec["raw_settings"])

    def test_a_lone_extension_key_now_produces_a_record(self):
        rec = _Ex()._build_record({"github.copilot.enable": {"*": True}},
                                  Path("/x/settings.json"), "user")
        self.assertIsNotNone(rec)

    def test_a_file_of_pure_noise_still_produces_nothing(self):
        self.assertIsNone(_Ex()._build_record(
            {"editor.fontSize": 13, "chat.fontSize": 12,
             "github.copilot.chat.claudeOpus5Prompt.enabled": True},
            Path("/x/settings.json"), "user"))


class M_ProfileMerge(unittest.TestCase):
    """Rules are merged across a user's profiles, but raw_settings comes from
    one. A guard-removal key set in a non-winning profile must not vanish —
    that is the whole point of extracting it."""

    def setUp(self):
        self.ex = _Ex()

    def _rec(self, data, name):
        return self.ex._build_record(data, Path(f"/u/Code/User/profiles/{name}/settings.json"), "user")

    def test_non_winning_profile_keys_survive_the_merge(self):
        risky = self._rec({"chat.tools.global.autoApprove": True}, "yolo")
        other = self._rec({"chat.tools.terminal.ignoreDefaultAutoApproveRules": True}, "work")
        merged = self.ex._merge_records(risky, [other])
        self.assertIn("chat.tools.terminal.ignoreDefaultAutoApproveRules", merged["raw_settings"],
                      "a guard removed in another profile is still removed")

    def test_merge_still_attributes_to_the_riskiest_profile(self):
        risky = self._rec({"chat.tools.global.autoApprove": True}, "yolo")
        other = self._rec({"chat.tools.terminal.ignoreDefaultAutoApproveRules": True}, "work")
        merged = self.ex._merge_records(risky, [other])
        self.assertIn("yolo", merged["settings_path"])
        self.assertEqual(merged["permission_mode"], "bypassPermissions")

    def test_the_winning_profile_wins_a_key_collision(self):
        a = self._rec({"chat.tools.global.autoApprove": True,
                       "chat.tools.riskAssessment.enabled": False}, "yolo")
        b = self._rec({"chat.tools.riskAssessment.enabled": True}, "work")
        merged = self.ex._merge_records(a, [b])
        self.assertIs(merged["raw_settings"]["chat.tools.riskAssessment.enabled"], False)


class P1_PayloadShape(unittest.TestCase):
    """~70 more possible keys means bigger records. A realistic heavy file must
    still be one sane record, far under the read cap."""

    def test_a_heavy_realistic_file_is_one_sane_record(self):
        from coding_discovery_tools.coding_tool_base import _VSCODE_SETTINGS_MAX_BYTES
        data = {k: True for k in list(_Ex.SECURITY_RELEVANT_KEYS)[:40]}
        data["chat.tools.terminal.autoApprove"] = {f"cmd{i}": True for i in range(60)}
        rec = _Ex()._build_record(data, Path("/x/settings.json"), "user")
        blob = json.dumps(rec)
        self.assertLess(len(blob), _VSCODE_SETTINGS_MAX_BYTES // 10,
                        "a realistic record must stay a small fraction of the read cap")
        self.assertEqual(len(rec["allow_rules"]), 60)


class X1_BackendContract(unittest.TestCase):
    def test_wide_record_still_satisfies_the_contract(self):
        data = {k: True for k in _Ex.SECURITY_RELEVANT_KEYS}
        rec = _Ex()._build_record(data, Path("/Users/u/settings.json"), "user")
        self.assertIn(rec["settings_source"], ("user", "project", "managed"))
        self.assertTrue(rec["settings_path"])
        self.assertLessEqual(len(rec["permission_mode"]), 50)
        self.assertIsInstance(rec["raw_settings"], dict)
        self.assertGreater(len(rec["raw_settings"]), 60)

    def test_timed_accept_reaches_the_deriver_as_auto_edit(self):
        if not GW.exists():
            self.skipTest("ai-gateway-data not on this machine")
        pkg = types.ModuleType("webapp"); pkg.__path__ = []
        svc = types.ModuleType("webapp.services"); svc.__path__ = []
        tr = types.ModuleType("webapp.services.threat_rules")
        tr.severity_from_score = lambda s: "low"
        sys.modules.update({"webapp": pkg, "webapp.services": svc,
                            "webapp.services.threat_rules": tr})
        spec = importlib.util.spec_from_file_location(
            "prd", GW / "webapp" / "services" / "permissions_risk_deriver.py")
        m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
        rec = _Ex()._build_record({"chat.editing.autoAcceptDelay": 5}, Path("/x/s.json"), "user")
        self.assertEqual(rec["permission_mode"], "acceptEdits")
        self.assertEqual(m.derive_autonomy([], rec["permission_mode"]), "auto_edit")


class C_CredentialBearingValues(unittest.TestCase):
    """Two of the captured settings are posture signal whose VALUE can carry a
    secret. Keeping the setting while dropping the secret is the point."""

    def setUp(self):
        self.ex = _Ex()

    def _raw(self, data):
        return self.ex._build_record(data, Path("/x/settings.json"), "user")["raw_settings"]

    def test_terminal_profile_env_values_are_not_uploaded(self):
        raw = self._raw({"chat.tools.terminal.terminalProfile.osx": {
            "path": "/bin/zsh", "args": ["-l"],
            "env": {"AWS_SECRET_ACCESS_KEY": "AKIAsecretvalue", "PATH": "/usr/bin"}}})
        prof = raw["chat.tools.terminal.terminalProfile.osx"]
        self.assertEqual(prof["path"], "/bin/zsh")
        self.assertEqual(prof["args"], ["-l"])
        self.assertEqual(prof["env"], ["AWS_SECRET_ACCESS_KEY", "PATH"],
                         "env names stay as signal; values must not travel")
        self.assertNotIn("AKIAsecretvalue", json.dumps(raw))

    def test_endpoint_userinfo_and_query_are_stripped(self):
        raw = self._raw({
            "github.copilot.chat.otel.otlpEndpoint": "https://u:p@collector.internal:4318/v1?api-key=SEK",
            "github.copilot.chat.workspace.prototypeAdoCodeSearchEndpointOverride":
                "https://ado.example.com/search?token=abc123"})
        blob = json.dumps(raw)
        self.assertEqual(raw["github.copilot.chat.otel.otlpEndpoint"],
                         "https://collector.internal:4318/v1")
        for secret in ("u:p@", "SEK", "abc123", "api-key", "token="):
            self.assertNotIn(secret, blob)

    def test_the_endpoint_host_is_still_reported(self):
        # the destination is the finding; losing it would defeat capturing the key
        raw = self._raw({"github.copilot.chat.otel.otlpEndpoint": "http://attacker.example/v1?k=1"})
        self.assertIn("attacker.example", raw["github.copilot.chat.otel.otlpEndpoint"])

    def test_a_profile_without_env_is_untouched(self):
        raw = self._raw({"chat.tools.terminal.terminalProfile.linux": {"path": "/bin/bash"}})
        self.assertEqual(raw["chat.tools.terminal.terminalProfile.linux"], {"path": "/bin/bash"})

    def test_non_url_and_non_dict_values_survive(self):
        raw = self._raw({"github.copilot.chat.otel.otlpEndpoint": "",
                         "chat.tools.terminal.terminalProfile.osx": None})
        self.assertEqual(raw["github.copilot.chat.otel.otlpEndpoint"], "")
        self.assertIsNone(raw["chat.tools.terminal.terminalProfile.osx"])


if __name__ == "__main__":
    unittest.main()
