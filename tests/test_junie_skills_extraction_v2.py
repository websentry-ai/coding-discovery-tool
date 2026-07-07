"""
Unit tests for Junie skills extraction.

Junie standardizes on the ``.junie`` directory at both scopes:
    user:    ~/.junie/skills/<name>/SKILL.md
    project: <repo>/.junie/skills/<name>/SKILL.md
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.junie_skills_helpers import (
    SKILLS_DIR_NAME,
    SKILL_FILE_NAME,
    JUNIE_PARENT_DIR_NAMES,
    JUNIE_USER_DIR_NAMES,
    JUNIE_SKILL_CONFIG,
    JUNIE_ITEM_CONFIGS,
    find_junie_item_project_root,
    extract_junie_items_from_directory,
    extract_junie_user_level_items,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    ItemTypeConfig,
    build_skills_project_list,
    add_skill_to_project,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file


class TestJunieConstants(unittest.TestCase):
    def test_skills_dir_name(self):
        self.assertEqual(SKILLS_DIR_NAME, "skills")

    def test_skill_file_name(self):
        self.assertEqual(SKILL_FILE_NAME, "SKILL.md")

    def test_parent_dir_names_is_junie_only(self):
        self.assertEqual(JUNIE_PARENT_DIR_NAMES, (".junie",))

    def test_user_dir_names_is_junie_only(self):
        self.assertEqual(JUNIE_USER_DIR_NAMES, (".junie",))


class TestJunieItemConfigIntegrity(unittest.TestCase):
    def test_skill_config_is_item_type_config(self):
        self.assertIsInstance(JUNIE_SKILL_CONFIG, ItemTypeConfig)

    def test_skill_config_fields(self):
        self.assertEqual(JUNIE_SKILL_CONFIG.type_name, "skill")
        self.assertEqual(JUNIE_SKILL_CONFIG.dir_name, "skills")
        self.assertEqual(JUNIE_SKILL_CONFIG.layout, "nested")

    def test_name_extractor_returns_parent_dir(self):
        f = Path("/x/.junie/skills/deploy/SKILL.md")
        self.assertEqual(JUNIE_SKILL_CONFIG.name_extractor(f), "deploy")

    def test_item_configs_list(self):
        self.assertEqual(JUNIE_ITEM_CONFIGS, [JUNIE_SKILL_CONFIG])


class TestFindJunieItemProjectRoot(unittest.TestCase):
    def test_project_level_junie_dir(self):
        rule = Path("/Users/test/proj/.junie/skills/lint/SKILL.md")
        self.assertEqual(find_junie_item_project_root(rule, JUNIE_SKILL_CONFIG), Path("/Users/test/proj"))

    def test_user_level_junie_dir(self):
        rule = Path("/Users/test/.junie/skills/deploy/SKILL.md")
        self.assertEqual(find_junie_item_project_root(rule, JUNIE_SKILL_CONFIG), Path("/Users/test"))

    def test_unknown_parent_falls_back(self):
        rule = Path("/Users/test/.cursor/skills/x/SKILL.md")
        # .cursor is not a Junie parent dir; generic fallback returns the file's parent
        self.assertEqual(find_junie_item_project_root(rule, JUNIE_SKILL_CONFIG), rule.parent)


class TestJunieHelperExtraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_skill(self, base: Path, name: str):
        d = base / SKILLS_DIR_NAME / name
        d.mkdir(parents=True, exist_ok=True)
        (d / SKILL_FILE_NAME).write_text(
            f"---\nname: {name}\ndescription: {name} desc\n---\nbody", encoding="utf-8"
        )
        return d / SKILL_FILE_NAME

    def test_project_extraction(self):
        junie = self.tmp / "proj" / ".junie"
        self._write_skill(junie, "lint")
        projects_by_root = {}
        extract_junie_items_from_directory(
            junie / SKILLS_DIR_NAME, projects_by_root, extract_single_rule_file,
            add_skill_to_project, JUNIE_SKILL_CONFIG,
        )
        result = build_skills_project_list(projects_by_root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_root"], str(self.tmp / "proj"))
        skill = result[0]["skills"][0]
        self.assertEqual(skill["skill_name"], "lint")
        self.assertEqual(skill["type"], "skill")
        self.assertEqual(skill["scope"], "project")
        self.assertEqual(skill["source"], "standalone")

    def test_user_extraction(self):
        home = self.tmp / "Users" / "alice"
        self._write_skill(home / ".junie", "deploy")
        user_skills = []
        extract_junie_user_level_items(home, user_skills, extract_single_rule_file, JUNIE_ITEM_CONFIGS)
        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["skill_name"], "deploy")
        self.assertEqual(user_skills[0]["scope"], "user")
        self.assertEqual(user_skills[0]["source"], "standalone")
        self.assertEqual(user_skills[0]["project_path"], str(home))

    def test_user_extraction_no_junie_dir_is_empty(self):
        home = self.tmp / "Users" / "bob"
        home.mkdir(parents=True)
        user_skills = []
        extract_junie_user_level_items(home, user_skills, extract_single_rule_file, JUNIE_ITEM_CONFIGS)
        self.assertEqual(user_skills, [])


class TestMacOSJunieSkillsExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_skill(self, base: Path, name: str):
        d = base / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")

    def test_extract_all_skills_user_and_project(self):
        from scripts.coding_discovery_tools.macos.junie import skills_extractor as se
        home = self.tmp / "Users" / "alice"
        self._write_skill(home / ".junie", "deploy")            # user
        self._write_skill(home / "proj" / ".junie", "lint")     # project

        ex = se.MacOSJunieSkillsExtractor()
        with patch.object(se, "is_running_as_root", return_value=False), \
             patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            user_skills = []
            ex._extract_user_level_skills(user_skills)
            projects_by_root = {}
            ex._walk_for_skills(home, home, projects_by_root, 0)

        self.assertEqual([s["skill_name"] for s in user_skills], ["deploy"])
        projects = build_skills_project_list(projects_by_root)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(home / "proj"))
        self.assertEqual(projects[0]["skills"][0]["skill_name"], "lint")


if __name__ == "__main__":
    unittest.main()
