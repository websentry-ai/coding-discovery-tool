"""Battery for PR #293 — the shared JSONC BOM strip and the approvals mapping.

N1-N3 exercise the JSONC families the shared helper feeds beyond Copilot settings;
N4 is the backward-compat guard; N5/N6 are the cross-repo consumers.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

from coding_discovery_tools.mcp_extraction_helpers import (  # noqa: E402
    _strip_jsonc_comments, _strip_trailing_commas,
)
from coding_discovery_tools.coding_tool_base import BaseGitHubCopilotSettingsExtractor  # noqa: E402

BOM = "﻿"


class _Ex(BaseGitHubCopilotSettingsExtractor):
    def _scan_users(self, callback): pass
    def _user_config_dirs(self, user_home): return []


def _parse(raw):
    return json.loads(_strip_trailing_commas(_strip_jsonc_comments(raw)))


class N1_OtherJsoncFamilies(unittest.TestCase):
    """The strip lives in the shared helper so every family gets it. Copilot
    settings is the one that surfaced the bug; these are the other five."""

    def test_vscode_mcp_json_with_bom(self):
        raw = BOM + '{"servers": {"s": {"command": "node", "args": ["x.js"]}}}'
        self.assertEqual(set(_parse(raw)["servers"]), {"s"})

    def test_augment_settings_with_bom(self):
        from coding_discovery_tools.macos.augment import augment_settings_extractor as a
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "settings.json"
            p.write_bytes(BOM.encode() + b'{"mcpServers": {"a": {"command": "node"}}}')
            self.assertIn("mcpServers", a._parse_jsonc(p))

    def test_copilot_cli_settings_with_bom(self):
        from coding_discovery_tools.macos.copilot_cli import copilot_cli_settings_extractor as c
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "config.json"
            p.write_bytes(BOM.encode() + b'{"trusted_folders": ["/repo"]}')
            self.assertEqual(c._parse_jsonc(p)["trusted_folders"], ["/repo"])

    def test_copilot_cli_reexport_is_the_same_function(self):
        from coding_discovery_tools.macos.copilot_cli import mcp_config_extractor as m
        from coding_discovery_tools import mcp_extraction_helpers as h
        self.assertIs(m._strip_jsonc_comments, h._strip_jsonc_comments)


class N2_StripOrdering(unittest.TestCase):
    def test_bom_comments_and_trailing_commas_together(self):
        raw = BOM + '{ // lead\n "a": 1, /* mid */ "b": [1,2,], }'
        self.assertEqual(_parse(raw), {"a": 1, "b": [1, 2]})

    def test_bom_only_stripped_at_the_start(self):
        self.assertEqual(_parse('{"a": "x' + BOM + 'y"}')["a"], "x" + BOM + "y")


class N3_MalformedEncodings(unittest.TestCase):
    """A UTF-16 BOM is a different byte sequence; it must fail cleanly rather
    than be half-stripped into something that parses as the wrong thing."""

    def test_utf16_bom_is_not_silently_accepted(self):
        raw = b"\xff\xfe" + '{"a": 1}'.encode("utf-16-le")
        text = raw.decode("utf-8", errors="replace")
        with self.assertRaises(ValueError):
            _parse(text)

    def test_lone_bom_is_not_a_document(self):
        with self.assertRaises(ValueError):
            _parse(BOM)


class N4_BackwardCompat(unittest.TestCase):
    """The two added keys must not change the record for a file that has neither."""

    def test_pre_change_settings_yield_the_same_record(self):
        data = {"chat.tools.global.autoApprove": True,
                "chat.tools.terminal.autoApprove": {"git status": True, "rm": False},
                "chat.agent.sandbox.enabled": "on"}
        rec = _Ex()._build_record(data, Path("/x/settings.json"), "user")
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        self.assertEqual(rec["allow_rules"], ["Bash(git status *)"])
        self.assertEqual(rec["deny_rules"], ["Bash(rm *)"])
        self.assertTrue(rec["sandbox_enabled"])
        self.assertEqual(sorted(rec["raw_settings"]), sorted(data))

    def test_absent_new_keys_do_not_appear(self):
        rec = _Ex()._build_record({"chat.agent.enabled": True}, Path("/x/s.json"), "user")
        self.assertNotIn("chat.defaultConfiguration", rec["raw_settings"])


class N5_BackendContract(unittest.TestCase):
    """gateway-data validates settings_source and requires settings_path; the
    record must still satisfy that with the new keys present."""

    GW = Path("/Users/aakashvelusamy/github/ai-gateway-data")

    def test_record_satisfies_process_permissions_validation(self):
        if not self.GW.exists():
            self.skipTest("ai-gateway-data not on this machine")
        rec = _Ex()._build_record(
            {"chat.defaultConfiguration": {"approvals": "allowAll"},
             "chat.agentSessions.defaultConfiguration": {"approvals": "manual"}},
            Path("/Users/u/settings.json"), "user")
        self.assertIn(rec["settings_source"], ("user", "project", "managed"))
        self.assertTrue(rec["settings_path"])
        self.assertEqual(rec["permission_mode"], "bypassPermissions")
        # permission_mode is a CharField(max_length=50) in the backend model
        self.assertLessEqual(len(rec["permission_mode"]), 50)
        self.assertIsInstance(rec["raw_settings"], dict)


class N6_SecondParser(unittest.TestCase):
    """setup/ ships a PyInstaller entry with its OWN argparse that wraps main().
    It is the MDM/LaunchDaemon front door — a different parser for the same run."""

    ENTRY = Path("/Users/aakashvelusamy/github/setup/packaging/unbound_discovery_entry.py")

    def test_entry_exists_and_wraps_the_same_main(self):
        if not self.ENTRY.exists():
            self.skipTest("setup repo not on this machine")
        src = self.ENTRY.read_text(encoding="utf-8")
        self.assertIn("ai_tools_discovery", src)

    def test_entry_fails_open_without_config(self):
        # a root LaunchDaemon must idle cleanly, not exit non-zero, when unconfigured
        if not self.ENTRY.exists():
            self.skipTest("setup repo not on this machine")
        env = dict(os.environ)
        env.pop("UNBOUND_API_KEY", None)
        env["PYTHONPATH"] = str(REPO / "scripts")
        p = subprocess.run([sys.executable, str(self.ENTRY)], capture_output=True,
                           text=True, timeout=120, env=env)
        self.assertEqual(p.returncode, 0, f"fail-open broken: {p.stderr[-400:]}")


if __name__ == "__main__":
    unittest.main()
