"""
Integration tests for GitHub Copilot VS Code discovery: custom-instruction
locations, prompt files, and MCP identity scoping.

  - ``.github/instructions/**/*.instructions.md`` and ``.github/prompts/*.prompt.md``
    (project) + the VS Code User prompts dir (user).
  - The documented "Default file location" custom-instruction sources:
    workspace ``.claude/rules`` and user ``~/.copilot/instructions`` / ``~/.claude/rules``.
  - ``extract_mcp_config(tool_name=...)`` returns only the MCP sources owned by
    the surface being processed (VS Code vs JetBrains), not the union.

These exercise the outermost extractor surfaces (``_walk_for_github_directories``
for project rules, ``_extract_global_vscode_rules`` for user rules). Project
fixtures are rooted under the real home so the production system-path skip
predicate (which rejects ``/tmp``) does not drop them.

The CRITICAL guard is ``test_prompt_rule_has_only_allowed_fields``: a rule
carrying any non-allowlisted key is silently DISCARDED whole by backend
ingestion, so every emitted rule's keys must be a subset of the allowlist.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor import (
    MacOSGitHubCopilotRulesExtractor,
)
from scripts.coding_discovery_tools.macos.github_copilot.mcp_config_extractor import (
    MacOSGitHubCopilotMCPConfigExtractor,
)

_MACOS_RULES_MOD = (
    "scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor"
)

# Backend ALLOWED_RULE_FIELDS — any rule dict with a key outside this set is
# silently dropped whole by ingestion, so every emitted rule's keys must be a
# subset of these.
_ALLOWED_RULE_FIELDS = {
    "file_path", "file_name", "content", "size",
    "last_modified", "truncated", "scope", "project_path",
}


def _flatten_rules(project_list):
    """Flatten the [{project_root, rules:[...]}] output into a single list."""
    rules = []
    for project in project_list:
        for rule in project["rules"]:
            rules.append((project["project_root"], rule))
    return rules


class TestProjectInstructionsAndPrompts(unittest.TestCase):
    """Project-scoped .github/instructions/** and .github/prompts/ extraction."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSGitHubCopilotRulesExtractor()
        # Root under the real home so the system-path skip predicate (which
        # rejects /tmp) does not drop the walk.
        self.tmp_root = Path(tempfile.mkdtemp(dir=str(Path.home())))
        self.repo = self.tmp_root / "myrepo"
        self.github_dir = self.repo / ".github"
        self.github_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _walk(self):
        """Run the real workspace walk rooted at the tmp tree."""
        projects_by_root = {}
        self.extractor._walk_for_github_directories(
            self.tmp_root, self.tmp_root, projects_by_root, current_depth=0
        )
        from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list
        return build_project_list(projects_by_root)

    def _write(self, path: Path, content: str = "# rule") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    # --- H4 ---------------------------------------------------------------

    def test_top_level_instruction_collected(self):
        self._write(self.github_dir / "instructions" / "style.instructions.md")
        rules = _flatten_rules(self._walk())
        match = [(root, r) for root, r in rules if r["file_name"] == "style.instructions.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.repo))
        self.assertEqual(rule["scope"], "project")

    def test_nested_instruction_collected_with_repo_root(self):
        self._write(
            self.github_dir / "instructions" / "frontend" / "react.instructions.md"
        )
        rules = _flatten_rules(self._walk())
        match = [(root, r) for root, r in rules if r["file_name"] == "react.instructions.md"]
        self.assertEqual(len(match), 1)
        root, _rule = match[0]
        # Nested file still resolves to the project root (parent of .github).
        self.assertEqual(root, str(self.repo))

    def test_plain_md_without_infix_not_collected(self):
        self._write(self.github_dir / "instructions" / "notes.md")
        rules = _flatten_rules(self._walk())
        names = {r["file_name"] for _root, r in rules}
        self.assertNotIn("notes.md", names)

    def test_legacy_copilot_dir_not_collected(self):
        """The intentional removal: .github/copilot/*.md is no longer read."""
        self._write(self.github_dir / "copilot" / "old.md")
        rules = _flatten_rules(self._walk())
        names = {r["file_name"] for _root, r in rules}
        self.assertNotIn("old.md", names)

    # --- H5 ---------------------------------------------------------------

    def test_project_prompt_file_collected(self):
        self._write(self.github_dir / "prompts" / "refactor.prompt.md")
        rules = _flatten_rules(self._walk())
        match = [(root, r) for root, r in rules if r["file_name"] == "refactor.prompt.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.repo))
        self.assertEqual(rule["scope"], "project")

    def test_prompt_rule_has_only_allowed_fields(self):
        """CRITICAL: a prompt rule with any non-allowlisted key is dropped whole
        by the backend, so the emitted keys must be a subset of the allowlist."""
        self._write(self.github_dir / "prompts" / "refactor.prompt.md")
        rules = _flatten_rules(self._walk())
        prompt_rules = [r for _root, r in rules if r["file_name"] == "refactor.prompt.md"]
        self.assertEqual(len(prompt_rules), 1)
        self.assertTrue(
            set(prompt_rules[0].keys()) <= _ALLOWED_RULE_FIELDS,
            f"prompt rule carries non-allowlisted keys: "
            f"{set(prompt_rules[0].keys()) - _ALLOWED_RULE_FIELDS}",
        )

    # --- Workspace .claude/rules (Claude-format instructions) -------------

    def test_claude_rules_md_collected_at_repo_root(self):
        self._write(self.repo / ".claude" / "rules" / "style.md")
        rules = _flatten_rules(self._walk())
        match = [(root, r) for root, r in rules if r["file_name"] == "style.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.repo))
        self.assertEqual(rule["scope"], "project")

    def test_claude_rule_has_only_allowed_fields(self):
        self._write(self.repo / ".claude" / "rules" / "style.md")
        rules = _flatten_rules(self._walk())
        rule = next(r for _root, r in rules if r["file_name"] == "style.md")
        self.assertTrue(
            set(rule.keys()) <= _ALLOWED_RULE_FIELDS,
            f"non-allowlisted keys: {set(rule.keys()) - _ALLOWED_RULE_FIELDS}",
        )

    def test_extension_package_claude_rules_not_collected(self):
        """A .claude/rules inside another tool's installed package is skipped."""
        self._write(
            self.tmp_root / ".antigravity" / "extensions" / "pkg" / ".claude" / "rules" / "ext.md"
        )
        rules = _flatten_rules(self._walk())
        names = {r["file_name"] for _root, r in rules}
        self.assertNotIn("ext.md", names)

    def test_user_home_claude_rules_not_collected_by_walk(self):
        """~/.claude/rules is user scope (handled elsewhere); the project walk
        must not also collect it as project scope (no double-count)."""
        fake_home = self.tmp_root / "home"
        (fake_home / ".claude" / "rules").mkdir(parents=True)
        (fake_home / ".claude" / "rules" / "home.md").write_text("# h", encoding="utf-8")
        projects_by_root = {}
        with patch.object(Path, "home", return_value=fake_home):
            self.extractor._extract_claude_rules(fake_home / ".claude", projects_by_root)
        self.assertEqual(projects_by_root, {})


class TestUserPromptsAndInstructions(unittest.TestCase):
    """User-scoped VS Code prompts dir: *.instructions.md AND *.prompt.md."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.extractor = MacOSGitHubCopilotRulesExtractor()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.prompts_dir = (
            self.user_home / "Library" / "Application Support" / "Code" / "User" / "prompts"
        )
        self.prompts_dir.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _extract_user(self):
        """Run _extract_global_vscode_rules for our stubbed user home."""
        projects_by_root = {}
        with patch(f"{_MACOS_RULES_MOD}.is_running_as_root", return_value=False), \
                patch(f"{_MACOS_RULES_MOD}.Path") as mock_path:
            # Path.home() -> our temp user home; Path(x) -> real Path(x).
            real_path = Path
            mock_path.home.return_value = self.user_home
            mock_path.side_effect = lambda *a, **k: real_path(*a, **k)
            self.extractor._extract_global_vscode_rules(projects_by_root)
        from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list
        return build_project_list(projects_by_root)

    def _write(self, name: str, content: str = "# rule") -> None:
        (self.prompts_dir / name).write_text(content, encoding="utf-8")

    def test_user_prompt_file_collected(self):
        self._write("x.prompt.md")
        rules = _flatten_rules(self._extract_user())
        match = [(root, r) for root, r in rules if r["file_name"] == "x.prompt.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.user_home / "Library" / "Application Support" / "Code" / "User"))
        self.assertEqual(rule["scope"], "user")

    def test_user_instructions_and_prompt_collected_together(self):
        """Regression guard: both file kinds in the user prompts dir surface."""
        self._write("x.instructions.md")
        self._write("y.prompt.md")
        rules = _flatten_rules(self._extract_user())
        names = {r["file_name"] for _root, r in rules}
        self.assertIn("x.instructions.md", names)
        self.assertIn("y.prompt.md", names)

    def test_user_copilot_instructions_dir_collected(self):
        """~/.copilot/instructions/**/*.instructions.md -> user scope."""
        d = self.user_home / ".copilot" / "instructions"
        d.mkdir(parents=True)
        (d / "global.instructions.md").write_text("# g", encoding="utf-8")
        rules = _flatten_rules(self._extract_user())
        match = [(root, r) for root, r in rules if r["file_name"] == "global.instructions.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.user_home))
        self.assertEqual(rule["scope"], "user")

    def test_user_claude_rules_dir_collected(self):
        """~/.claude/rules/**/*.md -> user scope."""
        d = self.user_home / ".claude" / "rules"
        d.mkdir(parents=True)
        (d / "global.md").write_text("# g", encoding="utf-8")
        rules = _flatten_rules(self._extract_user())
        match = [(root, r) for root, r in rules if r["file_name"] == "global.md"]
        self.assertEqual(len(match), 1)
        root, rule = match[0]
        self.assertEqual(root, str(self.user_home))
        self.assertEqual(rule["scope"], "user")


class TestVscodeCopilotMcpIdentityScoping(unittest.TestCase):
    """extract_mcp_config(tool_name=...) returns only the MCP sources owned by
    the surface, so a JetBrains-only server never appears under VS Code Copilot
    (and vice versa) when both IDE Copilots are installed."""

    def setUp(self):
        self.extractor = MacOSGitHubCopilotMCPConfigExtractor()
        self.extractor._extract_vscode_configs = lambda: [
            {"path": "/vscode", "mcpServers": [{"name": "vs"}]}
        ]
        self.extractor._extract_jetbrains_configs = lambda: [
            {"path": "/jb", "mcpServers": [{"name": "jb"}]}
        ]
        self.extractor._extract_workspace_configs = lambda: [
            {"path": "/repo", "mcpServers": [{"name": "ws"}]}
        ]

    def _paths(self, tool_name):
        result = self.extractor.extract_mcp_config(tool_name=tool_name)
        return {p["path"] for p in (result or {}).get("projects", [])}

    def test_vscode_row_gets_vscode_and_workspace_not_jetbrains(self):
        for name in ("GitHub Copilot (VS Code)", "GitHub Copilot Chat (VS Code)"):
            self.assertEqual(self._paths(name), {"/vscode", "/repo"}, name)

    def test_jetbrains_row_gets_jetbrains_only(self):
        self.assertEqual(self._paths("GitHub Copilot (PyCharm Community)"), {"/jb"})

    def test_none_keeps_legacy_union(self):
        self.assertEqual(self._paths(None), {"/vscode", "/jb", "/repo"})


if __name__ == "__main__":
    unittest.main()
