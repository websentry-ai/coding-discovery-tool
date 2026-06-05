"""
Integration tests for SKILLS-ONLY enrichment of VS Code GitHub Copilot rows.

When a VS Code GitHub Copilot surface is ALREADY detected, the IDE branch of
``AIToolsDetector.process_single_tool`` also reads the SHARED Copilot skills
(``~/.copilot/skills`` + project ``.github``/``.claude``/``.agents`` skills —
the same set the CLI reports) and attaches them to exactly ONE VS Code row
(preferring the Chat surface). This is extraction-only: detection is untouched,
so it cannot re-introduce the #164 CLI false positive.

These tests exercise the outermost testable boundary — ``process_single_tool``
plus ``filter_tool_projects_by_user`` — and mock the skills extractor so no real
filesystem walk runs. The orchestration-level choice of which VS Code row is
canonical (normally computed in ``main()`` from the full detected list) is
represented here by setting ``detector._canonical_vscode_copilot`` directly,
which is the documented contract the IDE branch reads.

Conventions mirror the existing suite: ``AIToolsDetector(os_name=...)``, mocked
extractors, and ``_SENTRY_DSN`` forced empty to prevent real Sentry calls.
"""

import unittest
from pathlib import Path
from unittest.mock import MagicMock

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


def _user_skill(file_path: str) -> dict:
    """Build a minimal user-scope skill dict (only file_path is load-bearing)."""
    return {
        "file_path": file_path,
        "file_name": Path(file_path).name,
        "scope": "user",
        "content": "x",
    }


def _project_skill(file_path: str) -> dict:
    """Build a minimal project-scope skill dict."""
    return {
        "file_path": file_path,
        "file_name": Path(file_path).name,
        "scope": "project",
        "content": "x",
    }


def _make_detector(skills_result: dict, os_name: str = "Darwin") -> AIToolsDetector:
    """Detector with IDE rules/MCP stubbed empty and the skills extractor mocked."""
    detector = AIToolsDetector(os_name=os_name)

    detector._github_copilot_rules_extractor = MagicMock()
    detector._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = []
    detector._github_copilot_mcp_extractor = MagicMock()
    detector._github_copilot_mcp_extractor.extract_mcp_config.return_value = None

    detector._copilot_cli_skills_extractor = MagicMock()
    detector._copilot_cli_skills_extractor.extract_all_skills.return_value = skills_result
    return detector


def _skills_paths(projects: list) -> dict:
    """Map project path -> set of skill file_paths, for compact assertions."""
    return {
        p["path"]: {s["file_path"] for s in p.get("skills", [])}
        for p in projects
    }


class TestVscodeCopilotSkillsAttach(unittest.TestCase):
    """Skills attach to the canonical VS Code Copilot row only."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    # 1. IDE-only user (no CLI strong marker) — chat row carries user + project skills.
    def test_chat_row_carries_user_and_project_skills(self):
        alice_user_skill = "/Users/alice/.copilot/skills/foo/SKILL.md"
        repo_root = "/Users/alice/proj"
        detector = _make_detector({
            "user_skills": [_user_skill(alice_user_skill)],
            "project_skills": [
                {"project_root": repo_root,
                 "skills": [_project_skill(f"{repo_root}/.github/skills/bar/SKILL.md")]},
            ],
        })
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        tool = {
            "name": "GitHub Copilot Chat (VS Code)",
            "version": "1.0",
            "install_path": "/Users/alice/.vscode",
        }
        result = detector.process_single_tool(tool)

        by_path = _skills_paths(result["projects"])
        # User skill keyed under the OWNER's home (derived from file_path), not install_path.
        self.assertIn("/Users/alice", by_path)
        self.assertEqual(by_path["/Users/alice"], {alice_user_skill})
        # Project skill keyed under the absolute repo root.
        self.assertIn(repo_root, by_path)
        self.assertEqual(
            by_path[repo_root], {f"{repo_root}/.github/skills/bar/SKILL.md"}
        )

    # 2. Both VS Code extensions present — only the chat row carries skills; walk runs once.
    def test_only_chat_row_carries_skills_and_walk_runs_once(self):
        user_skill = "/Users/alice/.copilot/skills/foo/SKILL.md"
        detector = _make_detector({
            "user_skills": [_user_skill(user_skill)],
            "project_skills": [],
        })
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        chat = {"name": "GitHub Copilot Chat (VS Code)", "version": "1.0",
                "install_path": "/Users/alice/.vscode"}
        plain = {"name": "GitHub Copilot (VS Code)", "version": "1.0",
                 "install_path": "/Users/alice/.vscode"}

        chat_result = detector.process_single_tool(chat)
        plain_result = detector.process_single_tool(plain)

        # Chat row carries the skill.
        chat_skill_paths = {
            s["file_path"] for p in chat_result["projects"] for s in p.get("skills", [])
        }
        self.assertEqual(chat_skill_paths, {user_skill})

        # Plain row has no skills (every project's skills list is empty).
        plain_skill_count = sum(
            len(p.get("skills", [])) for p in plain_result["projects"]
        )
        self.assertEqual(plain_skill_count, 0)
        # Every emitted project still serializes a "skills" key (shape stability).
        for p in plain_result["projects"]:
            self.assertIn("skills", p)

        # S6: the underlying walk ran at most once across BOTH rows (memoized).
        self.assertEqual(
            detector._copilot_cli_skills_extractor.extract_all_skills.call_count, 1
        )

    # 3. JetBrains Copilot row (has "ide" key) — no skills attached.
    def test_jetbrains_row_gets_no_skills(self):
        detector = _make_detector({
            "user_skills": [_user_skill("/Users/alice/.copilot/skills/foo/SKILL.md")],
            "project_skills": [],
        })
        # Even if canonical pointed at a VS Code name, JetBrains is excluded by "ide".
        detector._canonical_vscode_copilot = "github copilot (vs code)"

        tool = {
            "name": "GitHub Copilot (IntelliJ IDEA)",
            "version": "1.0",
            "install_path": "/Users/alice/Library/.../IntelliJ",
            "ide": "IntelliJ IDEA",
        }
        result = detector.process_single_tool(tool)

        skill_count = sum(len(p.get("skills", [])) for p in result["projects"])
        self.assertEqual(skill_count, 0)
        # The skills extractor must never be consulted for a JetBrains row.
        self.assertEqual(
            detector._copilot_cli_skills_extractor.extract_all_skills.call_count, 0
        )

    # 3b. Non-canonical VS Code row (plain, when chat is canonical) — no skills.
    def test_non_canonical_vscode_row_gets_no_skills(self):
        detector = _make_detector({
            "user_skills": [_user_skill("/Users/alice/.copilot/skills/foo/SKILL.md")],
            "project_skills": [],
        })
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        plain = {"name": "GitHub Copilot (VS Code)", "version": "1.0",
                 "install_path": "/Users/alice/.vscode"}
        result = detector.process_single_tool(plain)

        skill_count = sum(len(p.get("skills", [])) for p in result["projects"])
        self.assertEqual(skill_count, 0)
        # The non-canonical row never even consults the extractor.
        self.assertEqual(
            detector._copilot_cli_skills_extractor.extract_all_skills.call_count, 0
        )

    # 4. Multi-user user_skills — per-user filter scopes correctly, no leak/drop.
    def test_multi_user_skills_scoped_per_user_home(self):
        alice = "/Users/alice/.copilot/skills/foo/SKILL.md"
        bob = "/Users/bob/.copilot/skills/baz/SKILL.md"
        detector = _make_detector({
            "user_skills": [_user_skill(alice), _user_skill(bob)],
            "project_skills": [],
        })
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        tool = {"name": "GitHub Copilot Chat (VS Code)", "version": "1.0",
                "install_path": "/Users/alice/.vscode"}
        full = detector.process_single_tool(tool)

        # Both user homes appear in the unfiltered tool dict.
        by_path = _skills_paths(full["projects"])
        self.assertEqual(by_path.get("/Users/alice"), {alice})
        self.assertEqual(by_path.get("/Users/bob"), {bob})

        # Filtering for alice keeps only alice's skill (no bob leak).
        alice_only = detector.filter_tool_projects_by_user(full, Path("/Users/alice"))
        alice_paths = _skills_paths(alice_only["projects"])
        self.assertEqual(alice_paths, {"/Users/alice": {alice}})

        # Filtering for bob keeps only bob's skill (not dropped).
        bob_only = detector.filter_tool_projects_by_user(full, Path("/Users/bob"))
        bob_paths = _skills_paths(bob_only["projects"])
        self.assertEqual(bob_paths, {"/Users/bob": {bob}})

    # 4b. .agents user skills also resolve to the owner's home.
    def test_agents_user_skill_keyed_under_owner_home(self):
        agents_skill = "/Users/alice/.agents/skills/foo/SKILL.md"
        detector = _make_detector({
            "user_skills": [_user_skill(agents_skill)],
            "project_skills": [],
        })
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        tool = {"name": "GitHub Copilot Chat (VS Code)", "version": "1.0",
                "install_path": "/Users/alice/.vscode"}
        result = detector.process_single_tool(tool)

        by_path = _skills_paths(result["projects"])
        self.assertEqual(by_path.get("/Users/alice"), {agents_skill})


class TestVscodeCopilotSkillsMemoization(unittest.TestCase):
    """The shared walk runs once even when a CLI row and a VS Code row both process."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_cli_skills_byte_identical_via_accessor(self):
        """CLI projects[].skills must be identical whether read directly or via accessor."""
        user_skill = "/Users/x/.copilot/skills/foo/SKILL.md"
        repo = "/Users/x/proj"
        skills_result = {
            "user_skills": [_user_skill(user_skill)],
            "project_skills": [
                {"project_root": repo,
                 "skills": [_project_skill(f"{repo}/.github/skills/bar/SKILL.md")]},
            ],
        }
        cli_tool = {
            "name": "GitHub Copilot CLI", "version": "0.0.1",
            "install_path": "/Users/x/.copilot",
        }

        # OLD path: extractor called directly (no accessor memoization).
        old_det = AIToolsDetector(os_name="Darwin")
        old_det._copilot_cli_mcp_extractor = MagicMock()
        old_det._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        old_det._copilot_cli_rules_extractor = MagicMock()
        old_det._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        old_det._copilot_cli_settings_extractor = MagicMock()
        old_det._copilot_cli_settings_extractor.extract_settings.return_value = []
        old_det._copilot_cli_skills_extractor = MagicMock()
        old_det._copilot_cli_skills_extractor.extract_all_skills.return_value = skills_result
        old_projects = old_det.process_single_tool(dict(cli_tool))

        # NEW path: same mocked extractor output, accessor in play.
        new_det = AIToolsDetector(os_name="Darwin")
        new_det._copilot_cli_mcp_extractor = MagicMock()
        new_det._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        new_det._copilot_cli_rules_extractor = MagicMock()
        new_det._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        new_det._copilot_cli_settings_extractor = MagicMock()
        new_det._copilot_cli_settings_extractor.extract_settings.return_value = []
        new_det._copilot_cli_skills_extractor = MagicMock()
        new_det._copilot_cli_skills_extractor.extract_all_skills.return_value = skills_result
        new_projects = new_det.process_single_tool(dict(cli_tool))

        self.assertEqual(_skills_paths(old_projects["projects"]),
                         _skills_paths(new_projects["projects"]))

    def test_walk_runs_once_across_cli_and_vscode_rows(self):
        """One extractor instance shared by a CLI row + a VS Code row -> one walk."""
        user_skill = "/Users/x/.copilot/skills/foo/SKILL.md"
        skills_result = {"user_skills": [_user_skill(user_skill)], "project_skills": []}

        detector = AIToolsDetector(os_name="Darwin")
        # CLI extractors.
        detector._copilot_cli_mcp_extractor = MagicMock()
        detector._copilot_cli_mcp_extractor.extract_mcp_config.return_value = None
        detector._copilot_cli_rules_extractor = MagicMock()
        detector._copilot_cli_rules_extractor.extract_all_copilot_cli_rules.return_value = []
        detector._copilot_cli_settings_extractor = MagicMock()
        detector._copilot_cli_settings_extractor.extract_settings.return_value = []
        # IDE extractors.
        detector._github_copilot_rules_extractor = MagicMock()
        detector._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = []
        detector._github_copilot_mcp_extractor = MagicMock()
        detector._github_copilot_mcp_extractor.extract_mcp_config.return_value = None
        # Shared skills extractor.
        detector._copilot_cli_skills_extractor = MagicMock()
        detector._copilot_cli_skills_extractor.extract_all_skills.return_value = skills_result
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        detector.process_single_tool({
            "name": "GitHub Copilot CLI", "version": "0.0.1",
            "install_path": "/Users/x/.copilot"})
        detector.process_single_tool({
            "name": "GitHub Copilot Chat (VS Code)", "version": "1.0",
            "install_path": "/Users/x/.vscode"})

        self.assertEqual(
            detector._copilot_cli_skills_extractor.extract_all_skills.call_count, 1
        )


class TestVscodeCopilotSkillsLinux(unittest.TestCase):
    """Linux / no-extractor: rows serialize empty skills and nothing crashes."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_no_extractor_yields_empty_skills_no_crash(self):
        detector = AIToolsDetector(os_name="Linux")
        detector._github_copilot_rules_extractor = MagicMock()
        detector._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = [
            {"project_root": "/home/alice/proj",
             "rules": [{"file_path": "/home/alice/proj/.github/copilot-instructions.md"}]},
        ]
        detector._github_copilot_mcp_extractor = MagicMock()
        detector._github_copilot_mcp_extractor.extract_mcp_config.return_value = None
        # Linux factory yields None for the skills extractor; force that here.
        detector._copilot_cli_skills_extractor = None
        detector._canonical_vscode_copilot = "github copilot chat (vs code)"

        tool = {"name": "GitHub Copilot Chat (VS Code)", "version": "1.0",
                "install_path": "/home/alice/.vscode"}
        result = detector.process_single_tool(tool)

        # The rules project still emits, with an explicit empty skills list.
        self.assertEqual(len(result["projects"]), 1)
        self.assertEqual(result["projects"][0]["skills"], [])
        self.assertIn("skills", result["projects"][0])


if __name__ == "__main__":
    unittest.main()
