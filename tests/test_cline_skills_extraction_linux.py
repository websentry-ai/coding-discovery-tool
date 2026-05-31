"""
Unit tests for Cline skills extraction on Linux.

Mirrors the macOS-specific extractor tests in test_cline_skills_extraction.py
but targets LinuxClineSkillsExtractor. Linux iterates get_linux_user_homes()
instead of mocking Path.home() + is_running_as_root.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLinuxClineSkillsExtractor(unittest.TestCase):
    """Tests for Linux Cline skills extractor."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.get_linux_user_homes')
    def test_user_level_skills(self, mock_get_homes):
        from scripts.coding_discovery_tools.linux.cline.skills_extractor import LinuxClineSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        skill_dir = fake_home / ".cline" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        extractor = LinuxClineSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "skill")
        self.assertEqual(user_skills[0]["skill_name"], "my-skill")
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.get_linux_user_homes')
    def test_no_skills_dir_returns_empty(self, mock_get_homes):
        from scripts.coding_discovery_tools.linux.cline.skills_extractor import LinuxClineSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        extractor = LinuxClineSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 0)

    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_skills(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.linux.cline.skills_extractor import LinuxClineSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".cline" / "skills" / "proj-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Project Skill")

        extractor = LinuxClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "proj-skill")

    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_clinerules_skills(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.linux.cline.skills_extractor import LinuxClineSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".clinerules" / "skills" / "api-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# API Skill")

        extractor = LinuxClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "api-skill")

    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_skills_across_all_parent_dirs(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.linux.cline.skills_extractor import LinuxClineSkillsExtractor

        project = self.temp_path / "project"
        for parent_dir, skill_name in [(".cline", "skill-a"), (".clinerules", "skill-b"), (".claude", "skill-c")]:
            skill_dir = project / parent_dir / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {skill_name}")

        extractor = LinuxClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 3)
        names = {s["skill_name"] for s in skills}
        self.assertEqual(names, {"skill-a", "skill-b", "skill-c"})


if __name__ == "__main__":
    unittest.main()
