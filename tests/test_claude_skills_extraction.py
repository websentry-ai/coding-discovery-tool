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
    is_skill_md_file,
    build_skills_project_list,
    find_skill_project_root,
    add_skill_to_project,
    is_user_level_skills_dir,
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
        # project_root should be removed from the skill
        self.assertNotIn("project_root", projects["/test/project"][0])
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


if __name__ == "__main__":
    unittest.main()
