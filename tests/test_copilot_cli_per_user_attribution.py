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


def _cli_tool(install_path: str, projects=None, permissions=None, config_path=None) -> dict:
    tool = {
        "name": "GitHub Copilot CLI",
        "version": "unknown",
        "install_path": install_path,
        "projects": projects or [],
    }
    if config_path is not None:
        tool["config_path"] = config_path
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

    def test_machine_global_binary_owned_via_config_path(self):
        # Regression: install_path is now the BINARY. For a machine-global install
        # (Homebrew) the binary lives OUTSIDE any user home, so ownership must key
        # on config_path (~/.copilot under user x), not the binary — otherwise the
        # real owner would be wrongly suppressed.
        tool = _cli_tool(
            "/opt/homebrew/bin/copilot",        # binary, outside any home
            config_path="/Users/x/.copilot",    # config dir under user x
        )
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/x")))
        # And a different user (no config dir under their home, no data) is still suppressed.
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/y")))

    def test_config_path_preferred_over_binary_install_path(self):
        # Even when install_path happens to sit under a user's home (e.g. a
        # user-local ~/.local/bin/copilot), ownership is decided by config_path.
        tool = _cli_tool(
            "/Users/x/.local/bin/copilot",
            config_path="/Users/x/.copilot",
        )
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/x")))
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/y")))

    def test_falls_back_to_install_path_when_no_config_path(self):
        # Older payloads with no config_path: ownership still works off the legacy
        # install_path (~/.copilot) so pre-config_path behavior is preserved.
        tool = _cli_tool("/Users/x/.copilot")  # no config_path
        self.assertTrue(_copilot_cli_owned_by_user(tool, Path("/Users/x")))
        self.assertFalse(_copilot_cli_owned_by_user(tool, Path("/Users/y")))


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

    def test_filter_carries_config_path_for_machine_global_binary(self):
        # The exact main() pair for a machine-global binary install: the filter
        # must carry config_path through (it does a shallow tool.copy()), so the
        # gate can attribute the row to the config-dir owner and suppress others.
        tool = _cli_tool(
            "/opt/homebrew/bin/copilot",        # machine-global binary
            projects=[],
            config_path="/Users/gowshik/.copilot",
        )
        filtered_owner = self.detector.filter_tool_projects_by_user(tool, Path("/Users/gowshik"))
        self.assertEqual(filtered_owner.get("config_path"), "/Users/gowshik/.copilot")
        self.assertTrue(self._emit(tool, "/Users/gowshik"))
        # A second user with no config dir under their home and no data: suppressed.
        self.assertFalse(self._emit(tool, "/Users/gowshik_2"))


class TestProcessSingleToolCarriesConfigPath(unittest.TestCase):
    """Integration across the REAL boundary the unit tests above skipped.

    The unit tests hand-build the tool dict already containing ``config_path``;
    they never exercise ``process_single_tool`` -> ``_process_copilot_cli_tool``,
    which rebuilds the dict and is where ``config_path`` was being DROPPED. This
    runs the exact main() chain — ``process_single_tool`` ->
    ``filter_tool_projects_by_user`` -> ``_copilot_cli_owned_by_user`` — for a
    machine-global binary install with zero projects/permissions, the case that
    was wrongly suppressed for the legitimate owner.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")
        # Neutralize the four CLI extractors so process_single_tool does NOT touch
        # the real filesystem — the result is deterministic on any CI OS. Each
        # extractor branch guards on truthiness, so None => "not available" + skip.
        self.detector._copilot_cli_mcp_extractor = None
        self.detector._copilot_cli_rules_extractor = None
        self.detector._copilot_cli_settings_extractor = None
        self.detector._copilot_cli_skills_extractor = None
        # Pre-seed the memoized skills cache so _get_copilot_cli_skills is a no-op.
        self.detector._copilot_cli_skills_cache = {}

    def _detection_tool(self) -> dict:
        # Mirrors copilot_cli._detect_for_user: install_path is the BINARY,
        # config_path is the resolved ~/.copilot under the owner's home.
        return {
            "name": "GitHub Copilot CLI",
            "version": "0.0.1",
            "publisher": "GitHub",
            "install_path": "/opt/homebrew/bin/copilot",
            "config_path": "/Users/owner/.copilot",
        }

    def _emit(self, processed: dict, user_home: str) -> bool:
        """The main() pair: filter to the user, then apply the CLI ownership gate."""
        filtered = self.detector.filter_tool_projects_by_user(processed, Path(user_home))
        return _copilot_cli_owned_by_user(filtered, Path(user_home))

    def test_process_single_tool_preserves_config_path(self):
        # The regression: the rebuilt result dict MUST carry config_path forward.
        processed = self.detector.process_single_tool(self._detection_tool())
        self.assertEqual(processed.get("config_path"), "/Users/owner/.copilot")
        # install_path stays the binary (the detection gate), unchanged.
        self.assertEqual(processed.get("install_path"), "/opt/homebrew/bin/copilot")
        # Zero projects/permissions: this is the machine-global, no-data case.
        self.assertEqual(processed.get("projects"), [])
        self.assertNotIn("permissions", processed)

    def test_owner_emitted_through_full_chain(self):
        # Owner of ~/.copilot, machine-global binary, zero projects -> EMIT.
        processed = self.detector.process_single_tool(self._detection_tool())
        self.assertTrue(self._emit(processed, "/Users/owner"))

    def test_sibling_suppressed_through_full_chain(self):
        # A different OS user shares the machine-global binary but owns no config
        # dir under their home and has no per-user data -> SUPPRESS (no phantom row).
        processed = self.detector.process_single_tool(self._detection_tool())
        self.assertFalse(self._emit(processed, "/Users/sibling"))


if __name__ == "__main__":
    unittest.main()
