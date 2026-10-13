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
        "settings_path": "/gov/settings.json",
        "raw_settings": {"chat.mcp.access": "registry"},
    }

    def _attach(self, name, existing=None):
        from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
        detector = AIToolsDetector.__new__(AIToolsDetector)
        detector._vscode_mcp_governance_cache = dict(self.GOV)
        result = {"name": name}
        if existing is not None:
            result["permissions"] = existing
        detector._attach_vscode_mcp_governance({"name": name}, result)
        return result.get("permissions") or {}

    def test_an_agent_with_no_record_gets_one(self):
        perms = self._attach("Roo Code (VS Code)")
        self.assertEqual("vscode_mcp_governance", perms["settings_source"])
        self.assertEqual("registry", perms["access"])

    def test_copilots_own_record_survives_and_gains_the_decision(self):
        """Replacing it would drop the terminal posture; skipping it would leave
        the decision readable only by digging through raw_settings."""
        own = {
            "settings_source": "vscode",
            "settings_path": "/real/settings.json",
            "permission_mode": "auto",
            "raw_settings": {"chat.tools.terminal.autoApprove": {"rm": True}},
        }
        perms = self._attach("GitHub Copilot Chat (VS Code)", own)
        self.assertEqual("vscode", perms["settings_source"])
        self.assertEqual("/real/settings.json", perms["settings_path"])
        self.assertEqual("auto", perms["permission_mode"])
        self.assertEqual(["chat.tools.terminal.autoApprove"], list(perms["raw_settings"]))
        self.assertEqual("registry", perms["access"])
        self.assertEqual(["evil-mcp"], perms["denied_servers"])

    def test_an_existing_value_is_not_clobbered(self):
        """If the editor's own extractor ever derives these, its answer wins."""
        perms = self._attach("GitHub Copilot Chat (VS Code)",
                             {"settings_source": "vscode", "access": "none"})
        self.assertEqual("none", perms["access"])

    def test_another_editors_row_is_untouched(self):
        """These settings belong to one editor; Cursor has its own."""
        self.assertEqual({}, self._attach("Roo Code (Cursor)"))
        self.assertEqual({}, self._attach("Cursor"))
        self.assertEqual({}, self._attach("Claude Code"))
