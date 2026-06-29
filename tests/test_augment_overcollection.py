"""
Over-collection tests for Augment Code (macOS).

Augment ships three surfaces that share one ``~/.augment`` config. These tests
prove the shared config is attached to EXACTLY ONE (canonical) surface row, the
others stay bare, the canonical fallback order is CLI > VS Code > JetBrains, and
per-user attribution under a simulated root scan does not leak across users.
"""

import unittest
from pathlib import Path
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
        self.assertEqual(
            d._canonical_augment_surface_by_config["/Users/x/.augment"],
            "augment (vs code)",
        )
        results = {t["name"]: d.process_single_tool(t) for t in tools}
        self.assertTrue(results["Augment (VS Code)"]["projects"])
        self.assertEqual(results["Augment (IntelliJ IDEA)"]["projects"], [])

    def test_canonical_fallback_jetbrains_when_cli_and_vscode_absent(self):
        d = _stub_detector()
        tools = [_JB]
        d._set_canonical_augment_surface(tools)
        self.assertEqual(
            d._canonical_augment_surface_by_config["/Users/x/.augment"],
            "augment (intellij idea)",
        )
        result = d.process_single_tool(_JB)
        self.assertTrue(result["projects"])
        # JetBrains canonical row keeps its ``ide`` key but routed via Augment.
        self.assertEqual(result["ide"], "IntelliJ IDEA")

    def test_no_augment_surface_canonical_is_empty(self):
        d = _stub_detector()
        d._set_canonical_augment_surface([{"name": "Cursor"}])
        self.assertEqual(d._canonical_augment_surface_by_config, {})

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


class TestAugmentPerUserCanonical(unittest.TestCase):
    """FIX G: canonical surface is chosen PER USER (keyed by ``_config_path``).

    The old single global-string model would mark only CLI rows canonical when ANY
    user had the CLI — so a DIFFERENT user with VS Code ONLY got a bare row and lost
    their config. These assert each user's config is independently canonicalised.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def _multi_user_detector(self):
        """Root scan: user A has CLI + VS Code under configA; user B has VS Code
        ONLY under configB. The shared extractors return per-config data."""
        d = AIToolsDetector(os_name="Darwin")
        d._augment_mcp_extractor = MagicMock()
        d._augment_mcp_extractor.extract_mcp_config.return_value = None
        d._augment_rules_extractor = MagicMock()
        # Rules for BOTH users, each keyed under their own ~/.augment.
        d._augment_rules_extractor.extract_all_augment_rules.return_value = [
            {"project_root": "/Users/alice/.augment",
             "rules": [{"file_path": "/Users/alice/.augment/user-guidelines.md",
                        "file_name": "user-guidelines.md"}]},
            {"project_root": "/Users/bob/.augment",
             "rules": [{"file_path": "/Users/bob/.augment/user-guidelines.md",
                        "file_name": "user-guidelines.md"}]},
        ]
        d._augment_skills_extractor = MagicMock()
        d._augment_skills_extractor.extract_all_skills.return_value = {
            "user_skills": [], "project_skills": []}
        d._augment_settings_extractor = MagicMock()
        d._augment_settings_extractor.extract_settings.return_value = []
        return d

    def test_each_user_gets_own_canonical_surface(self):
        d = self._multi_user_detector()
        a_cli = {"name": "Auggie CLI", "version": "0.30.0",
                 "install_path": "/Users/alice/.local/bin/auggie",
                 "_config_path": "/Users/alice/.augment"}
        a_vsc = {"name": "Augment (VS Code)", "version": "1.0",
                 "install_path": "/Users/alice/.vscode/extensions",
                 "_config_path": "/Users/alice/.augment"}
        b_vsc = {"name": "Augment (VS Code)", "version": "1.0",
                 "install_path": "/Users/bob/.vscode/extensions",
                 "_config_path": "/Users/bob/.augment"}
        tools = [a_cli, a_vsc, b_vsc]
        d._set_canonical_augment_surface(tools)

        # Per-config canonical: A -> CLI, B -> VS Code (independent winners).
        self.assertEqual(
            d._canonical_augment_surface_by_config["/Users/alice/.augment"], "auggie cli")
        self.assertEqual(
            d._canonical_augment_surface_by_config["/Users/bob/.augment"], "augment (vs code)")

        results = [d.process_single_tool(t) for t in tools]
        by = {(r["name"], r["_config_path"]): r for r in results}

        # A's CLI carries A's config; A's VS Code is bare.
        self.assertTrue(by[("Auggie CLI", "/Users/alice/.augment")]["projects"])
        self.assertEqual(by[("Augment (VS Code)", "/Users/alice/.augment")]["projects"], [])

        # B's VS Code is CANONICAL and carries B's config (NOT dropped).
        b_row = by[("Augment (VS Code)", "/Users/bob/.augment")]
        self.assertTrue(b_row["projects"])
        b_roots = {p["path"] for p in b_row["projects"]}
        self.assertIn("/Users/bob/.augment", b_roots)

    def test_cross_user_isolation_holds(self):
        d = self._multi_user_detector()
        a_cli = {"name": "Auggie CLI", "version": "0.30.0",
                 "install_path": "/Users/alice/.local/bin/auggie",
                 "_config_path": "/Users/alice/.augment"}
        b_vsc = {"name": "Augment (VS Code)", "version": "1.0",
                 "install_path": "/Users/bob/.vscode/extensions",
                 "_config_path": "/Users/bob/.augment"}
        tools = [a_cli, b_vsc]
        d._set_canonical_augment_surface(tools)

        a_processed = d.process_single_tool(a_cli)
        b_processed = d.process_single_tool(b_vsc)

        # A's config never appears on B's row and vice-versa, even after the
        # per-user project filter.
        a_view = d.filter_tool_projects_by_user(a_processed, Path("/Users/alice"))
        b_view = d.filter_tool_projects_by_user(b_processed, Path("/Users/bob"))
        a_roots = {p["path"] for p in a_view["projects"]}
        b_roots = {p["path"] for p in b_view["projects"]}
        self.assertNotIn("/Users/bob/.augment", a_roots)
        self.assertNotIn("/Users/alice/.augment", b_roots)


class TestAugmentMcpCacheSentinel(unittest.TestCase):
    """FIX B: ``extract_mcp_config`` returning None must still memoize. A distinct
    UNSET sentinel (not None) drives the cache, so a cached None short-circuits and
    the expensive MCP walk runs only ONCE across many ``_get_augment_mcp`` calls."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_none_result_memoized_extract_called_once(self):
        d = AIToolsDetector(os_name="Darwin")
        d._augment_mcp_extractor = MagicMock()
        d._augment_mcp_extractor.extract_mcp_config.return_value = None

        for _ in range(5):
            self.assertIsNone(d._get_augment_mcp())

        self.assertEqual(d._augment_mcp_extractor.extract_mcp_config.call_count, 1)


class TestAugmentManagedOnlyOwnership(unittest.TestCase):
    """FIX F: managed-scope-only permissions must NOT manufacture an Augment row
    for a non-owner. Managed (org-wide /etc/augment) policy survives filtering for
    EVERY user, so its mere presence cannot count as user-owned data."""

    def test_managed_only_perms_do_not_make_non_owner_owned(self):
        # alice owns ~/.augment; the surviving permissions block is MANAGED only.
        # bob, a non-owner with no per-user data, must NOT pass the gate.
        tool_filtered = {
            "name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
            "install_path": "/Users/alice/.local/bin/auggie", "projects": [],
            "permissions": {"settings_source": "managed", "scope": "managed",
                            "settings_path": "/etc/augment/settings.json"},
        }
        self.assertFalse(_augment_owned_by_user(tool_filtered, "/Users/bob"))

    def test_managed_perms_still_owned_by_config_owner(self):
        # The config owner (alice) is still owned — via owns_install — even when the
        # only attached permissions are managed (effective org policy).
        tool_filtered = {
            "name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
            "install_path": "/Users/alice/.local/bin/auggie", "projects": [],
            "permissions": {"settings_source": "managed", "scope": "managed",
                            "settings_path": "/etc/augment/settings.json"},
        }
        self.assertTrue(_augment_owned_by_user(tool_filtered, "/Users/alice"))

    def test_user_scope_perms_count_as_owned(self):
        # A surviving NON-managed (user-scope) permissions block means this user
        # owns user-scope policy here -> owned even without owns_install.
        tool_filtered = {
            "name": "Auggie CLI", "_config_path": "/Users/alice/.augment",
            "install_path": "/Users/alice/.local/bin/auggie", "projects": [],
            "permissions": {"settings_source": "user", "scope": "user",
                            "settings_path": "/Users/bob/.augment/settings.json"},
        }
        self.assertTrue(_augment_owned_by_user(tool_filtered, "/Users/bob"))

    def test_managed_only_full_flow_b_dropped_a_keeps_managed(self):
        """FIX F end-to-end: managed /etc/augment is attached to alice's canonical
        row. Under a root scan, B (no install) iterates the row -> the gate drops B
        (managed alone doesn't manufacture a row); A keeps the managed (effective)
        permissions."""
        utils_mod._SENTRY_DSN = ""
        d = AIToolsDetector(os_name="Darwin")
        d._augment_mcp_extractor = MagicMock()
        d._augment_mcp_extractor.extract_mcp_config.return_value = None
        d._augment_rules_extractor = MagicMock()
        d._augment_rules_extractor.extract_all_augment_rules.return_value = []
        d._augment_skills_extractor = MagicMock()
        d._augment_skills_extractor.extract_all_skills.return_value = {
            "user_skills": [], "project_skills": []}
        # Only a MANAGED (org-wide) settings record exists — no user-scope record.
        d._augment_settings_extractor = MagicMock()
        d._augment_settings_extractor.extract_settings.return_value = [
            {"tool_name": "Augment Code", "scope": "managed",
             "settings_path": "/etc/augment/settings.json",
             "raw_settings": {"toolPermissions": []},
             "permissions": {"defaultMode": None, "allow": ["read"], "deny": [],
                             "ask": [], "additionalDirectories": []}},
        ]

        cli = {"name": "Auggie CLI", "version": "0.30.0",
               "install_path": "/Users/alice/.local/bin/auggie",
               "_config_path": "/Users/alice/.augment"}
        d._set_canonical_augment_surface([cli])
        processed = d.process_single_tool(cli)

        # Managed (effective) perms ARE attached to the canonical (alice) row.
        self.assertIn("permissions", processed)
        self.assertEqual(processed["permissions"]["settings_source"], "managed")

        # Alice (owner) keeps the row: managed perms survive filtering.
        alice_view = d.filter_tool_projects_by_user(processed, Path("/Users/alice"))
        self.assertTrue(_augment_owned_by_user(alice_view, "/Users/alice"))
        self.assertIn("permissions", alice_view)
        self.assertEqual(alice_view["permissions"]["settings_source"], "managed")

        # Bob (no install, no per-user data) is DROPPED: managed alone does not
        # manufacture a row for a non-owner.
        bob_view = d.filter_tool_projects_by_user(processed, Path("/Users/bob"))
        self.assertFalse(_augment_owned_by_user(bob_view, "/Users/bob"))


class TestAugmentUserSkillsNoCrossUserLeak(unittest.TestCase):
    """FIX 1: under a root all-users scan ``_get_augment_skills`` returns ALL
    users' user-scope skills in one flat list. Each skill must be keyed under ITS
    OWN config dir (``<owner-home>/.augment`` etc., derived from its ``file_path``)
    so the per-user project filter scopes A's skill to A and B's skill to B — no
    cross-user content leak — and ~/.augment skills coalesce with that row's
    ~/.augment rules/MCP rather than a bare-home project.
    """

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def _detector_with_two_user_skills(self):
        d = AIToolsDetector(os_name="Darwin")
        d._augment_mcp_extractor = MagicMock()
        d._augment_mcp_extractor.extract_mcp_config.return_value = None
        d._augment_rules_extractor = MagicMock()
        d._augment_rules_extractor.extract_all_augment_rules.return_value = []
        d._augment_settings_extractor = MagicMock()
        d._augment_settings_extractor.extract_settings.return_value = []
        # A root all-users scan: BOTH users' user-scope skills in one flat list,
        # each carrying its own home in file_path.
        d._augment_skills_extractor = MagicMock()
        d._augment_skills_extractor.extract_all_skills.return_value = {
            "user_skills": [
                {"skill_name": "a", "type": "skill", "scope": "user",
                 "file_path": "/Users/alice/.augment/skills/a/SKILL.md"},
                {"skill_name": "b", "type": "skill", "scope": "user",
                 "file_path": "/Users/bob/.augment/skills/b/SKILL.md"},
            ],
            "project_skills": [],
        }
        return d

    def test_each_users_skills_scoped_to_their_own_home(self):
        d = self._detector_with_two_user_skills()
        # Canonical row is alice's Auggie CLI (the surface that carries the
        # shared config). Its skills are keyed by each skill's OWNER config dir.
        cli = {"name": "Auggie CLI", "version": "0.30.0",
               "install_path": "/Users/alice/.local/bin/auggie",
               "_config_path": "/Users/alice/.augment"}
        d._set_canonical_augment_surface([cli])
        processed = d.process_single_tool(cli)

        # Skills live under each owner's ~/.augment (config dir), not all under
        # alice's install_key and not under a bare home.
        by_path = {p["path"]: p for p in processed["projects"]}
        self.assertIn("/Users/alice/.augment", by_path)
        self.assertIn("/Users/bob/.augment", by_path)

        # Per-user filtering scopes each user's skills to their own home.
        alice_view = d.filter_tool_projects_by_user(processed, Path("/Users/alice"))
        bob_view = d.filter_tool_projects_by_user(processed, Path("/Users/bob"))

        alice_names = {s["skill_name"] for p in alice_view["projects"] for s in p["skills"]}
        bob_names = {s["skill_name"] for p in bob_view["projects"] for s in p["skills"]}

        # Alice's row carries ONLY alice's skill; bob's ONLY bob's. No leak.
        self.assertEqual(alice_names, {"a"})
        self.assertEqual(bob_names, {"b"})


class TestAugmentSkillProjectRoot(unittest.TestCase):
    """``_augment_skill_project_root`` returns the CONFIG DIR a user skill lives in
    (so ~/.augment skills coalesce with ~/.augment rules/MCP), across .augment /
    .claude / .agents, and stays owner-scoped. None when unparseable."""

    def _root(self, file_path):
        return AIToolsDetector._augment_skill_project_root({"file_path": file_path})

    def test_augment_marker(self):
        self.assertEqual(self._root("/Users/x/.augment/skills/a/SKILL.md"), "/Users/x/.augment")

    def test_claude_marker(self):
        self.assertEqual(self._root("/Users/x/.claude/skills/a/SKILL.md"), "/Users/x/.claude")

    def test_agents_marker(self):
        self.assertEqual(self._root("/Users/x/.agents/skills/a/SKILL.md"), "/Users/x/.agents")

    def test_windows_separator(self):
        self.assertEqual(self._root(r"C:\Users\x\.augment\skills\a\SKILL.md"), r"C:\Users\x\.augment")

    def test_missing_or_unparseable_returns_none(self):
        self.assertIsNone(self._root(""))
        self.assertIsNone(self._root("/Users/x/random/file.md"))


if __name__ == "__main__":
    unittest.main()
