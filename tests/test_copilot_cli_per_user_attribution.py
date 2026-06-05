"""Per-user attribution for GitHub Copilot CLI (FIX #2).

The CLI's ``install_path`` is a per-user ``~/.copilot`` owned by exactly one
user, but the per-user scan loop in ``main()`` re-emits every detected tool for
every OS user via ``filter_tool_projects_by_user``. That filter scopes a tool's
projects/permissions to the user but never rewrites ``install_path`` — so a
second user (e.g. ``gowshik_2``) who never had ``~/.copilot`` would still get a
phantom "GitHub Copilot CLI" install row pointing at ``gowshik``'s home.

These tests exercise the real ``AIToolsDetector.filter_tool_projects_by_user``
composed with the ownership gate ``_copilot_cli_owned_by_user`` (the exact pair
``main()`` runs), plus the gate's decision matrix directly.
"""

import unittest
from pathlib import Path

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import (
    AIToolsDetector,
    _copilot_cli_owned_by_user,
    _normalise_path,
)


def _cli_tool(install_path: str, projects=None, permissions=None) -> dict:
    tool = {
        "name": "GitHub Copilot CLI",
        "version": "unknown",
        "install_path": install_path,
        "projects": projects or [],
    }
    if permissions is not None:
        tool["permissions"] = permissions
    return tool


def _project(path: str) -> dict:
    return {"path": path, "rules": [], "mcpServers": [], "skills": []}


class TestCopilotCliOwnershipGate(unittest.TestCase):
    """The pure ownership decision: owns-install OR has-data => emit."""

    def test_owner_of_install_emits(self):
        tool = _cli_tool("/Users/gowshik/.copilot")
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/gowshik")))

    def test_non_owner_without_data_suppressed(self):
        tool = _cli_tool("/Users/gowshik/.copilot", projects=[])
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/gowshik_2")))

    def test_non_owner_with_projects_emits(self):
        tool = _cli_tool("/Users/gowshik/.copilot", projects=[_project("/Users/gowshik_2/dev/x")])
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/gowshik_2")))

    def test_non_owner_with_permissions_emits(self):
        tool = _cli_tool("/Users/gowshik/.copilot", permissions={"scope": "user"})
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/gowshik_2")))

    def test_prefix_sibling_is_not_owner(self):
        # /Users/gowshik must NOT be treated as owning /Users/gowshik_2's data.
        tool = _cli_tool("/Users/gowshik/.copilot")
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/gowshik_2")))

    def test_empty_install_path_not_owner(self):
        tool = _cli_tool("", projects=[])
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/anyone")))

    def test_windows_drive_case_insensitive_owner(self):
        tool = _cli_tool(r"C:\Users\Owner\.copilot")
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path(r"c:\Users\Owner")))


class TestNormalisePath(unittest.TestCase):
    def test_backslash_and_drive_and_trailing_slash(self):
        self.assertEqual(_normalise_path("c:\\Users\\x\\"), "C:/Users/x")
        self.assertEqual(_normalise_path("/Users/x/"), "/Users/x")
        self.assertEqual(_normalise_path(""), "")


class TestFilterThenGate(unittest.TestCase):
    """The real filter + gate composed — the gowshik repro end to end."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")

    def _emit(self, tool: dict, user_home: str) -> bool:
        """Mirror main(): filter to the user, then apply the CLI ownership gate."""
        filtered = self.detector.filter_tool_projects_by_user(tool, Path(user_home))
        return _copilot_cli_owned_by_user(filtered, Path(user_home))

    def test_owner_emits_and_keeps_projects(self):
        tool = _cli_tool(
            "/Users/gowshik/.copilot",
            projects=[_project("/Users/gowshik/dev/repo1"), _project("/Users/gowshik/dev/repo2")],
        )
        filtered = self.detector.filter_tool_projects_by_user(tool, Path("/Users/gowshik"))
        self.assertEqual(len(filtered["projects"]), 2)
        self.assertTrue(_copilot_cli_owned_by_user(filtered, Path("/Users/gowshik")))

    def test_non_owner_filters_to_empty_and_is_suppressed(self):
        # gowshik_2: gowshik's projects are NOT under /Users/gowshik_2 -> filtered out,
        # install_path still points at gowshik's home -> not owner + no data -> suppress.
        tool = _cli_tool(
            "/Users/gowshik/.copilot",
            projects=[_project("/Users/gowshik/dev/repo1")],
        )
        filtered = self.detector.filter_tool_projects_by_user(tool, Path("/Users/gowshik_2"))
        self.assertEqual(filtered["projects"], [])
        self.assertFalse(_copilot_cli_owned_by_user(filtered, Path("/Users/gowshik_2")))
        # And the owner is unaffected.
        self.assertTrue(self._emit(tool, "/Users/gowshik"))

    def test_second_user_with_own_install_and_data_still_emits(self):
        # Defensive: if the filter genuinely yields this user's own project, emit.
        tool = _cli_tool(
            "/Users/gowshik/.copilot",
            projects=[
                _project("/Users/gowshik/dev/repo1"),
                _project("/Users/gowshik_2/dev/repo9"),
            ],
        )
        self.assertTrue(self._emit(tool, "/Users/gowshik_2"))

    def test_permissions_under_user_home_keeps_emit(self):
        tool = _cli_tool(
            "/Users/gowshik/.copilot",
            projects=[],
            permissions={"scope": "user", "settings_path": "/Users/gowshik_2/.copilot/permissions-config.json"},
        )
        filtered = self.detector.filter_tool_projects_by_user(tool, Path("/Users/gowshik_2"))
        self.assertIn("permissions", filtered)
        self.assertTrue(_copilot_cli_owned_by_user(filtered, Path("/Users/gowshik_2")))


if __name__ == "__main__":
    unittest.main()
