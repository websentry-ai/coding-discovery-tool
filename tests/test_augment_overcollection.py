"""
Over-collection tests for Augment Code (macOS).

Augment ships three surfaces that share one ``~/.augment`` config. These tests
prove the shared config is attached to EXACTLY ONE (canonical) surface row, the
others stay bare, the canonical fallback order is CLI > VS Code > JetBrains, and
per-user attribution under a simulated root scan does not leak across users.
"""

import unittest
from unittest.mock import MagicMock

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import (
    AIToolsDetector,
    _augment_owned_by_user,
)


def _stub_detector():
    d = AIToolsDetector(os_name="Darwin")
    d._augment_mcp_extractor = MagicMock()
    d._augment_mcp_extractor.extract_mcp_config.return_value = {
        "projects": [{"path": "/Users/x/.augment", "mcpServers": [{"name": "srv"}], "scope": "user"}],
    }
    d._augment_rules_extractor = MagicMock()
    d._augment_rules_extractor.extract_all_augment_rules.return_value = [
        {"project_root": "/Users/x/.augment",
         "rules": [{"file_path": "/Users/x/.augment/user-guidelines.md", "file_name": "user-guidelines.md"}]},
    ]
    d._augment_skills_extractor = MagicMock()
    d._augment_skills_extractor.extract_all_skills.return_value = {"user_skills": [], "project_skills": []}
    d._augment_settings_extractor = MagicMock()
    d._augment_settings_extractor.extract_settings.return_value = [
        {"tool_name": "Augment Code", "scope": "user",
         "settings_path": "/Users/x/.augment/settings.json",
         "raw_settings": {"toolPermissions": []},
         "permissions": {"defaultMode": None, "allow": ["read"], "deny": [], "ask": [],
                         "additionalDirectories": []}},
    ]
    return d


_CLI = {"name": "Auggie CLI", "version": "0.30.0",
        "install_path": "/Users/x/.local/bin/auggie", "_config_path": "/Users/x/.augment"}
_VSC = {"name": "Augment (VS Code)", "version": "1.0",
        "install_path": "/Users/x/.vscode/extensions", "_config_path": "/Users/x/.augment"}
_JB = {"name": "Augment (IntelliJ IDEA)", "version": "2024.1", "ide": "IntelliJ IDEA",
       "install_path": "/cfg", "_config_path": "/Users/x/.augment"}


class TestAugmentCanonicalSplit(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_shared_config_on_exactly_one_row(self):
        d = _stub_detector()
        tools = [_CLI, _VSC, _JB]
        d._set_canonical_augment_surface(tools)
        results = {t["name"]: d.process_single_tool(t) for t in tools}

        # Canonical = Auggie CLI carries the shared config + permissions.
        cli = results["Auggie CLI"]
        self.assertTrue(cli["projects"])
        self.assertIn("permissions", cli)

        # The other two are bare: no projects, no permissions.
        for name in ("Augment (VS Code)", "Augment (IntelliJ IDEA)"):
            self.assertEqual(results[name]["projects"], [])
            self.assertNotIn("permissions", results[name])

    def test_canonical_fallback_vscode_when_cli_absent(self):
        d = _stub_detector()
        tools = [_VSC, _JB]
        d._set_canonical_augment_surface(tools)
        self.assertEqual(d._canonical_augment_surface, "augment (vs code)")
        results = {t["name"]: d.process_single_tool(t) for t in tools}
        self.assertTrue(results["Augment (VS Code)"]["projects"])
        self.assertEqual(results["Augment (IntelliJ IDEA)"]["projects"], [])

    def test_canonical_fallback_jetbrains_when_cli_and_vscode_absent(self):
        d = _stub_detector()
        tools = [_JB]
        d._set_canonical_augment_surface(tools)
        self.assertEqual(d._canonical_augment_surface, "augment (intellij idea)")
        result = d.process_single_tool(_JB)
        self.assertTrue(result["projects"])
        # JetBrains canonical row keeps its ``ide`` key but routed via Augment.
        self.assertEqual(result["ide"], "IntelliJ IDEA")

    def test_no_augment_surface_canonical_is_none(self):
        d = _stub_detector()
        d._set_canonical_augment_surface([{"name": "Cursor"}])
        self.assertIsNone(d._canonical_augment_surface)

    def test_memoized_extractors_run_once(self):
        """Even with three surfaces processed, the canonical row drives extraction;
        the shared walks run at most once per scan via the memo caches."""
        d = _stub_detector()
        tools = [_CLI, _VSC, _JB]
        d._set_canonical_augment_surface(tools)
        for t in tools:
            d.process_single_tool(t)
        # Only the canonical surface invokes the extractors, and the memo cache
        # collapses repeats — so at most one call each.
        self.assertEqual(d._augment_rules_extractor.extract_all_augment_rules.call_count, 1)
        self.assertEqual(d._augment_skills_extractor.extract_all_skills.call_count, 1)
        self.assertEqual(d._augment_mcp_extractor.extract_mcp_config.call_count, 1)


class TestAugmentPerUserAttribution(unittest.TestCase):
    """The ~/.augment-keyed ownership gate scopes a canonical row to its owner."""

    def test_owner_passes_gate(self):
        tool_filtered = {"name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
                         "install_path": "/Users/alice/.local/bin/auggie", "projects": []}
        self.assertTrue(_augment_owned_by_user(tool_filtered, "/Users/alice"))

    def test_non_owner_with_no_data_fails_gate(self):
        # bob is scanned but the config dir is alice's and there's no per-user data.
        tool_filtered = {"name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
                         "install_path": "/Users/alice/.local/bin/auggie", "projects": []}
        self.assertFalse(_augment_owned_by_user(tool_filtered, "/Users/bob"))

    def test_non_owner_with_per_user_data_passes_gate(self):
        # A user with per-user project data is kept even if the config dir differs.
        tool_filtered = {"name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
                         "install_path": "/Users/alice/.local/bin/auggie",
                         "projects": [{"path": "/Users/bob/repo", "rules": [{"x": 1}]}]}
        self.assertTrue(_augment_owned_by_user(tool_filtered, "/Users/bob"))


if __name__ == "__main__":
    unittest.main()
