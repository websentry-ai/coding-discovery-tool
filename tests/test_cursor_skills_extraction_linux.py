"""
Unit tests for Cursor skills extraction on Linux.

Mirrors the macOS-specific extractor tests in test_cursor_skills_extraction.py
but targets LinuxCursorSkillsExtractor. Linux iterates get_linux_user_homes()
instead of mocking Path.home() + is_running_as_root.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLinuxCursorSkillsExtractor(unittest.TestCase):
    """Tests for Linux Cursor skills extractor."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.get_linux_user_homes')
    def test_user_level_skills(self, mock_get_homes):
        """Extractor must return user-level skills."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create user-level skill
        skill_dir = fake_home / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        extractor = LinuxCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "skill")
        self.assertEqual(user_skills[0]["skill_name"], "my-skill")
        # project_root should be stripped for user-level items
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.get_linux_user_homes')
    def test_no_skills_dir_returns_empty(self, mock_get_homes):
        """Extractor returns empty list when no .cursor/skills/ directory exists."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        extractor = LinuxCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 0)

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_skills(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find skills in project .cursor/skills/."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".cursor" / "skills" / "lint"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Lint")

        extractor = LinuxCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "skill")
        self.assertEqual(items[0]["skill_name"], "lint")

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_multiple_skills(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find multiple skills in a project."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        project = self.temp_path / "project"

        for name in ("skill-a", "skill-b", "skill-c"):
            skill_dir = project / ".cursor" / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}")

        extractor = LinuxCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 3)
        names = {i["skill_name"] for i in items}
        self.assertEqual(names, {"skill-a", "skill-b", "skill-c"})


class TestLinuxCursorSkillsExtractorWithCommands(unittest.TestCase):
    """Tests for Linux Cursor skills extractor with commands support."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.get_linux_user_homes')
    def test_user_level_commands(self, mock_get_homes):
        """Extractor must return user-level commands."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create user-level command
        commands_dir = fake_home / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "code-review.md").write_text("# Code Review")

        extractor = LinuxCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "command")
        self.assertEqual(user_skills[0]["skill_name"], "code-review")
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.get_linux_user_homes')
    def test_user_level_skills_and_commands_coexist(self, mock_get_homes):
        """Extractor must return both skills and commands from user level."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create user-level skill
        skill_dir = fake_home / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        # Create user-level command
        commands_dir = fake_home / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "my-command.md").write_text("# My Command")

        extractor = LinuxCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 2)
        types = {s["type"] for s in user_skills}
        self.assertEqual(types, {"skill", "command"})

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find commands in project .cursor/commands/."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "lint.md").write_text("# Lint")

        extractor = LinuxCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "command")
        self.assertEqual(items[0]["skill_name"], "lint")

    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_skills_and_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find both skills and commands in a project."""
        from scripts.coding_discovery_tools.linux.cursor.skills_extractor import LinuxCursorSkillsExtractor

        project = self.temp_path / "project"

        # Create a skill
        skill_dir = project / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")

        # Create a command
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "my-command.md").write_text("# Command")

        extractor = LinuxCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 2)
        types = {i["type"] for i in items}
        self.assertEqual(types, {"skill", "command"})


if __name__ == "__main__":
    unittest.main()
