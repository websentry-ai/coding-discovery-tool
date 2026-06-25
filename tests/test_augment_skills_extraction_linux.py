"""
Linux-variant integration tests for Augment Code skills/commands extraction.

The Linux extractor is a thin subclass of the macOS one overriding only OS seams
(file-metadata read, walk-skip predicate, all-users scan, filesystem root,
top-level enumeration, and the ``/home``+``/root`` user-level-dir check). These
tests exercise ``extract_all_skills()`` through the Linux subclass with the seams
pinned to a hermetic temp tree.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.augment.augment_skills_extractor import (
    LinuxAugmentSkillsExtractor,
)


class TestLinuxAugmentSkills(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "home" / "user"
        self.augment_dir = self.user_home / ".augment"
        self.augment_dir.mkdir(parents=True)
        self.extractor = LinuxAugmentSkillsExtractor()

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_user_skill_via_linux_subclass(self):
        skill_dir = self.augment_dir / "skills" / "deploy"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("deploy it", encoding="utf-8")
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: fn(self.user_home)), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[]):
            result = self.extractor.extract_all_skills()
        self.assertEqual(len(result["user_skills"]), 1)
        self.assertEqual(result["user_skills"][0]["type"], "skill")
        self.assertEqual(result["user_skills"][0]["source"], "standalone")

    def test_project_command_via_linux_subclass(self):
        repo = self.user_home / "repo"
        commands_dir = repo / ".augment" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "review.md").write_text("review", encoding="utf-8")
        with patch.object(self.extractor, "_scan_all_user_homes",
                          side_effect=lambda fn: None), \
             patch.object(self.extractor, "_iter_top_level_dirs", return_value=[repo]), \
             patch.object(self.extractor, "_should_skip_walk_item", return_value=False), \
             patch.object(self.extractor, "_is_user_level_skill_dir", return_value=False):
            result = self.extractor.extract_all_skills()
        all_skills = [s for p in result["project_skills"] for s in p["skills"]]
        self.assertEqual(len(all_skills), 1)
        self.assertEqual(all_skills[0]["type"], "command")
        self.assertEqual(all_skills[0]["skill_name"], "review")

    def test_user_level_skill_dir_recognized_for_home_user(self):
        """The Linux user-level-dir check pins users-root to ``/home``."""
        type_dir = Path("/home/alice/.augment/skills")
        self.assertTrue(self.extractor._is_user_level_skill_dir(type_dir))

    def test_user_level_skill_dir_recognized_for_root_home(self):
        type_dir = Path("/root/.augment/skills")
        self.assertTrue(self.extractor._is_user_level_skill_dir(type_dir))

    def test_project_skill_dir_not_user_level(self):
        type_dir = Path("/home/alice/projects/repo/.augment/skills")
        self.assertFalse(self.extractor._is_user_level_skill_dir(type_dir))


if __name__ == "__main__":
    unittest.main()
