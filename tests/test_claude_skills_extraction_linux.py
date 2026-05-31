"""
Unit tests for Claude Code skills extraction on Linux.

Mirrors the macOS-specific extractor tests in test_claude_skills_extraction.py
but targets LinuxClaudeSkillsExtractor. Linux iterates get_linux_user_homes()
instead of mocking Path.home() + is_running_as_root.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class TestLinuxExtractorEndToEnd(unittest.TestCase):
    """End-to-end tests for Linux extractor with both skills and commands."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.get_linux_user_homes')
    def test_user_level_skills_and_commands(self, mock_get_homes):
        """Extractor must return both user-level skills and commands."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create user-level skill
        skill_dir = fake_home / ".claude" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        # Create user-level command
        commands_dir = fake_home / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        extractor = LinuxClaudeSkillsExtractor()
        # Only test user-level extraction (project-level walks the whole FS)
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 2)
        types = {s["type"] for s in user_skills}
        self.assertEqual(types, {"skill", "command"})
        names = {s["skill_name"] for s in user_skills}
        self.assertEqual(names, {"my-skill", "deploy"})
        # project_root should be stripped for user-level items
        for item in user_skills:
            self.assertNotIn("project_root", item)

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.get_linux_user_homes')
    def test_user_level_commands_only_no_skills_dir(self, mock_get_homes):
        """Extractor must find commands even when ~/.claude/skills/ does not exist."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Only commands, no skills directory
        commands_dir = fake_home / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "greet.md").write_text("# Greet")

        extractor = LinuxClaudeSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "command")
        self.assertEqual(user_skills[0]["skill_name"], "greet")

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find commands in project .claude/commands/."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "build.md").write_text("# Build")

        skill_dir = project / ".claude" / "skills" / "lint"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Lint")

        extractor = LinuxClaudeSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 2)
        types = {i["type"] for i in items}
        self.assertEqual(types, {"skill", "command"})
        names = {i["skill_name"] for i in items}
        self.assertEqual(names, {"lint", "build"})

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_commands_without_skills_dir(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must detect .claude/commands/ even without .claude/skills/."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        extractor = LinuxClaudeSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "command")
        self.assertEqual(items[0]["skill_name"], "deploy")


class TestLinuxExtractorAgents(unittest.TestCase):
    """End-to-end tests for Linux extractor with agents."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.get_linux_user_homes')
    def test_user_level_agents(self, mock_get_homes):
        """Extractor must return user-level agents."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create user-level agent
        agents_dir = fake_home / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text("# Reviewer")

        extractor = LinuxClaudeSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "agent")
        self.assertEqual(user_skills[0]["skill_name"], "reviewer")
        self.assertNotIn("project_root", user_skills[0])

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.get_linux_user_homes')
    def test_user_level_all_three_types(self, mock_get_homes):
        """Extractor must return skills, commands, and agents from user level."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_get_homes.return_value = [fake_home]

        # Create one of each
        skill_dir = fake_home / ".claude" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")

        commands_dir = fake_home / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        agents_dir = fake_home / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "reviewer.md").write_text("# Reviewer")

        extractor = LinuxClaudeSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 3)
        types = {s["type"] for s in user_skills}
        self.assertEqual(types, {"skill", "command", "agent"})

    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.linux.claude_code.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_agents(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find agents in project .claude/agents/."""
        from scripts.coding_discovery_tools.linux.claude_code.skills_extractor import LinuxClaudeSkillsExtractor

        project = self.temp_path / "project"
        agents_dir = project / ".claude" / "agents"
        agents_dir.mkdir(parents=True)
        (agents_dir / "ci.md").write_text("# CI Agent")

        extractor = LinuxClaudeSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "agent")
        self.assertEqual(items[0]["skill_name"], "ci")


if __name__ == "__main__":
    unittest.main()
