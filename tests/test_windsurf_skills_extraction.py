"""
Unit tests for Windsurf (Cascade) skills extraction.

Windsurf's user tool dir is the nested ~/.codeium/windsurf; at project scope
Cascade scans .windsurf plus documented compat dirs (.agents/.claude/.github/
.cursor/.codex). Like OpenCode, a user skill must not be double-counted.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.windsurf_skills_helpers import (
    WINDSURF_DIR_NAME,
    CODEIUM_WINDSURF_USER_DIR,
    WINDSURF_PARENT_DIR_NAMES,
    WINDSURF_USER_DIR_NAMES,
    WINDSURF_USER_PARENT_DIR_NAMES,
    WINDSURF_SKILL_CONFIG,
    WINDSURF_ITEM_CONFIGS,
    find_windsurf_item_project_root,
    extract_windsurf_items_from_directory,
    extract_windsurf_user_level_items,
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


class TestWindsurfConstants(unittest.TestCase):
    def test_user_dir_is_nested_codeium(self):
        self.assertEqual(CODEIUM_WINDSURF_USER_DIR, ".codeium/windsurf")
        self.assertIn(".codeium/windsurf", WINDSURF_USER_DIR_NAMES)

    def test_project_parents_include_windsurf_and_compat(self):
        for d in (".windsurf", ".agents", ".claude", ".github", ".cursor", ".codex"):
            self.assertIn(d, WINDSURF_PARENT_DIR_NAMES)

    def test_user_parents_use_codeium_for_home_resolution(self):
        self.assertIn(".codeium", WINDSURF_USER_PARENT_DIR_NAMES)

    def test_bare_windsurf_leaf_not_in_project_parents(self):
        self.assertNotIn("windsurf", WINDSURF_PARENT_DIR_NAMES)
        self.assertNotIn("codeium", WINDSURF_PARENT_DIR_NAMES)


class TestWindsurfConfigIntegrity(unittest.TestCase):
    def test_config(self):
        self.assertIsInstance(WINDSURF_SKILL_CONFIG, ItemTypeConfig)
        self.assertEqual(WINDSURF_SKILL_CONFIG.dir_name, "skills")
        self.assertEqual(WINDSURF_ITEM_CONFIGS, [WINDSURF_SKILL_CONFIG])


class TestFindWindsurfProjectRoot(unittest.TestCase):
    def test_project_windsurf_dir(self):
        rule = Path("/Users/t/proj/.windsurf/skills/x/SKILL.md")
        self.assertEqual(find_windsurf_item_project_root(rule, WINDSURF_SKILL_CONFIG), Path("/Users/t/proj"))

    def test_project_codex_compat(self):
        rule = Path("/Users/t/proj/.codex/skills/x/SKILL.md")
        self.assertEqual(find_windsurf_item_project_root(rule, WINDSURF_SKILL_CONFIG), Path("/Users/t/proj"))


class TestWindsurfHelperExtraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_codeium_windsurf_resolves_to_home(self):
        home = self.tmp / "Users" / "alice"
        _mk(home / ".codeium" / "windsurf", "wuser")
        _mk(home / ".agents", "wagents")
        user_skills = []
        extract_windsurf_user_level_items(home, user_skills, extract_single_rule_file, WINDSURF_ITEM_CONFIGS)
        by_name = {s["skill_name"]: s for s in user_skills}
        self.assertEqual(set(by_name), {"wuser", "wagents"})
        self.assertEqual(by_name["wuser"]["project_path"], str(home))
        self.assertEqual(by_name["wagents"]["project_path"], str(home))


class TestMacOSWindsurfExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_all_user_and_project_no_double_count(self):
        from scripts.coding_discovery_tools.macos.windsurf import skills_extractor as se
        home = self.tmp / "Users" / "bob"
        _mk(home / ".codeium" / "windsurf", "wuser")   # user only
        _mk(home / "proj" / ".windsurf", "wp1")         # project primary
        _mk(home / "proj" / ".claude", "wp2")           # project compat
        ex = se.MacOSWindsurfSkillsExtractor()
        with patch.object(se, "is_running_as_root", return_value=False), \
             patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            user_skills = []
            ex._extract_user_level_skills(user_skills)
            pbr = {}
            ex._walk_for_skills(home, home, pbr, 0)
        user_names = [s["skill_name"] for s in user_skills]
        proj = build_skills_project_list(pbr)
        proj_names = sorted(s["skill_name"] for pr in proj for s in pr["skills"])
        self.assertIn("wuser", user_names)
        self.assertNotIn("wuser", proj_names)   # user skill not double-counted
        self.assertEqual(proj_names, ["wp1", "wp2"])
        for pr in proj:
            self.assertEqual(pr["project_root"], str(home / "proj"))


if __name__ == "__main__":
    unittest.main()
