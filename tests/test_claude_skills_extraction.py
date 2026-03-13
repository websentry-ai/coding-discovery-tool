"""
Unit tests for Claude Code skills extraction.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    CLAUDE_DIR_NAME,
    SKILLS_DIR_NAME,
    SKILL_FILE_NAME,
    COMMANDS_DIR_NAME,
    is_skill_md_file,
    is_command_md_file,
    build_skills_project_list,
    find_skill_project_root,
    find_command_project_root,
    extract_skill_info,
    extract_command_info,
    extract_commands_from_directory,
    extract_skills_from_directory,
    add_skill_to_project,
    is_user_level_skills_dir,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import (
    extract_single_rule_file,
)


class TestIsSkillMdFile(unittest.TestCase):
    """Tests for is_skill_md_file function."""

    def test_exact_match(self):
        self.assertTrue(is_skill_md_file("SKILL.md"))

    def test_lowercase(self):
        self.assertTrue(is_skill_md_file("skill.md"))

    def test_mixed_case(self):
        self.assertTrue(is_skill_md_file("Skill.md"))
        self.assertTrue(is_skill_md_file("SKILL.MD"))
        self.assertTrue(is_skill_md_file("sKiLl.Md"))

    def test_non_matching(self):
        self.assertFalse(is_skill_md_file("README.md"))
        self.assertFalse(is_skill_md_file("CLAUDE.md"))
        self.assertFalse(is_skill_md_file("skill.txt"))
        self.assertFalse(is_skill_md_file("skill"))


class TestBuildSkillsProjectList(unittest.TestCase):
    """Tests for build_skills_project_list function."""

    def test_empty_dict(self):
        result = build_skills_project_list({})
        self.assertEqual(result, [])

    def test_single_project(self):
        projects_by_root = {
            "/Users/test/project1": [
                {"skill_name": "commit", "content": "..."}
            ]
        }
        result = build_skills_project_list(projects_by_root)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["project_root"], "/Users/test/project1")
        self.assertEqual(len(result[0]["skills"]), 1)

    def test_multiple_projects(self):
        projects_by_root = {
            "/project1": [{"skill_name": "a"}],
            "/project2": [{"skill_name": "b"}, {"skill_name": "c"}],
        }
        result = build_skills_project_list(projects_by_root)
        self.assertEqual(len(result), 2)


class TestFindSkillProjectRoot(unittest.TestCase):
    """Tests for find_skill_project_root function."""

    def test_standard_project_structure(self):
        # /Users/test/myproject/.claude/skills/commit/SKILL.md
        skill_file = Path("/Users/test/myproject/.claude/skills/commit/SKILL.md")
        result = find_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_user_level_skill(self):
        # /Users/test/.claude/skills/global-skill/SKILL.md
        skill_file = Path("/Users/test/.claude/skills/global-skill/SKILL.md")
        result = find_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test"))

    def test_nested_project(self):
        # /Users/test/work/repos/myproject/.claude/skills/deploy/SKILL.md
        skill_file = Path("/Users/test/work/repos/myproject/.claude/skills/deploy/SKILL.md")
        result = find_skill_project_root(skill_file)
        self.assertEqual(result, Path("/Users/test/work/repos/myproject"))

    def test_windows_style_path(self):
        # Test with Windows-style path (works on any OS since Path normalizes)
        skill_file = Path("C:/Users/test/project/.claude/skills/commit/SKILL.md")
        result = find_skill_project_root(skill_file)
        expected = Path("C:/Users/test/project")
        self.assertEqual(result, expected)


class TestAddSkillToProject(unittest.TestCase):
    """Tests for add_skill_to_project function."""

    def test_add_to_empty_dict(self):
        projects = {}
        skill_info = {
            "skill_name": "commit",
            "project_root": "/test/project",
            "content": "skill content"
        }
        add_skill_to_project(skill_info, "/test/project", projects)

        self.assertIn("/test/project", projects)
        self.assertEqual(len(projects["/test/project"]), 1)
        # project_root should be renamed to project_path
        self.assertNotIn("project_root", projects["/test/project"][0])
        self.assertEqual(projects["/test/project"][0]["project_path"], "/test/project")
        self.assertEqual(projects["/test/project"][0]["skill_name"], "commit")

    def test_add_to_existing_project(self):
        projects = {
            "/test/project": [{"skill_name": "existing"}]
        }
        skill_info = {
            "skill_name": "new",
            "project_root": "/test/project",
        }
        add_skill_to_project(skill_info, "/test/project", projects)

        self.assertEqual(len(projects["/test/project"]), 2)

    def test_add_to_multiple_projects(self):
        projects = {}
        add_skill_to_project({"skill_name": "a", "project_root": "/p1"}, "/p1", projects)
        add_skill_to_project({"skill_name": "b", "project_root": "/p2"}, "/p2", projects)
        add_skill_to_project({"skill_name": "c", "project_root": "/p1"}, "/p1", projects)

        self.assertEqual(len(projects), 2)
        self.assertEqual(len(projects["/p1"]), 2)
        self.assertEqual(len(projects["/p2"]), 1)


class TestIsUserLevelSkillsDir(unittest.TestCase):
    """Tests for is_user_level_skills_dir function."""

    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_current_user_home(self, mock_home):
        mock_home.return_value = Path("/Users/testuser")

        # User-level: /Users/testuser/.claude/skills
        skills_dir = Path("/Users/testuser/.claude/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir))

    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_project_level(self, mock_home):
        mock_home.return_value = Path("/Users/testuser")

        # Project-level: /Users/testuser/projects/myapp/.claude/skills
        skills_dir = Path("/Users/testuser/projects/myapp/.claude/skills")
        self.assertFalse(is_user_level_skills_dir(skills_dir))

    def test_with_explicit_users_root(self):
        # Test macOS-style path
        skills_dir = Path("/Users/john/.claude/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir, "/Users"))

        # Test project-level
        skills_dir = Path("/Users/john/project/.claude/skills")
        self.assertFalse(is_user_level_skills_dir(skills_dir, "/Users"))

    def test_with_windows_users_root(self):
        # Test Windows-style user path
        skills_dir = Path("C:/Users/john/.claude/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir, "C:/Users"))

        # Test project-level
        skills_dir = Path("C:/Users/john/projects/app/.claude/skills")
        self.assertFalse(is_user_level_skills_dir(skills_dir, "C:/Users"))

    def test_alternate_drive(self):
        # Test with D: drive
        skills_dir = Path("D:/Users/john/.claude/skills")
        self.assertTrue(is_user_level_skills_dir(skills_dir, "D:/Users"))


class TestSkillsExtractionIntegration(unittest.TestCase):
    """Integration tests for skills extraction with real filesystem."""

    def setUp(self):
        """Create a temporary directory structure for testing."""
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        """Clean up temporary directory."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, skill_name: str, content: str = "# Test Skill"):
        """Helper to create a skill directory with SKILL.md."""
        skill_dir = base_path / ".claude" / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_create_and_find_skill(self):
        """Test creating a skill and finding its project root."""
        project_path = self.temp_path / "myproject"
        project_path.mkdir()

        skill_file = self._create_skill(project_path, "my-skill", "# My Skill\nDoes things")

        # Verify file was created
        self.assertTrue(skill_file.exists())
        self.assertTrue(is_skill_md_file(skill_file.name))

        # Verify project root detection
        project_root = find_skill_project_root(skill_file)
        self.assertEqual(project_root, project_path)

    def test_multiple_skills_in_project(self):
        """Test extracting multiple skills from a project."""
        project_path = self.temp_path / "project"
        project_path.mkdir()

        self._create_skill(project_path, "skill-a", "# Skill A")
        self._create_skill(project_path, "skill-b", "# Skill B")
        self._create_skill(project_path, "skill-c", "# Skill C")

        skills_dir = project_path / ".claude" / "skills"
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 3)

    def test_empty_skills_directory(self):
        """Test handling of empty skills directory."""
        project_path = self.temp_path / "project"
        skills_dir = project_path / ".claude" / "skills"
        skills_dir.mkdir(parents=True)

        # Empty skills directory should have no skill subdirectories
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 0)

    def test_skill_directory_without_skill_md(self):
        """Test skill directory that's missing SKILL.md file."""
        project_path = self.temp_path / "project"
        skill_dir = project_path / ".claude" / "skills" / "incomplete-skill"
        skill_dir.mkdir(parents=True)

        # Create a different file instead of SKILL.md
        (skill_dir / "README.md").write_text("Not a skill file")

        # Directory exists but has no SKILL.md
        self.assertTrue(skill_dir.exists())
        skill_files = [f for f in skill_dir.iterdir() if is_skill_md_file(f.name)]
        self.assertEqual(len(skill_files), 0)


class TestThreadSafety(unittest.TestCase):
    """Tests for thread-safe operations."""

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


class TestIsCommandMdFile(unittest.TestCase):
    """Tests for is_command_md_file function."""

    def test_standard_md_file(self):
        self.assertTrue(is_command_md_file("deploy.md"))

    def test_uppercase_extension(self):
        self.assertTrue(is_command_md_file("deploy.MD"))

    def test_mixed_case_extension(self):
        self.assertTrue(is_command_md_file("deploy.Md"))

    def test_complex_name(self):
        self.assertTrue(is_command_md_file("review-pr.md"))
        self.assertTrue(is_command_md_file("my_command.md"))

    def test_hidden_files_excluded(self):
        self.assertFalse(is_command_md_file(".hidden.md"))
        self.assertFalse(is_command_md_file(".DS_Store.md"))

    def test_non_md_files_rejected(self):
        self.assertFalse(is_command_md_file("deploy.txt"))
        self.assertFalse(is_command_md_file("deploy.py"))
        self.assertFalse(is_command_md_file("deploy"))
        self.assertFalse(is_command_md_file("README"))


class TestFindCommandProjectRoot(unittest.TestCase):
    """Tests for find_command_project_root function."""

    def test_standard_project(self):
        command_file = Path("/Users/test/myproject/.claude/commands/deploy.md")
        result = find_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_user_level_command(self):
        command_file = Path("/Users/test/.claude/commands/cmd.md")
        result = find_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test"))

    def test_nested_project_path(self):
        command_file = Path("/Users/test/work/repos/myproject/.claude/commands/deploy.md")
        result = find_command_project_root(command_file)
        self.assertEqual(result, Path("/Users/test/work/repos/myproject"))

    def test_windows_style_path(self):
        command_file = Path("C:/Users/test/project/.claude/commands/deploy.md")
        result = find_command_project_root(command_file)
        self.assertEqual(result, Path("C:/Users/test/project"))


class TestCommandsExtractionIntegration(unittest.TestCase):
    """Integration tests for commands extraction with real filesystem."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_command(self, base_path: Path, command_name: str, content: str = "# Test Command"):
        """Helper to create a command .md file."""
        commands_dir = base_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        command_file = commands_dir / f"{command_name}.md"
        command_file.write_text(content)
        return command_file

    def test_create_and_find_command(self):
        """Test creating a command and finding its project root."""
        project_path = self.temp_path / "myproject"
        project_path.mkdir()

        command_file = self._create_command(project_path, "deploy", "# Deploy\nDeploys the app")

        self.assertTrue(command_file.exists())
        self.assertTrue(is_command_md_file(command_file.name))

        project_root = find_command_project_root(command_file)
        self.assertEqual(project_root, project_path)

    def test_multiple_commands_in_project(self):
        """Test extracting multiple commands from a project."""
        project_path = self.temp_path / "project"
        project_path.mkdir()

        self._create_command(project_path, "deploy", "# Deploy")
        self._create_command(project_path, "test", "# Test")
        self._create_command(project_path, "lint", "# Lint")

        commands_dir = project_path / ".claude" / "commands"
        command_count = sum(1 for f in commands_dir.iterdir() if f.is_file() and is_command_md_file(f.name))
        self.assertEqual(command_count, 3)

    def test_non_md_files_ignored(self):
        """Test that non-.md files in commands dir are ignored."""
        project_path = self.temp_path / "project"
        commands_dir = project_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)

        (commands_dir / "deploy.md").write_text("# Deploy")
        (commands_dir / "notes.txt").write_text("some notes")
        (commands_dir / "script.py").write_text("print('hi')")

        command_count = sum(1 for f in commands_dir.iterdir() if f.is_file() and is_command_md_file(f.name))
        self.assertEqual(command_count, 1)

    def test_subdirectories_inside_commands_ignored(self):
        """Test that subdirectories inside commands/ are ignored (only flat files)."""
        project_path = self.temp_path / "project"
        commands_dir = project_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)

        (commands_dir / "deploy.md").write_text("# Deploy")
        subdir = commands_dir / "nested"
        subdir.mkdir()
        (subdir / "inner.md").write_text("# Inner")

        # Only flat files should be counted
        command_count = sum(1 for f in commands_dir.iterdir() if f.is_file() and is_command_md_file(f.name))
        self.assertEqual(command_count, 1)

    def test_hidden_md_files_excluded(self):
        """Test that hidden .md files are excluded."""
        project_path = self.temp_path / "project"
        commands_dir = project_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)

        (commands_dir / "deploy.md").write_text("# Deploy")
        (commands_dir / ".hidden.md").write_text("# Hidden")

        command_count = sum(1 for f in commands_dir.iterdir() if f.is_file() and is_command_md_file(f.name))
        self.assertEqual(command_count, 1)


class TestMixedSkillsAndCommands(unittest.TestCase):
    """Tests for projects with both skills and commands."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_project_with_both_skills_and_commands(self):
        """Test project with both .claude/skills/ and .claude/commands/."""
        project_path = self.temp_path / "project"

        # Create a skill
        skill_dir = project_path / ".claude" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        # Create a command
        commands_dir = project_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        # Verify both exist
        self.assertTrue((project_path / ".claude" / "skills").exists())
        self.assertTrue((project_path / ".claude" / "commands").exists())

        skills_count = sum(1 for d in (project_path / ".claude" / "skills").iterdir() if d.is_dir())
        commands_count = sum(
            1 for f in (project_path / ".claude" / "commands").iterdir()
            if f.is_file() and is_command_md_file(f.name)
        )
        self.assertEqual(skills_count, 1)
        self.assertEqual(commands_count, 1)

    def test_project_with_only_commands(self):
        """Test project with commands but no skills directory."""
        project_path = self.temp_path / "project"
        commands_dir = project_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        # skills/ directory should not exist
        self.assertFalse((project_path / ".claude" / "skills").exists())
        self.assertTrue((project_path / ".claude" / "commands").exists())

        commands_count = sum(
            1 for f in commands_dir.iterdir()
            if f.is_file() and is_command_md_file(f.name)
        )
        self.assertEqual(commands_count, 1)


class TestCommandThreadSafety(unittest.TestCase):
    """Tests for thread-safe operations with both skills and commands."""

    def test_concurrent_add_skills_and_commands(self):
        """Test that concurrent additions of both skills and commands don't lose data."""
        import threading

        projects = {}
        lock = threading.Lock()

        def add_with_lock(item_info, project_root):
            with lock:
                add_skill_to_project(item_info, project_root, projects)

        threads = []
        for i in range(50):
            skill_thread = threading.Thread(
                target=add_with_lock,
                args=({"skill_name": f"skill-{i}", "type": "skill", "project_root": "/test/project"}, "/test/project")
            )
            command_thread = threading.Thread(
                target=add_with_lock,
                args=({"skill_name": f"cmd-{i}", "type": "command", "project_root": "/test/project"}, "/test/project")
            )
            threads.extend([skill_thread, command_thread])

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(projects["/test/project"]), 100)
        types = {item["type"] for item in projects["/test/project"]}
        self.assertEqual(types, {"skill", "command"})


class TestExtractCommandInfo(unittest.TestCase):
    """Tests for extract_command_info output fields."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_command(self, base_path: Path, name: str, content: str = "# Test"):
        commands_dir = base_path / ".claude" / "commands"
        commands_dir.mkdir(parents=True, exist_ok=True)
        cmd_file = commands_dir / f"{name}.md"
        cmd_file.write_text(content)
        return cmd_file

    def test_type_is_command(self):
        """extract_command_info must set type to 'command'."""
        cmd_file = self._create_command(self.temp_path / "proj", "deploy")
        result = extract_command_info(cmd_file, extract_single_rule_file, scope="project")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "command")

    def test_skill_name_from_stem(self):
        """skill_name must be the filename stem, not the full filename."""
        cmd_file = self._create_command(self.temp_path / "proj", "review-pr", "# Review PR")
        result = extract_command_info(cmd_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["skill_name"], "review-pr")
        self.assertEqual(result["file_name"], "review-pr.md")

    def test_project_root_detected(self):
        """project_root must point to the directory containing .claude."""
        project = self.temp_path / "myproject"
        cmd_file = self._create_command(project, "build")
        result = extract_command_info(cmd_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["project_root"], str(project))

    def test_content_preserved(self):
        """File content must be included in the result."""
        cmd_file = self._create_command(self.temp_path / "proj", "lint", "# Lint\nRun the linter")
        result = extract_command_info(cmd_file, extract_single_rule_file, scope="project")
        self.assertIn("Lint", result["content"])
        self.assertIn("Run the linter", result["content"])

    def test_scope_passed_through(self):
        """Scope argument must be reflected in the result."""
        cmd_file = self._create_command(self.temp_path / "proj", "cmd")
        result = extract_command_info(cmd_file, extract_single_rule_file, scope="user")
        self.assertEqual(result["scope"], "user")

    def test_nonexistent_file_returns_none(self):
        """Non-existent command file must return None."""
        fake = self.temp_path / ".claude" / "commands" / "ghost.md"
        result = extract_command_info(fake, extract_single_rule_file, scope="project")
        self.assertIsNone(result)


class TestExtractSkillInfoFields(unittest.TestCase):
    """Tests for extract_skill_info output fields (parity with command tests)."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_type_is_skill(self):
        project = self.temp_path / "proj"
        skill_dir = project / ".claude" / "skills" / "deploy"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Deploy")

        result = extract_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "skill")

    def test_skill_name_from_directory(self):
        project = self.temp_path / "proj"
        skill_dir = project / ".claude" / "skills" / "my-tool"
        skill_dir.mkdir(parents=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text("# Tool")

        result = extract_skill_info(skill_file, extract_single_rule_file, scope="project")
        self.assertEqual(result["skill_name"], "my-tool")
        self.assertEqual(result["file_name"], "SKILL.md")


class TestExtractCommandsFromDirectory(unittest.TestCase):
    """Tests for extract_commands_from_directory populating projects_by_root."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_populates_projects_dict(self):
        """Commands from a directory must appear in projects_by_root."""
        project = self.temp_path / "proj"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")
        (commands_dir / "test.md").write_text("# Test")

        projects_by_root = {}
        extract_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 1)
        project_root = str(project)
        self.assertIn(project_root, projects_by_root)
        commands = projects_by_root[project_root]
        self.assertEqual(len(commands), 2)
        names = {c["skill_name"] for c in commands}
        self.assertEqual(names, {"deploy", "test"})
        for cmd in commands:
            self.assertEqual(cmd["type"], "command")
            self.assertNotIn("project_root", cmd)
            self.assertIn("project_path", cmd)

    def test_ignores_non_md_and_hidden(self):
        """Non-.md files and hidden .md files must be skipped."""
        project = self.temp_path / "proj"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "valid.md").write_text("# Valid")
        (commands_dir / ".hidden.md").write_text("# Hidden")
        (commands_dir / "notes.txt").write_text("notes")

        projects_by_root = {}
        extract_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        commands = projects_by_root[str(project)]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["skill_name"], "valid")

    def test_ignores_subdirectories(self):
        """Subdirectories inside commands/ must not be processed."""
        project = self.temp_path / "proj"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "top.md").write_text("# Top")
        nested = commands_dir / "subdir"
        nested.mkdir()
        (nested / "nested.md").write_text("# Nested")

        projects_by_root = {}
        extract_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        commands = projects_by_root[str(project)]
        self.assertEqual(len(commands), 1)
        self.assertEqual(commands[0]["skill_name"], "top")

    def test_empty_commands_dir(self):
        """Empty commands directory must produce no entries."""
        project = self.temp_path / "proj"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)

        projects_by_root = {}
        extract_commands_from_directory(
            commands_dir, projects_by_root, extract_single_rule_file, add_skill_to_project
        )

        self.assertEqual(len(projects_by_root), 0)


class TestIsUserLevelWithCommandsDir(unittest.TestCase):
    """Tests that is_user_level_skills_dir works correctly for commands directories."""

    def test_user_level_commands_dir(self):
        commands_dir = Path("/Users/john/.claude/commands")
        self.assertTrue(is_user_level_skills_dir(commands_dir, "/Users"))

    def test_project_level_commands_dir(self):
        commands_dir = Path("/Users/john/project/.claude/commands")
        self.assertFalse(is_user_level_skills_dir(commands_dir, "/Users"))

    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_commands_via_home(self, mock_home):
        mock_home.return_value = Path("/Users/testuser")
        commands_dir = Path("/Users/testuser/.claude/commands")
        self.assertTrue(is_user_level_skills_dir(commands_dir))

    def test_windows_user_level_commands(self):
        commands_dir = Path("C:/Users/john/.claude/commands")
        self.assertTrue(is_user_level_skills_dir(commands_dir, "C:/Users"))

    def test_windows_project_level_commands(self):
        commands_dir = Path("C:/Users/john/repos/app/.claude/commands")
        self.assertFalse(is_user_level_skills_dir(commands_dir, "C:/Users"))


class TestMacOSExtractorEndToEnd(unittest.TestCase):
    """End-to-end tests for macOS extractor with both skills and commands."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_skills_and_commands(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor must return both user-level skills and commands."""
        from scripts.coding_discovery_tools.macos.claude_code.skills_extractor import MacOSClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        # Create user-level skill
        skill_dir = fake_home / ".claude" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        # Create user-level command
        commands_dir = fake_home / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        extractor = MacOSClaudeSkillsExtractor()
        # Only test user-level extraction (project-level walks the whole FS)
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 2)
        types = {s["type"] for s in user_skills}
        self.assertEqual(types, {"skill", "command"})
        names = {s["skill_name"] for s in user_skills}
        self.assertEqual(names, {"my-skill", "deploy"})
        # project_root should be renamed to project_path for user-level items
        for item in user_skills:
            self.assertNotIn("project_root", item)
            self.assertIn("project_path", item)

    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_commands_only_no_skills_dir(self, mock_helpers_home, mock_extractor_home, mock_root):
        """Extractor must find commands even when ~/.claude/skills/ does not exist."""
        from scripts.coding_discovery_tools.macos.claude_code.skills_extractor import MacOSClaudeSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        # Only commands, no skills directory
        commands_dir = fake_home / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "greet.md").write_text("# Greet")

        extractor = MacOSClaudeSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "command")
        self.assertEqual(user_skills[0]["skill_name"], "greet")

    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_commands(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must find commands in project .claude/commands/."""
        from scripts.coding_discovery_tools.macos.claude_code.skills_extractor import MacOSClaudeSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "build.md").write_text("# Build")

        skill_dir = project / ".claude" / "skills" / "lint"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Lint")

        extractor = MacOSClaudeSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 2)
        types = {i["type"] for i in items}
        self.assertEqual(types, {"skill", "command"})
        names = {i["skill_name"] for i in items}
        self.assertEqual(names, {"lint", "build"})

    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.claude_code.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_commands_without_skills_dir(self, _mock_skip, _mock_sys_skip):
        """_walk_for_skills must detect .claude/commands/ even without .claude/skills/."""
        from scripts.coding_discovery_tools.macos.claude_code.skills_extractor import MacOSClaudeSkillsExtractor

        project = self.temp_path / "project"
        commands_dir = project / ".claude" / "commands"
        commands_dir.mkdir(parents=True)
        (commands_dir / "deploy.md").write_text("# Deploy")

        extractor = MacOSClaudeSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        items = projects_by_root[str(project)]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["type"], "command")
        self.assertEqual(items[0]["skill_name"], "deploy")


if __name__ == "__main__":
    unittest.main()
