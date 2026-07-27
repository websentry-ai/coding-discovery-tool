"""
Unit tests for Roo Code skills extraction.

Roo supports the plain ``skills/`` dir plus MODE-specific ``skills-{mode}/`` dirs
(mirroring its ``rules-{mode}/`` convention), at ~/.roo, ~/.agents (user) and
.roo, .agents (project). Mode dirs are discovered dynamically.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.roo_skills_helpers import (
    ROO_PARENT_DIR_NAMES,
    ROO_USER_DIR_NAMES,
    ROO_SKILL_CONFIG,
    SKILLS_MODE_PREFIX,
    is_roo_skill_type_dirname,
    iter_roo_skill_type_dirs,
    extract_roo_items_from_directory,
    extract_roo_user_level_items,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    ItemTypeConfig,
    build_skills_project_list,
    add_skill_to_project,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file


def _mk(type_dir: Path, name: str):
    d = type_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")


class TestRooConstants(unittest.TestCase):
    def test_parent_and_user_dirs(self):
        self.assertEqual(ROO_PARENT_DIR_NAMES, (".roo", ".agents"))
        self.assertEqual(ROO_USER_DIR_NAMES, (".roo", ".agents"))

    def test_mode_prefix(self):
        self.assertEqual(SKILLS_MODE_PREFIX, "skills-")

    def test_base_config(self):
        self.assertIsInstance(ROO_SKILL_CONFIG, ItemTypeConfig)
        self.assertEqual(ROO_SKILL_CONFIG.dir_name, "skills")


class TestRooModeDirDetection(unittest.TestCase):
    def test_is_roo_skill_type_dirname(self):
        self.assertTrue(is_roo_skill_type_dirname("skills"))
        self.assertTrue(is_roo_skill_type_dirname("skills-code"))
        self.assertTrue(is_roo_skill_type_dirname("skills-architect"))
        self.assertFalse(is_roo_skill_type_dirname("rules"))
        self.assertFalse(is_roo_skill_type_dirname("skill"))

    def test_iter_finds_plain_and_mode_dirs(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            tool = tmp / ".roo"
            (tool / "skills").mkdir(parents=True)
            (tool / "skills-code").mkdir()
            (tool / "skills-architect").mkdir()
            (tool / "rules").mkdir()  # not a skills dir
            names = sorted(p.name for p in iter_roo_skill_type_dirs(tool))
            self.assertEqual(names, ["skills", "skills-architect", "skills-code"])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestRooHelperExtraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_plain_and_mode_dirs(self):
        home = self.tmp / "Users" / "alice"
        _mk(home / ".roo" / "skills", "base")
        _mk(home / ".roo" / "skills-architect", "arch")
        _mk(home / ".agents" / "skills", "ag")
        user_skills = []
        extract_roo_user_level_items(home, user_skills, extract_single_rule_file)
        self.assertEqual(sorted(s["skill_name"] for s in user_skills), ["ag", "arch", "base"])
        for s in user_skills:
            self.assertEqual(s["scope"], "user")
            self.assertEqual(s["project_path"], str(home))

    def test_project_mode_dir_project_root(self):
        proj = self.tmp / "proj"
        _mk(proj / ".roo" / "skills-code", "p2")
        pbr = {}
        extract_roo_items_from_directory(
            proj / ".roo" / "skills-code", pbr, extract_single_rule_file, add_skill_to_project
        )
        result = build_skills_project_list(pbr)
        self.assertEqual(result[0]["project_root"], str(proj))
        self.assertEqual(result[0]["skills"][0]["skill_name"], "p2")


class TestMacOSRooExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_all_collects_mode_dirs(self):
        from scripts.coding_discovery_tools.macos.roo_code import skills_extractor as se
        home = self.tmp / "Users" / "alice"
        _mk(home / ".roo" / "skills", "base")
        _mk(home / ".roo" / "skills-architect", "arch")
        _mk(home / "proj" / ".roo" / "skills-code", "p2")
        _mk(home / "proj" / ".agents" / "skills", "p3")
        ex = se.MacOSRooSkillsExtractor()
        with patch.object(se, "is_running_as_root", return_value=False), \
             patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            user_skills = []
            ex._extract_user_level_skills(user_skills)
            pbr = {}
            ex._walk_for_skills(home, home, pbr, 0)
        self.assertEqual(sorted(s["skill_name"] for s in user_skills), ["arch", "base"])
        proj_names = sorted(s["skill_name"] for pr in build_skills_project_list(pbr) for s in pr["skills"])
        self.assertEqual(proj_names, ["p2", "p3"])


if __name__ == "__main__":
    unittest.main()
