"""VS Code's MCP access decision, reported for every agent it binds.

VS Code decides which MCP servers any agent in the window may use, through its
own workbench settings. The rule is the editor's, so it has to reach every
VS Code-hosted agent we report -- not only Copilot, whose extractor happens to
be the thing that reads the file.
"""
import unittest

from scripts.coding_discovery_tools.vscode_mcp_governance import (
    GOVERNANCE_KEYS,
    governance_from_settings,
)


class TheEditorsDecisionIsRead(unittest.TestCase):

    def test_an_access_mode_is_reported(self):
        gov = governance_from_settings({"chat.mcp.access": "registry"})
        self.assertEqual("registry", gov["access"])
        self.assertTrue(gov["access_is_known"])
        self.assertEqual("vscode", gov["scope"])

    def test_a_denied_server_is_named(self):
        """The settings UI writes an object keyed by server name; a list-only
        reader would miss everything configured through it."""
        gov = governance_from_settings({"chat.mcp.deniedServers": {"evil": {}}})
        self.assertEqual(["evil"], gov["denied_servers"])

    def test_the_list_form_is_read_too(self):
        gov = governance_from_settings({"chat.mcp.allowedServers": ["a", "b"]})
        self.assertEqual(["a", "b"], gov["allowed_servers"])

    def test_an_unknown_mode_is_kept_and_flagged(self):
        """A value the workbench would not accept is still a posture worth
        seeing, so it is reported rather than coerced or dropped."""
        gov = governance_from_settings({"chat.mcp.access": "typo"})
        self.assertEqual("typo", gov["access"])
        self.assertFalse(gov["access_is_known"])

    def test_silence_is_not_permission(self):
        """A settings file saying nothing about MCP must yield no record. A
        record would read as 'every server allowed', which the admin never said."""
        self.assertIsNone(governance_from_settings({}))
        self.assertIsNone(governance_from_settings(
            {"chat.tools.terminal.autoApprove": {"rm": True}}))

    def test_a_malformed_file_does_not_raise(self):
        for value in (None, [], "string", 7):
            with self.subTest(value=repr(value)):
                self.assertIsNone(governance_from_settings(value))

    def test_only_the_mcp_keys_travel(self):
        """Another agent's row must not inherit Copilot's wider posture: only
        the editor-scoped MCP keys belong on it."""
        raw = {"chat.tools.terminal.autoApprove": {"rm": True},
               "chat.mcp.access": "none"}
        carried = {k: v for k, v in raw.items() if k in GOVERNANCE_KEYS}
        self.assertEqual({"chat.mcp.access": "none"}, carried)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheDecisionReachesEveryVSCodeRow(unittest.TestCase):
    """Attachment is driven through the real method every tool routes through."""

    GOV = {
        "scope": "vscode", "access": "registry", "access_is_known": True,
        "allowed_servers": ["github-mcp"], "denied_servers": ["evil-mcp"],
        "settings_source": "vscode_mcp_governance",
        "settings_path": "/home/alice/Code/User/settings.json",
        "raw_settings": {"chat.mcp.access": "registry"},
    }

    def _detector(self, records=None):
        from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        detector = AIToolsDetector.__new__(AIToolsDetector)
        detector._vscode_mcp_governance_cache = (
            [dict(self.GOV)] if records is None else records)
        return detector

    def _attach(self, name, result=None, records=None):
        result = result if result is not None else {"name": name}
        self._detector(records)._attach_vscode_mcp_governance({"name": name}, result)
        return result

    def test_an_agent_with_no_record_reports_per_user(self):
        """The record carries its own file's path, which is what the per-user
        report filter matches against a home directory."""
        res = self._attach("Roo Code (VS Code)")
        self.assertEqual("registry", res["permissions"]["access"])
        self.assertEqual(
            ["/home/alice/Code/User/settings.json"],
            [r["settings_path"] for r in res["_permissions_by_user"]])

    def test_each_user_keeps_their_own_decision(self):
        """An elevated scan must not credit one account's policy to another."""
        alice = dict(self.GOV)
        bob = dict(self.GOV, access="none",
                   settings_path="/home/bob/Code/User/settings.json",
                   raw_settings={"chat.mcp.access": "none"})
        res = self._attach("Roo Code (VS Code)", records=[alice, bob])
        by_path = {r["settings_path"]: r["access"] for r in res["_permissions_by_user"]}
        self.assertEqual("registry", by_path["/home/alice/Code/User/settings.json"])
        self.assertEqual("none", by_path["/home/bob/Code/User/settings.json"])

    def test_each_profile_stays_its_own_posture(self):
        """A union of a locked default and a permissive profile is a policy
        neither file declares, so they are reported separately."""
        default = dict(self.GOV, access="registry",
                       settings_path="/home/alice/Code/User/settings.json",
                       raw_settings={"chat.mcp.access": "registry"})
        named = dict(self.GOV, access="all",
                    settings_path="/home/alice/Code/User/profiles/x/settings.json",
                    raw_settings={"chat.mcp.access": "all"})
        res = self._attach("Roo Code (VS Code)", records=[default, named])
        self.assertEqual(["registry", "all"],
                         [r["access"] for r in res["_permissions_by_user"]])

    def test_an_existing_record_is_enriched_from_its_own_settings(self):
        """Copilot's records keep their fuller posture and gain the derived
        fields, each from the file it came from -- never another user's."""
        alice = {"settings_source": "vscode",
                 "settings_path": "/home/alice/Code/User/settings.json",
                 "permission_mode": "auto",
                 "raw_settings": {"chat.mcp.access": "registry",
                                  "chat.tools.terminal.autoApprove": {"rm": True}}}
        bob = {"settings_source": "vscode",
               "settings_path": "/home/bob/Code/User/settings.json",
               "raw_settings": {"chat.mcp.access": "none"}}
        res = self._attach("GitHub Copilot Chat (VS Code)",
                           result={"name": "GitHub Copilot Chat (VS Code)",
                                   "_permissions_by_user": [alice, bob],
                                   "permissions": alice})
        self.assertEqual("registry", alice["access"])
        self.assertEqual("none", bob["access"], "bob keeps his own decision")
        self.assertEqual("auto", alice["permission_mode"])
        self.assertIn("chat.tools.terminal.autoApprove", alice["raw_settings"])
        self.assertEqual("vscode", alice["settings_source"])
        self.assertEqual([alice, bob], res["_permissions_by_user"])

    def test_an_existing_value_is_not_clobbered(self):
        rec = {"settings_source": "vscode", "access": "all",
               "raw_settings": {"chat.mcp.access": "none"}}
        self._attach("GitHub Copilot Chat (VS Code)",
                     result={"name": "GitHub Copilot Chat (VS Code)",
                             "_permissions_by_user": [rec]})
        self.assertEqual("all", rec["access"])

    def test_another_editors_row_is_untouched(self):
        """These settings belong to one editor; Cursor has its own."""
        for name in ("Roo Code (Cursor)", "Cursor", "Claude Code"):
            with self.subTest(name=name):
                self.assertNotIn("permissions", self._attach(name))

    def test_no_governance_anywhere_adds_nothing(self):
        self.assertNotIn("permissions",
                         self._attach("Roo Code (VS Code)", records=[]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
