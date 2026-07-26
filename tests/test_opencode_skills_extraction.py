"""
Unit tests for OpenCode skills extraction.

OpenCode's user tool dir (~/.config/opencode) differs from its project dir
(.opencode), so the helper uses two parent-name sets. Key invariant tested here:
a user skill in ~/.config/opencode/skills must NOT be double-counted as a
project skill by the walk.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.opencode_skills_helpers import (
    OPENCODE_DIR_NAME,
    SKILLS_DIR_NAME,
    OPENCODE_PARENT_DIR_NAMES,
    OPENCODE_USER_DIR_NAMES,
    OPENCODE_USER_PARENT_DIR_NAMES,
    OPENCODE_SKILL_CONFIG,
    OPENCODE_ITEM_CONFIGS,
    find_opencode_item_project_root,
    extract_opencode_items_from_directory,
    extract_opencode_user_level_items,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    ItemTypeConfig,
    build_skills_project_list,
    add_skill_to_project,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import extract_single_rule_file


def _mk(base: Path, name: str):
    d = base / "skills" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: d\n---\nb", encoding="utf-8")


class TestOpenCodeConstants(unittest.TestCase):
    def test_project_parent_dirs_are_dot_form(self):
        self.assertEqual(OPENCODE_PARENT_DIR_NAMES, (".opencode", ".claude", ".agents"))

    def test_user_dirs_include_nested_config(self):
        self.assertEqual(OPENCODE_USER_DIR_NAMES, (".config/opencode", ".claude", ".agents"))

    def test_user_parent_dirs_use_config_for_home_resolution(self):
        self.assertEqual(OPENCODE_USER_PARENT_DIR_NAMES, (".config", ".claude", ".agents"))

    def test_bare_opencode_not_in_project_parents(self):
        # Guards against the walk matching ~/.config/opencode and double-counting.
        self.assertNotIn("opencode", OPENCODE_PARENT_DIR_NAMES)


class TestOpenCodeConfigIntegrity(unittest.TestCase):
    def test_skill_config(self):
        self.assertIsInstance(OPENCODE_SKILL_CONFIG, ItemTypeConfig)
        self.assertEqual(OPENCODE_SKILL_CONFIG.dir_name, "skills")
        self.assertEqual(OPENCODE_SKILL_CONFIG.layout, "nested")
        self.assertEqual(OPENCODE_ITEM_CONFIGS, [OPENCODE_SKILL_CONFIG])


class TestFindOpenCodeProjectRoot(unittest.TestCase):
    def test_project_opencode_dir(self):
        rule = Path("/Users/t/proj/.opencode/skills/x/SKILL.md")
        self.assertEqual(find_opencode_item_project_root(rule, OPENCODE_SKILL_CONFIG), Path("/Users/t/proj"))

    def test_project_claude_compat(self):
        rule = Path("/Users/t/proj/.claude/skills/x/SKILL.md")
        self.assertEqual(find_opencode_item_project_root(rule, OPENCODE_SKILL_CONFIG), Path("/Users/t/proj"))


class TestOpenCodeHelperExtraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_config_opencode_resolves_to_home(self):
        home = self.tmp / "Users" / "alice"
        _mk(home / ".config" / "opencode", "globe")
        _mk(home / ".agents", "ua")
        user_skills = []
        extract_opencode_user_level_items(home, user_skills, extract_single_rule_file, OPENCODE_ITEM_CONFIGS)
        by_name = {s["skill_name"]: s for s in user_skills}
        self.assertEqual(set(by_name), {"globe", "ua"})
        # Both resolve project_path to the home (not ~/.config or a deep skill dir).
        self.assertEqual(by_name["globe"]["project_path"], str(home))
        self.assertEqual(by_name["ua"]["project_path"], str(home))
        self.assertEqual(by_name["globe"]["scope"], "user")

    def test_project_dirs_collected(self):
        proj = self.tmp / "proj"
        _mk(proj / ".opencode", "p1")
        _mk(proj / ".claude", "p2")
        _mk(proj / ".agents", "p3")
        pbr = {}
        for parent in (".opencode", ".claude", ".agents"):
            extract_opencode_items_from_directory(
                proj / parent / "skills", pbr, extract_single_rule_file, add_skill_to_project, OPENCODE_SKILL_CONFIG
            )
        result = build_skills_project_list(pbr)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_root"], str(proj))
        self.assertEqual(sorted(s["skill_name"] for s in result[0]["skills"]), ["p1", "p2", "p3"])


class TestMacOSOpenCodeExtractorNoDoubleCount(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_skill_not_double_counted_as_project(self):
        from scripts.coding_discovery_tools.macos.opencode import skills_extractor as se
        home = self.tmp / "Users" / "bob"
        _mk(home / ".config" / "opencode", "globe")   # user only
        _mk(home / "proj" / ".opencode", "p1")         # project only
        ex = se.MacOSOpenCodeSkillsExtractor()
        with patch.object(se, "is_running_as_root", return_value=False), \
             patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            user_skills = []
            ex._extract_user_level_skills(user_skills)
            pbr = {}
            ex._walk_for_skills(home, home, pbr, 0)

        user_names = [s["skill_name"] for s in user_skills]
        proj_names = [s["skill_name"] for pr in build_skills_project_list(pbr) for s in pr["skills"]]
        self.assertIn("globe", user_names)
        # The user skill must NOT reappear as a project skill.
        self.assertNotIn("globe", proj_names)
        self.assertEqual(proj_names, ["p1"])


if __name__ == "__main__":
    unittest.main()
