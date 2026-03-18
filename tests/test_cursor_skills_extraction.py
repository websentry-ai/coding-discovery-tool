"""
Unit tests for Cursor skills extraction.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.coding_discovery_tools.cursor_skills_helpers import (
    CURSOR_DIR_NAME,
    SKILLS_DIR_NAME,
    SKILL_FILE_NAME,
    COMMANDS_DIR_NAME,
    find_cursor_skill_project_root,
    find_cursor_command_project_root,
    extract_cursor_skill_info,
    extract_cursor_command_info,
    extract_cursor_skills_from_directory,
    extract_cursor_commands_from_directory,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    is_skill_md_file,
    is_command_md_file,
    build_skills_project_list,
    add_skill_to_project,
    is_user_level_skills_dir,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import (
    extract_single_rule_file,
)


class TestCursorConstants(unittest.TestCase):
    """Tests for Cursor skills constants."""

    def test_cursor_dir_name(self):
        self.assertEqual(CURSOR_DIR_NAME, ".cursor")

    def test_skills_dir_name(self):
        self.assertEqual(SKILLS_DIR_NAME, "skills")

    def test_skill_file_name(self):
        self.assertEqual(SKILL_FILE_NAME, "SKILL.md")


class TestFindCursorSkillProjectRoot(unittest.TestCase):
    """Tests for find_cursor_skill_project_root function."""

    def test_standard_project_structure(self):
        # /Users/test/myproject/.cursor/skills/commit/SKILL.md
        skill_file = Path("/Users/test/myproject/.cursor/skills/commit/SKILL.md")
        result = find_cursor_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_user_level_skill(self):
        # /Users/test/.cursor/skills/global-skill/SKILL.md
        skill_file = Path("/Users/test/.cursor/skills/global-skill/SKILL.md")
        result = find_cursor_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test"))

    def test_nested_project(self):
        # /Users/test/work/repos/myproject/.cursor/skills/deploy/SKILL.md
        skill_file = Path("/Users/test/work/repos/myproject/.cursor/skills/deploy/SKILL.md")
        result = find_cursor_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test/work/repos/myproject"))

    def test_windows_style_path(self):
        # Test with Windows-style path (works on any OS since Path normalizes)
        skill_file = Path("C:/Users/test/project/.cursor/skills/commit/SKILL.md")
        result = find_cursor_skill_project_root(skill_file)
        expected = Path("C:/Users/test/project")
        self.assertEqual(result, expected)


class TestCursorSkillsExtractionIntegration(unittest.TestCase):
    """Integration tests for Cursor skills extraction with real filesystem."""

    def setUp(self):
        """Create a temporary directory structure for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, skill_name: str, content: str = "# Test Skill"):
        """Helper to create a Cursor skill directory with SKILL.md."""
        skill_dir = base_path / ".cursor" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_create_and_find_skill(self):
        """Test creating a Cursor skill and finding its project root."""
        project_path = self.temp_path / "myproject"
        project_path.mkdir()

        skill_file = self._create_skill(project_path, "my-skill", "# My Skill\nDoes things")

        # Verify file was created
        self.assertTrue(skill_file.exists())
        self.assertTrue(is_skill_md_file(skill_file.name))

        # Verify project root detection
        project_root = find_cursor_skill_project_root(skill_file)
        self.assertEqual(project_root, project_path)

    def test_multiple_skills_in_project(self):
        """Test extracting multiple skills from a Cursor project."""
        project_path = self.temp_path / "project"
        project_path.mkdir()

        self._create_skill(project_path, "skill-a", "# Skill A")
        self._create_skill(project_path, "skill-b", "# Skill B")
        self._create_skill(project_path, "skill-c", "# Skill C")

        skills_dir = project_path / ".cursor" / "skills"
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 3)

    def test_empty_skills_directory(self):
        """Test handling of empty skills directory."""
        project_path = self.temp_path / "project"
        skills_dir = project_path / ".cursor" / "skills"
        skills_dir.mkdir(parents=True)

        # Empty skills directory should have no skill subdirectories
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 0)

    def test_skill_directory_without_skill_md(self):
        """Test skill directory that's missing SKILL.md file."""
        project_path = self.temp_path / "project"
        skill_dir = project_path / ".cursor" / "skills" / "incomplete-skill"
        skill_dir.mkdir(parents=True)

        # Create a different file instead of SKILL.md
        (skill_dir / "README.md").write_text("Not a skill file")

        # Directory exists but has no SKILL.md
        self.assertTrue(skill_dir.exists())
        skill_files = [f for f in skill_dir.iterdir() if is_skill_md_file(f.name)]
        self.assertEqual(len(skill_files), 0)


class TestExtractCursorSkillInfo(unittest.TestCase):
    """Tests for extract_cursor_skill_info output fields."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, name: str, content: str = "# Test"):
        skill_dir = base_path / ".cursor" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_type_is_skill(self):
        """extract_cursor_skill_info must set type to 'skill'."""
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, "deploy")
        result = extract_cursor_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "skill")

    def test_skill_name_from_directory(self):
        """skill_name must be the directory name, not the filename."""
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, "my-tool", "# Tool")
        result = extract_cursor_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["skill_name"], "my-tool")
        self.assertEqual(result["file_name"], "SKILL.md")

    def test_project_root_detected(self):
        """project_root must point to the directory containing .cursor."""
        project = self.temp_path / "myproject"
        skill_file = self._create_skill(project, "build")
        result = extract_cursor_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["project_root"], str(project))

    def test_content_preserved(self):
        """File content must be included in the result."""
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, "lint", "# Lint\nRun the linter")
        result = extract_cursor_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertIn("Lint", result["content"])
        self.assertIn("Run the linter", result["content"])

    def test_scope_passed_through(self):
        """Scope argument must be reflected in the result."""
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, "cmd")
        result = extract_cursor_skill_info(skill_file, extract_single_rule_file, scope="user")
        self.assertEqual(result["scope"], "user")

    def test_nonexistent_file_returns_none(self):
        """Non-existent skill file must return None."""
        fake = self.temp_path / ".cursor" / "skills" / "ghost" / "SKILL.md"
        result = extract_cursor_skill_info(fake, extract_single_rule_file, scope="project")
        self.assertIsNone(result)


class TestExtractCursorSkillsFromDirectory(unittest.TestCase):
    """Tests for extract_cursor_skills_from_directory populating projects_by_root."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, name: str, content: str = "# Test"):
        skill_dir = base_path / ".cursor" / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_populates_projects_dict(self):
        """Skills from a directory must appear in projects_by_root."""
        project = self.temp_path / "proj"
        self._create_skill(project, "deploy", "# Deploy")
        self._create_skill(project, "test", "# Test")

        skills_dir = project / ".cursor" / "skills"
        projects_by_root = {}
        extract_cursor_skills_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 1)
        project_root = str(project)
        self.assertIn(project_root, projects_by_root)
        skills = projects_by_root[project_root]
        self.assertEqual(len(skills), 2)
        names = {s["skill_name"] for s in skills}
        self.assertEqual(names, {"deploy", "test"})
        for skill in skills:
            self.assertEqual(skill["type"], "skill")
            self.assertNotIn("project_root", skill)

    def test_empty_skills_dir(self):
        """Empty skills directory must produce no entries."""
        project = self.temp_path / "proj"
        skills_dir = project / ".cursor" / "skills"
        skills_dir.mkdir(parents=True)

        projects_by_root = {}
        extract_cursor_skills_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 0)

    def test_non_skill_dirs_ignored(self):
        """Directories without SKILL.md are ignored."""
        project = self.temp_path / "proj"
        self._create_skill(project, "valid", "# Valid")

        # Create a directory without SKILL.md
        no_skill = project / ".cursor" / "skills" / "no-skill"
        no_skill.mkdir(parents=True)
        (no_skill / "README.md").write_text("Not a skill")

        skills_dir = project / ".cursor" / "skills"
        projects_by_root = {}
        extract_cursor_skills_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "valid")


class TestCursorSkillThreadSafety(unittest.TestCase):
    """Tests for thread-safe operations with Cursor skills."""

    def test_concurrent_add_skill_to_project(self):
        """Test that concurrent additions don't lose data."""
        import threading

        projects = {}
        lock = threading.Lock()

        def add_with_lock(skill_name, project_root):
            with lock:
                add_skill_to_project(
                    {"skill_name": skill_name, "project_root": project_root},
                    project_root,
                    projects
                )

        threads = []
        # Add 100 skills to the same project concurrently
        for i in range(100):
            t = threading.Thread(target=add_with_lock, args=(f"skill-{i}", "/test/project"))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All 100 skills should be present
        self.assertEqual(len(projects["/test/project"]), 100)


class TestCursorVsClaudeSkillPaths(unittest.TestCase):
    """Tests to verify Cursor skill paths use .cursor (not .claude)."""

    def test_cursor_path_structure(self):
        """Verify find_cursor_skill_project_root handles .cursor directory."""
        # Cursor uses .cursor, not .claude
        cursor_skill = Path("/Users/test/project/.cursor/skills/my-skill/SKILL.md")
        result = find_cursor_skill_project_root(cursor_skill)
        self.assertEqual(result, Path("/Users/test/project"))

    def test_wrong_directory_falls_back(self):
        """If the directory is not .cursor, fallback logic is used."""
        # .claude directory should NOT be recognized by Cursor root finder
        claude_skill = Path("/Users/test/project/.claude/skills/my-skill/SKILL.md")
        result = find_cursor_skill_project_root(claude_skill)
        # Should NOT return /Users/test/project because it expects .cursor
        self.assertNotEqual(result, Path("/Users/test/project"))


class TestMacOSCursorSkillsExtractor(unittest.TestCase):
    """Tests for macOS Cursor skills extractor."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_skills(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor must return user-level skills."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        # Create user-level skill
        skill_dir = fake_home / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        extractor = MacOSCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "skill")
        self.assertEqual(user_skills[0]["skill_name"], "my-skill")
        # project_root should be stripped for user-level items
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_no_skills_dir_returns_empty(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor returns empty list when no .cursor/skills/ directory exists."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        extractor = MacOSCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 0)

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_skills(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find skills in project .cursor/skills/."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".cursor" / "skills" / "lint"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Lint")

        extractor = MacOSCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "skill")
        self.assertEqual(items[0]["skill_name"], "lint")

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_multiple_skills(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find multiple skills in a project."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        project = self.temp_path / "project"

        for name in ("skill-a", "skill-b", "skill-c"):
            skill_dir = project / ".cursor" / "skills" / name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {name}")

        extractor = MacOSCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 3)
        names = {i["skill_name"] for i in items}
        self.assertEqual(names, {"skill-a", "skill-b", "skill-c"})


class TestCursorSkillsUserLevel(unittest.TestCase):
    """Tests for is_user_level_skills_dir with .cursor paths."""

    def test_user_level_cursor_skills(self):
        # is_user_level_skills_dir checks parent of parent (the dir containing .cursor/skills)
        # For user level: /Users/john/.cursor/skills -> parent is .cursor, parent.parent is /Users/john
        skills_dir = Path("/Users/john/.cursor/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir, "/Users"))

    def test_project_level_cursor_skills(self):
        skills_dir = Path("/Users/john/project/.cursor/skills")
        self.assertFalse(is_user_level_skills_dir(skills_dir, "/Users"))

    def test_windows_user_level_cursor_skills(self):
        skills_dir = Path("C:/Users/john/.cursor/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir, "C:/Users"))

    def test_windows_project_level_cursor_skills(self):
        skills_dir = Path("C:/Users/john/projects/app/.cursor/skills")
        self.assertFalse(is_user_level_skills_dir(skills_dir, "C:/Users"))


class TestFindCursorCommandProjectRoot(unittest.TestCase):
    """Tests for find_cursor_command_project_root function."""

    def test_standard_project_structure(self):
        # /Users/test/myproject/.cursor/commands/code-review.md
        command_file = Path("/Users/test/myproject/.cursor/commands/code-review.md")
        result = find_cursor_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_user_level_command(self):
        # /Users/test/.cursor/commands/lint.md
        command_file = Path("/Users/test/.cursor/commands/lint.md")
        result = find_cursor_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test"))

    def test_nested_project(self):
        # /Users/test/work/repos/app/.cursor/commands/deploy.md
        command_file = Path("/Users/test/work/repos/app/.cursor/commands/deploy.md")
        result = find_cursor_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test/work/repos/app"))

    def test_wrong_directory_falls_back(self):
        # .claude directory should NOT be recognized by Cursor command root finder
        command_file = Path("/Users/test/project/.claude/commands/test.md")
        result = find_cursor_command_project_root(command_file)
        # Should NOT return /Users/test/project because it expects .cursor
        self.assertNotEqual(result, Path("/Users/test/project"))


class TestExtractCursorCommandInfo(unittest.TestCase):
    """Tests for extract_cursor_command_info output fields."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_command(self, base_path: Path, name: str, content: str = "# Test"):
        commands_dir = base_path / ".cursor" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        command_file = commands_dir / f"{name}.md"
        command_file.write_text(content)
        return command_file

    def test_type_is_command(self):
        """extract_cursor_command_info must set type to 'command'."""
        project = self.temp_path / "proj"
        command_file = self._create_command(project, "code-review")
        result = extract_cursor_command_info(command_file, extract_single_rule_file, scope="project")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "command")

    def test_skill_name_from_stem(self):
        """skill_name must be the filename stem, not the full filename."""
        project = self.temp_path / "proj"
        command_file = self._create_command(project, "deploy-prod", "# Deploy")
        result = extract_cursor_command_info(command_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["skill_name"], "deploy-prod")
        self.assertEqual(result["file_name"], "deploy-prod.md")

    def test_project_root_detected(self):
        """project_root must point to the directory containing .cursor."""
        project = self.temp_path / "myproject"
        command_file = self._create_command(project, "lint")
        result = extract_cursor_command_info(command_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["project_root"], str(project))

    def test_content_preserved(self):
        """File content must be included in the result."""
        project = self.temp_path / "proj"
        command_file = self._create_command(project, "review", "# Review\nCheck for bugs")
        result = extract_cursor_command_info(command_file, extract_single_rule_file, scope="project")
        self.assertIn("Review", result["content"])
        self.assertIn("Check for bugs", result["content"])

    def test_scope_passed_through(self):
        """Scope argument must be reflected in the result."""
        project = self.temp_path / "proj"
        command_file = self._create_command(project, "fmt")
        result = extract_cursor_command_info(command_file, extract_single_rule_file, scope="user")
        self.assertEqual(result["scope"], "user")

    def test_nonexistent_file_returns_none(self):
        """Non-existent command file must return None."""
        fake = self.temp_path / ".cursor" / "commands" / "ghost.md"
        result = extract_cursor_command_info(fake, extract_single_rule_file, scope="project")
        self.assertIsNone(result)


class TestExtractCursorCommandsFromDirectory(unittest.TestCase):
    """Tests for extract_cursor_commands_from_directory populating projects_by_root."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_command(self, base_path: Path, name: str, content: str = "# Test"):
        commands_dir = base_path / ".cursor" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        command_file = commands_dir / f"{name}.md"
        command_file.write_text(content)
        return command_file

    def test_populates_projects_dict(self):
        """Commands from a directory must appear in projects_by_root."""
        project = self.temp_path / "proj"
        self._create_command(project, "review", "# Review")
        self._create_command(project, "deploy", "# Deploy")

        commands_dir = project / ".cursor" / "commands"
        projects_by_root = {}
        extract_cursor_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 1)
        project_root = str(project)
        self.assertIn(project_root, projects_by_root)
        commands = projects_by_root[project_root]
        self.assertEqual(len(commands), 2)
        names = {c["skill_name"] for c in commands}
        self.assertEqual(names, {"review", "deploy"})
        for cmd in commands:
            self.assertEqual(cmd["type"], "command")
            self.assertNotIn("project_root", cmd)

    def test_empty_commands_dir(self):
        """Empty commands directory must produce no entries."""
        project = self.temp_path / "proj"
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)

        projects_by_root = {}
        extract_cursor_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 0)

    def test_ignores_non_md_files(self):
        """Non-.md files and hidden files must be ignored."""
        project = self.temp_path / "proj"
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)

        # Valid command
        (commands_dir / "valid.md").write_text("# Valid")
        # Non-md file
        (commands_dir / "notes.txt").write_text("not a command")
        # Hidden md file
        (commands_dir / ".hidden.md").write_text("hidden")

        projects_by_root = {}
        extract_cursor_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        commands = projects_by_root[str(project)]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["skill_name"], "valid")

    def test_ignores_subdirectories(self):
        """Subdirectories inside commands/ must be ignored (commands are flat .md files)."""
        project = self.temp_path / "proj"
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)

        # Valid flat command
        (commands_dir / "review.md").write_text("# Review")
        # Subdirectory with an .md file — should be ignored
        nested = commands_dir / "nested-dir"
        nested.mkdir()
        (nested / "should-ignore.md").write_text("# Ignored")

        projects_by_root = {}
        extract_cursor_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        commands = projects_by_root[str(project)]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["skill_name"], "review")


class TestMacOSCursorSkillsExtractorWithCommands(unittest.TestCase):
    """Tests for macOS Cursor skills extractor with commands support."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_commands(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor must return user-level commands."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        # Create user-level command
        commands_dir = fake_home / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "code-review.md").write_text("# Code Review")

        extractor = MacOSCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "command")
        self.assertEqual(user_skills[0]["skill_name"], "code-review")
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_skills_and_commands_coexist(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor must return both skills and commands from user level."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        # Create user-level skill
        skill_dir = fake_home / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        # Create user-level command
        commands_dir = fake_home / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "my-command.md").write_text("# My Command")

        extractor = MacOSCursorSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 2)
        types = {s["type"] for s in user_skills}
        self.assertEqual(types, {"skill", "command"})

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find commands in project .cursor/commands/."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "lint.md").write_text("# Lint")

        extractor = MacOSCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "command")
        self.assertEqual(items[0]["skill_name"], "lint")

    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cursor.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_skills_and_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find both skills and commands in a project."""
        from scripts.coding_discovery_tools.macos.cursor.skills_extractor import MacOSCursorSkillsExtractor

        project = self.temp_path / "project"

        # Create a skill
        skill_dir = project / ".cursor" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")

        # Create a command
        commands_dir = project / ".cursor" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "my-command.md").write_text("# Command")

        extractor = MacOSCursorSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 2)
        types = {i["type"] for i in items}
        self.assertEqual(types, {"skill", "command"})


class TestCursorCommandsUserLevel(unittest.TestCase):
    """Tests for is_user_level_skills_dir with commands directory paths."""

    def test_user_level_cursor_commands(self):
        commands_dir = Path("/Users/john/.cursor/commands")
        self.assertTrue(is_user_level_skills_dir(commands_dir, "/Users"))

    def test_project_level_cursor_commands(self):
        commands_dir = Path("/Users/john/project/.cursor/commands")
        self.assertFalse(is_user_level_skills_dir(commands_dir, "/Users"))

    def test_windows_user_level_cursor_commands(self):
        commands_dir = Path("C:/Users/john/.cursor/commands")
        self.assertTrue(is_user_level_skills_dir(commands_dir, "C:/Users"))

    def test_windows_project_level_cursor_commands(self):
        commands_dir = Path("C:/Users/john/projects/app/.cursor/commands")
        self.assertFalse(is_user_level_skills_dir(commands_dir, "C:/Users"))


if __name__ == "__main__":
    unittest.main()
