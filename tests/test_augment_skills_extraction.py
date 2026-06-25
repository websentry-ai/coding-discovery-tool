"""
Integration tests for Augment Code skills/commands extraction (macOS).

Exercises the outermost surface (``extract_all_skills()``):

  - User skills (nested ``~/.augment/skills/<name>/SKILL.md``) -> type "skill",
    source "standalone".
  - User commands (flat ``~/.augment/commands/*.md``) -> type "command".
  - Project commands (flat ``<ws>/.augment/commands/*.md``) -> type "command".
  - Project skills (nested) grouped by project root.
  - Dedup, empty dir -> nothing, missing SKILL.md skipped.

The temp dir lives under /var (skipped by the real ``should_skip_system_path``),
so the project walk neutralises the skip predicate via the extractor's seam.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.augment.augment_skills_extractor import (
    MacOSAugmentSkillsExtractor,
)


class _AugmentSkillsHarness(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.augment_dir = self.user_home / ".augment"
        self.augment_dir.mkdir(parents=True)
        self.extractor = MacOSAugmentSkillsExtractor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _extract_user_only(self):
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: fn(self.user_home)), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[]):
            return self.extractor.extract_all_skills()

    def _extract_project_only(self, repo: Path):
        # ``_filesystem_root`` is pinned to the temp ancestor so the walk's
        # ``relative_to(root)`` depth check works on Windows too (a real path like
        # ``C:\...\repo`` is not relative to the macOS class's default ``/``).
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: None), \
             patch.object(self.extractor, "_filesystem_root",
                          return_value=Path(self.tmp_dir)), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[repo]), \
             patch.object(self.extractor, "_should_skip_walk_item", return_value=False), \
             patch.object(self.extractor, "_is_user_level_skill_dir", return_value=False):
            return self.extractor.extract_all_skills()

    def _write_nested_skill(self, base: Path, name: str, body="do the thing"):
        skill_dir = base / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")

    def _write_command(self, base: Path, name: str, body="run it"):
        commands_dir = base / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        (commands_dir / f"{name}.md").write_text(body, encoding="utf-8")


class TestAugmentUserSkills(_AugmentSkillsHarness):
    def test_user_nested_skill_type_and_source(self):
        self._write_nested_skill(self.augment_dir, "deploy")
        result = self._extract_user_only()
        user = result["user_skills"]
        self.assertEqual(len(user), 1)
        self.assertEqual(user[0]["type"], "skill")
        self.assertEqual(user[0]["source"], "standalone")
        self.assertEqual(user[0]["skill_name"], "deploy")
        self.assertEqual(user[0]["scope"], "user")

    def test_user_flat_command_type(self):
        self._write_command(self.augment_dir, "review")
        result = self._extract_user_only()
        user = result["user_skills"]
        self.assertEqual(len(user), 1)
        self.assertEqual(user[0]["type"], "command")
        self.assertEqual(user[0]["skill_name"], "review")

    def test_missing_skill_md_skipped(self):
        # A skill subdir with NO SKILL.md is not collected.
        (self.augment_dir / "skills" / "empty").mkdir(parents=True)
        result = self._extract_user_only()
        self.assertEqual(result["user_skills"], [])

    def test_empty_dirs_yield_nothing(self):
        (self.augment_dir / "skills").mkdir()
        (self.augment_dir / "commands").mkdir()
        result = self._extract_user_only()
        self.assertEqual(result["user_skills"], [])
        self.assertEqual(result["project_skills"], [])


class TestAugmentProjectSkills(_AugmentSkillsHarness):
    def test_project_command_flat(self):
        repo = self.user_home / "repo"
        augment = repo / ".augment"
        self._write_command(augment, "bug-fix")
        result = self._extract_project_only(repo)
        all_skills = [s for p in result["project_skills"] for s in p["skills"]]
        self.assertEqual(len(all_skills), 1)
        self.assertEqual(all_skills[0]["type"], "command")
        self.assertEqual(all_skills[0]["skill_name"], "bug-fix")

    def test_project_nested_skill_grouped_by_root(self):
        repo = self.user_home / "repo"
        augment = repo / ".augment"
        self._write_nested_skill(augment, "perf")
        result = self._extract_project_only(repo)
        self.assertEqual(len(result["project_skills"]), 1)
        self.assertEqual(result["project_skills"][0]["project_root"], str(repo))
        self.assertEqual(result["project_skills"][0]["skills"][0]["skill_name"], "perf")

    def test_project_skills_deduped(self):
        # The dedup happens downstream in process_single_tool; here verify the
        # extractor emits one entry per distinct file (no accidental duplication).
        repo = self.user_home / "repo"
        augment = repo / ".augment"
        self._write_nested_skill(augment, "alpha")
        self._write_command(augment, "beta")
        result = self._extract_project_only(repo)
        all_skills = [s for p in result["project_skills"] for s in p["skills"]]
        names = sorted(s["skill_name"] for s in all_skills)
        self.assertEqual(names, ["alpha", "beta"])


if __name__ == "__main__":
    unittest.main()
