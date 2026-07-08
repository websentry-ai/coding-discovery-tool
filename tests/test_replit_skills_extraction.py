"""
Unit tests for Replit skills extraction.

Replit is PROJECT-SCOPE ONLY: skills live in-repo at .agents/skills/<name>/SKILL.md.
There is no local user/global path (global skills live server-side in Workspace
Settings), so user-level extraction must be a correct no-op.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.replit_skills_helpers import (
    REPLIT_PARENT_DIR_NAMES,
    REPLIT_USER_DIR_NAMES,
    REPLIT_SKILL_CONFIG,
    REPLIT_ITEM_CONFIGS,
    find_replit_item_project_root,
    extract_replit_items_from_directory,
    extract_replit_user_level_items,
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


class TestReplitConstants(unittest.TestCase):
    def test_project_parent_is_agents_only(self):
        self.assertEqual(REPLIT_PARENT_DIR_NAMES, (".agents",))

    def test_user_dirs_empty(self):
        # Project-scope only: no local user/global path.
        self.assertEqual(tuple(REPLIT_USER_DIR_NAMES), ())

    def test_config(self):
        self.assertIsInstance(REPLIT_SKILL_CONFIG, ItemTypeConfig)
        self.assertEqual(REPLIT_SKILL_CONFIG.dir_name, "skills")
        self.assertEqual(REPLIT_ITEM_CONFIGS, [REPLIT_SKILL_CONFIG])


class TestFindReplitProjectRoot(unittest.TestCase):
    def test_project_agents_dir(self):
        rule = Path("/Users/t/proj/.agents/skills/x/SKILL.md")
        self.assertEqual(find_replit_item_project_root(rule, REPLIT_SKILL_CONFIG), Path("/Users/t/proj"))


class TestReplitHelperExtraction(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_user_extraction_is_noop(self):
        home = self.tmp / "Users" / "alice"
        # Even if an ~/.agents/skills exists, Replit user extraction collects nothing
        # (USER_DIR_NAMES is empty) — those are attributed to other tools, not Replit.
        _mk(home / ".agents", "should_not_appear")
        user_skills = []
        extract_replit_user_level_items(home, user_skills, extract_single_rule_file, REPLIT_ITEM_CONFIGS)
        self.assertEqual(user_skills, [])

    def test_project_extraction(self):
        proj = self.tmp / "proj"
        _mk(proj / ".agents", "p1")
        pbr = {}
        extract_replit_items_from_directory(
            proj / ".agents" / "skills", pbr, extract_single_rule_file, add_skill_to_project, REPLIT_SKILL_CONFIG
        )
        result = build_skills_project_list(pbr)
        self.assertEqual(result[0]["project_root"], str(proj))
        self.assertEqual(result[0]["skills"][0]["skill_name"], "p1")
        self.assertEqual(result[0]["skills"][0]["source"], "standalone")


class TestMacOSReplitExtractor(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_extract_all_project_only(self):
        # Scope the walk to the temp home (extract_all_skills() walks the real
        # filesystem root, so drive _walk_for_skills directly like the other tools).
        from scripts.coding_discovery_tools.macos.replit import skills_extractor as se
        from scripts.coding_discovery_tools.claude_code_skills_helpers import build_skills_project_list
        home = self.tmp / "Users" / "bob"
        _mk(home / "proj" / ".agents", "p1")
        ex = se.MacOSReplitSkillsExtractor()
        with patch.object(se, "should_skip_system_path", return_value=False), \
             patch("pathlib.Path.home", return_value=home):
            user_skills = []
            ex._extract_user_level_skills(user_skills)   # project-scope only -> no-op, stays empty
            pbr = {}
            ex._walk_for_skills(home, home, pbr, 0)
        self.assertEqual(user_skills, [])
        proj_names = [s["skill_name"] for pr in build_skills_project_list(pbr) for s in pr["skills"]]
        self.assertEqual(proj_names, ["p1"])


if __name__ == "__main__":
    unittest.main()
