"""
Integration tests for Augment Code rules/guidelines extraction (macOS).

Exercises the outermost surface (``extract_all_augment_rules()``):

  - User: ``~/.augment/user-guidelines.md`` + ``~/.augment/rules/*.{md,mdx}``
    grouped under ``~/.augment`` (scope "user").
  - Project: ``.augment-guidelines`` + ``.augment/rules/*.{md,mdx}``.
  - Revised D3: ``AGENTS.md`` AND ``CLAUDE.md`` ARE collected (hierarchically).
  - ``.mdx`` regression + the no-frontmatter contract (rule dicts carry only the
    backend's allowlisted fields).
  - Sad-paths: symlink loop, depth bound, permission error.

The temp dir lives under /var, which the real ``should_skip_system_path`` skips,
so the project walk neutralises it (mirrors the rules suite convention).
"""

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.augment.augment_rules_extractor import (
    MacOSAugmentRulesExtractor,
)

_RULES_MOD = "scripts.coding_discovery_tools.macos.augment.augment_rules_extractor"

# The backend's allowed rule-dict fields — every emitted rule must be a subset.
_ALLOWED_RULE_FIELDS = {
    "file_path", "file_name", "content", "size",
    "last_modified", "truncated", "scope", "project_path",
}


def _all_rules(projects):
    rules = []
    for p in projects:
        rules.extend(p.get("rules", []))
    return rules


def _names(projects):
    return {r["file_name"] for r in _all_rules(projects)}


class _AugmentRulesHarness(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.augment_dir = self.user_home / ".augment"
        self.augment_dir.mkdir(parents=True)
        self.extractor = MacOSAugmentRulesExtractor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _extract_user_only(self):
        """Run only the user-scope extraction (no project walk)."""
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: fn(self.user_home)), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[]):
            return self.extractor.extract_all_augment_rules()

    def _extract_with_project_root(self, repo: Path):
        """Run extraction with the project walk pinned to ``repo`` (no user scan).

        ``_filesystem_root`` is pinned to the temp ancestor so the walk's
        ``relative_to(root)`` depth check works on Windows too (a real path like
        ``C:\\...\\repo`` is not relative to the macOS class's default ``/``).
        """
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: None), \
             patch.object(self.extractor, "_filesystem_root",
                          return_value=Path(self.tmp_dir)), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[repo]), \
             patch(f"{_RULES_MOD}.should_skip_system_path", return_value=False):
            return self.extractor.extract_all_augment_rules()


class TestAugmentUserRules(_AugmentRulesHarness):
    def test_user_guidelines_and_rules_grouped_under_augment_dir(self):
        (self.augment_dir / "user-guidelines.md").write_text("be nice", encoding="utf-8")
        rules_dir = self.augment_dir / "rules"
        rules_dir.mkdir()
        (rules_dir / "style.md").write_text("two spaces", encoding="utf-8")
        (rules_dir / "security.mdx").write_text("no secrets", encoding="utf-8")

        projects = self._extract_user_only()
        # Everything coalesces under the ~/.augment dir as a single project_root.
        roots = {p["project_root"] for p in projects}
        self.assertEqual(roots, {str(self.augment_dir)})
        self.assertEqual(_names(projects), {"user-guidelines.md", "style.md", "security.mdx"})
        for r in _all_rules(projects):
            self.assertEqual(r["scope"], "user")

    def test_user_rules_mdx_regression(self):
        rules_dir = self.augment_dir / "rules"
        rules_dir.mkdir()
        (rules_dir / "only.mdx").write_text("mdx rule", encoding="utf-8")
        projects = self._extract_user_only()
        self.assertIn("only.mdx", _names(projects))


class TestAugmentProjectRules(_AugmentRulesHarness):
    def _make_repo(self) -> Path:
        repo = self.user_home / "repo"
        repo.mkdir(parents=True)
        return repo

    def test_augment_guidelines_and_rules_tree(self):
        repo = self._make_repo()
        (repo / ".augment-guidelines").write_text("repo rules", encoding="utf-8")
        rules_dir = repo / ".augment" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "a.md").write_text("a", encoding="utf-8")
        (rules_dir / "b.mdx").write_text("b", encoding="utf-8")

        projects = self._extract_with_project_root(repo)
        self.assertIn(".augment-guidelines", _names(projects))
        self.assertIn("a.md", _names(projects))
        self.assertIn("b.mdx", _names(projects))
        for r in _all_rules(projects):
            self.assertEqual(r["scope"], "project")

    def test_agents_md_and_claude_md_are_collected(self):
        """Revised D3: Augment discovers AGENTS.md AND CLAUDE.md — both collected."""
        repo = self._make_repo()
        (repo / "AGENTS.md").write_text("agents", encoding="utf-8")
        (repo / "CLAUDE.md").write_text("claude", encoding="utf-8")

        projects = self._extract_with_project_root(repo)
        names = _names(projects)
        self.assertIn("AGENTS.md", names)
        self.assertIn("CLAUDE.md", names)

    def test_agents_md_collected_hierarchically_in_subdir(self):
        """AGENTS.md/CLAUDE.md are discovered at any depth (not just repo root)."""
        repo = self._make_repo()
        (repo / "AGENTS.md").write_text("root agents", encoding="utf-8")
        sub = repo / "pkg" / "nested"
        sub.mkdir(parents=True)
        (sub / "CLAUDE.md").write_text("nested claude", encoding="utf-8")

        projects = self._extract_with_project_root(repo)
        names = _names(projects)
        self.assertIn("AGENTS.md", names)
        self.assertIn("CLAUDE.md", names)

    def test_no_frontmatter_contract(self):
        """Every emitted rule dict must be a subset of the backend's allowlist —
        no frontmatter keys leak into the dict (they stay inside ``content``)."""
        repo = self._make_repo()
        rules_dir = repo / ".augment" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "fm.md").write_text(
            "---\napplyTo: '**/*.py'\n---\nUse type hints", encoding="utf-8"
        )
        projects = self._extract_with_project_root(repo)
        rules = _all_rules(projects)
        self.assertTrue(rules)
        for r in rules:
            self.assertTrue(
                set(r.keys()).issubset(_ALLOWED_RULE_FIELDS),
                f"rule dict has non-allowlisted keys: {set(r.keys()) - _ALLOWED_RULE_FIELDS}",
            )
        # The frontmatter is preserved verbatim inside content.
        fm_rule = next(r for r in rules if r["file_name"] == "fm.md")
        self.assertIn("applyTo", fm_rule["content"])


class TestAugmentRulesNoUserProjectDuplication(_AugmentRulesHarness):
    """FIX 2: when the project walk descends into a user-home ``~/.augment`` it
    must NOT re-collect ``~/.augment/rules/**`` as scope "project" (which would
    duplicate the user-scope rule under a different project_root, defeating the
    per-project dedup). The user-augment-dir guard skips it.
    """

    def test_user_rule_not_duplicated_as_project(self):
        # The SAME home is seen by both the user scan and the project walk.
        rules_dir = self.augment_dir / "rules"
        rules_dir.mkdir()
        (rules_dir / "style.md").write_text("two spaces", encoding="utf-8")

        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: fn(self.user_home)), \
             patch.object(self.extractor, "_filesystem_root",
                          return_value=Path(self.tmp_dir)), \
             patch.object(self.extractor, "_iter_top_level_dirs",
                          return_value=[self.user_home]), \
             patch(f"{_RULES_MOD}.should_skip_system_path", return_value=False):
            projects = self.extractor.extract_all_augment_rules()

        style_rules = [r for r in _all_rules(projects) if r["file_name"] == "style.md"]
        # Exactly ONE record, and it is the user-scope one (not a project dup).
        self.assertEqual(len(style_rules), 1)
        self.assertEqual(style_rules[0]["scope"], "user")
        roots = {p["project_root"] for p in projects if p.get("rules")}
        self.assertEqual(roots, {str(self.augment_dir)})


class TestAugmentRulesSadPaths(_AugmentRulesHarness):
    def test_symlink_loop_does_not_hang(self):
        repo = self.user_home / "repo"
        rules_dir = repo / ".augment" / "rules"
        rules_dir.mkdir(parents=True)
        (rules_dir / "x.md").write_text("x", encoding="utf-8")
        # A self-referential symlink inside the rules tree must be skipped.
        try:
            os.symlink(str(rules_dir), str(rules_dir / "loop"))
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not supported")
        projects = self._extract_with_project_root(repo)
        self.assertIn("x.md", _names(projects))

    def test_depth_bound_respected(self):
        repo = self.user_home / "repo"
        repo.mkdir(parents=True)
        # A rule far beyond MAX_SEARCH_DEPTH must not crash the walk.
        deep = repo
        for i in range(15):
            deep = deep / f"d{i}"
        deep.mkdir(parents=True)
        (deep / "AGENTS.md").write_text("deep", encoding="utf-8")
        (repo / "AGENTS.md").write_text("shallow", encoding="utf-8")
        # Should not raise; the shallow file is collected.
        projects = self._extract_with_project_root(repo)
        self.assertIn("AGENTS.md", _names(projects))

    def test_permission_error_never_raises(self):
        repo = self.user_home / "repo"
        repo.mkdir(parents=True)
        (repo / "AGENTS.md").write_text("ok", encoding="utf-8")
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            # Must return cleanly (the walk swallows the error).
            projects = self._extract_with_project_root(repo)
        self.assertIsInstance(projects, list)


if __name__ == "__main__":
    unittest.main()
