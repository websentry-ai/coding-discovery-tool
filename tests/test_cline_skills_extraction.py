"""
Unit tests for Cline skills extraction.
"""

import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.cline_skills_helpers import (
    CLINE_DIR_NAME,
    CLINERULES_DIR_NAME,
    CLAUDE_DIR_NAME,
    SKILLS_DIR_NAME,
    SKILL_FILE_NAME,
    CLINE_PARENT_DIR_NAMES,
    CLINE_USER_DIR_NAMES,
    CLINE_SKILL_CONFIG,
    CLINE_ITEM_CONFIGS,
    find_cline_item_project_root,
    extract_cline_item_info,
    extract_cline_items_from_directory,
    extract_cline_user_level_items,
)
from scripts.coding_discovery_tools.claude_code_skills_helpers import (
    ItemTypeConfig,
    is_skill_md_file,
    build_skills_project_list,
    add_skill_to_project,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import (
    extract_single_rule_file,
)


class TestClineConstants(unittest.TestCase):
    """Tests for Cline skills constants."""

    def test_cline_dir_name(self):
        self.assertEqual(CLINE_DIR_NAME, ".cline")

    def test_clinerules_dir_name(self):
        self.assertEqual(CLINERULES_DIR_NAME, ".clinerules")

    def test_claude_dir_name(self):
        self.assertEqual(CLAUDE_DIR_NAME, ".claude")

    def test_skills_dir_name(self):
        self.assertEqual(SKILLS_DIR_NAME, "skills")

    def test_skill_file_name(self):
        self.assertEqual(SKILL_FILE_NAME, "SKILL.md")

    def test_parent_dir_names_has_three(self):
        self.assertEqual(len(CLINE_PARENT_DIR_NAMES), 3)
        self.assertIn(".cline", CLINE_PARENT_DIR_NAMES)
        self.assertIn(".clinerules", CLINE_PARENT_DIR_NAMES)
        self.assertIn(".claude", CLINE_PARENT_DIR_NAMES)

    def test_user_dir_names_only_cline(self):
        self.assertEqual(len(CLINE_USER_DIR_NAMES), 1)
        self.assertIn(".cline", CLINE_USER_DIR_NAMES)


class TestClineItemConfigIntegrity(unittest.TestCase):
    """Validate config instances have correct structure and callables."""

    def test_skill_config_type_name(self):
        self.assertEqual(CLINE_SKILL_CONFIG.type_name, "skill")

    def test_skill_config_dir_name(self):
        self.assertEqual(CLINE_SKILL_CONFIG.dir_name, "skills")

    def test_skill_config_layout(self):
        self.assertEqual(CLINE_SKILL_CONFIG.layout, "nested")

    def test_skill_config_file_filter(self):
        self.assertTrue(CLINE_SKILL_CONFIG.file_filter("SKILL.md"))
        self.assertTrue(CLINE_SKILL_CONFIG.file_filter("skill.md"))
        self.assertFalse(CLINE_SKILL_CONFIG.file_filter("README.md"))

    def test_skill_config_name_extractor(self):
        p = Path("/proj/.cline/skills/my-skill/SKILL.md")
        self.assertEqual(CLINE_SKILL_CONFIG.name_extractor(p), "my-skill")

    def test_item_configs_list(self):
        self.assertEqual(len(CLINE_ITEM_CONFIGS), 1)
        self.assertIs(CLINE_ITEM_CONFIGS[0], CLINE_SKILL_CONFIG)

    def test_configs_are_item_type_config_instances(self):
        for config in CLINE_ITEM_CONFIGS:
            self.assertIsInstance(config, ItemTypeConfig)


class TestFindClineItemProjectRoot(unittest.TestCase):
    """Tests for find_cline_item_project_root with all three parent dirs."""

    def test_cline_directory(self):
        skill_file = Path("/Users/test/myproject/.cline/skills/commit/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_clinerules_directory(self):
        skill_file = Path("/Users/test/myproject/.clinerules/skills/deploy/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_claude_directory(self):
        skill_file = Path("/Users/test/myproject/.claude/skills/review/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/myproject"))

    def test_user_level_skill(self):
        skill_file = Path("/Users/test/.cline/skills/global-skill/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test"))

    def test_nested_project(self):
        skill_file = Path("/Users/test/work/repos/proj/.clinerules/skills/lint/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/work/repos/proj"))

    def test_windows_style_path(self):
        skill_file = Path("C:/Users/test/project/.cline/skills/commit/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("C:/Users/test/project"))

    def test_unknown_parent_dir_falls_back(self):
        skill_file = Path("/Users/test/project/.unknown/skills/my-skill/SKILL.md")
        result = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        # Should NOT return /Users/test/project because .unknown is not recognized
        self.assertNotEqual(result, Path("/Users/test/project"))


class TestClineSkillsExtractionIntegration(unittest.TestCase):
    """Integration tests for Cline skills extraction with real filesystem."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, parent_dir: str, skill_name: str, content: str = "# Test Skill"):
        skill_dir = base_path / parent_dir / "skills" / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_create_and_find_skill_cline(self):
        project = self.temp_path / "myproject"
        project.mkdir()

        skill_file = self._create_skill(project, ".cline", "my-skill", "# My Skill")
        self.assertTrue(skill_file.exists())
        self.assertTrue(is_skill_md_file(skill_file.name))

        project_root = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(project_root, project)

    def test_create_and_find_skill_clinerules(self):
        project = self.temp_path / "myproject"
        project.mkdir()

        skill_file = self._create_skill(project, ".clinerules", "deploy-skill")
        project_root = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(project_root, project)

    def test_create_and_find_skill_claude(self):
        project = self.temp_path / "myproject"
        project.mkdir()

        skill_file = self._create_skill(project, ".claude", "review-skill")
        project_root = find_cline_item_project_root(skill_file, CLINE_SKILL_CONFIG)
        self.assertEqual(project_root, project)

    def test_multiple_skills_in_project(self):
        project = self.temp_path / "project"
        project.mkdir()

        self._create_skill(project, ".cline", "skill-a", "# A")
        self._create_skill(project, ".cline", "skill-b", "# B")
        self._create_skill(project, ".cline", "skill-c", "# C")

        skills_dir = project / ".cline" / "skills"
        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 3)

    def test_empty_skills_directory(self):
        project = self.temp_path / "project"
        skills_dir = project / ".cline" / "skills"
        skills_dir.mkdir(parents=True)

        skill_count = sum(1 for d in skills_dir.iterdir() if d.is_dir())
        self.assertEqual(skill_count, 0)

    def test_skill_directory_without_skill_md(self):
        project = self.temp_path / "project"
        skill_dir = project / ".cline" / "skills" / "incomplete"
        skill_dir.mkdir(parents=True)
        (skill_dir / "README.md").write_text("Not a skill file")

        self.assertTrue(skill_dir.exists())
        skill_files = [f for f in skill_dir.iterdir() if is_skill_md_file(f.name)]
        self.assertEqual(len(skill_files), 0)


class TestExtractClineItemInfo(unittest.TestCase):
    """Tests for extract_cline_item_info output fields."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, parent_dir: str, name: str, content: str = "# Test"):
        skill_dir = base_path / parent_dir / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_type_is_skill(self):
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, ".cline", "deploy")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "skill")

    def test_skill_name_from_directory(self):
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, ".cline", "my-tool", "# Tool")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertEqual(result["skill_name"], "my-tool")
        self.assertEqual(result["file_name"], "SKILL.md")

    def test_project_root_detected_cline(self):
        project = self.temp_path / "myproject"
        skill_file = self._create_skill(project, ".cline", "build")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertEqual(result["project_root"], str(project))

    def test_project_root_detected_clinerules(self):
        project = self.temp_path / "myproject"
        skill_file = self._create_skill(project, ".clinerules", "build")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertEqual(result["project_root"], str(project))

    def test_project_root_detected_claude(self):
        project = self.temp_path / "myproject"
        skill_file = self._create_skill(project, ".claude", "build")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertEqual(result["project_root"], str(project))

    def test_content_preserved(self):
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, ".cline", "lint", "# Lint\nRun the linter")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertIn("Lint", result["content"])
        self.assertIn("Run the linter", result["content"])

    def test_scope_passed_through(self):
        project = self.temp_path / "proj"
        skill_file = self._create_skill(project, ".cline", "cmd")
        result = extract_cline_item_info(skill_file, extract_single_rule_file, scope="user", config=CLINE_SKILL_CONFIG)
        self.assertEqual(result["scope"], "user")

    def test_nonexistent_file_returns_none(self):
        fake = self.temp_path / ".cline" / "skills" / "ghost" / "SKILL.md"
        result = extract_cline_item_info(fake, extract_single_rule_file, scope="project", config=CLINE_SKILL_CONFIG)
        self.assertIsNone(result)


class TestExtractClineItemsFromDirectory(unittest.TestCase):
    """Tests for extract_cline_items_from_directory populating projects_by_root."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, parent_dir: str, name: str, content: str = "# Test"):
        skill_dir = base_path / parent_dir / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content)
        return skill_file

    def test_populates_projects_dict(self):
        project = self.temp_path / "proj"
        self._create_skill(project, ".cline", "deploy", "# Deploy")
        self._create_skill(project, ".cline", "test", "# Test")

        skills_dir = project / ".cline" / "skills"
        projects_by_root = {}
        extract_cline_items_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project, CLINE_SKILL_CONFIG
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

    def test_clinerules_populates_projects_dict(self):
        project = self.temp_path / "proj"
        self._create_skill(project, ".clinerules", "api", "# API")

        skills_dir = project / ".clinerules" / "skills"
        projects_by_root = {}
        extract_cline_items_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project, CLINE_SKILL_CONFIG
        )

        self.assertEqual(len(projects_by_root), 1)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "api")

    def test_empty_skills_dir(self):
        project = self.temp_path / "proj"
        skills_dir = project / ".cline" / "skills"
        skills_dir.mkdir(parents=True)

        projects_by_root = {}
        extract_cline_items_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project, CLINE_SKILL_CONFIG
        )

        self.assertEqual(len(projects_by_root), 0)

    def test_non_skill_dirs_ignored(self):
        project = self.temp_path / "proj"
        self._create_skill(project, ".cline", "valid", "# Valid")

        no_skill = project / ".cline" / "skills" / "no-skill"
        no_skill.mkdir(parents=True)
        (no_skill / "README.md").write_text("Not a skill")

        skills_dir = project / ".cline" / "skills"
        projects_by_root = {}
        extract_cline_items_from_directory(
            skills_dir, projects_by_root, extract_single_rule_file, add_skill_to_project, CLINE_SKILL_CONFIG
        )

        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "valid")


class TestExtractClineUserLevelItems(unittest.TestCase):
    """Tests for extract_cline_user_level_items."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_skill(self, base_path: Path, parent_dir: str, name: str, content: str = "# Test"):
        skill_dir = base_path / parent_dir / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content)

    def test_user_level_from_cline_dir(self):
        fake_home = self.temp_path / "home"
        fake_home.mkdir()
        self._create_skill(fake_home, ".cline", "global-skill", "# Global")

        user_skills = []
        extract_cline_user_level_items(fake_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["skill_name"], "global-skill")
        self.assertEqual(user_skills[0]["scope"], "user")
        self.assertEqual(user_skills[0]["type"], "skill")
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    def test_user_level_ignores_clinerules(self):
        fake_home = self.temp_path / "home"
        fake_home.mkdir()
        self._create_skill(fake_home, ".clinerules", "should-not-find")

        user_skills = []
        extract_cline_user_level_items(fake_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        self.assertEqual(len(user_skills), 0)

    def test_user_level_ignores_claude(self):
        fake_home = self.temp_path / "home"
        fake_home.mkdir()
        self._create_skill(fake_home, ".claude", "should-not-find")

        user_skills = []
        extract_cline_user_level_items(fake_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        self.assertEqual(len(user_skills), 0)

    def test_no_skills_dir_returns_empty(self):
        fake_home = self.temp_path / "home"
        fake_home.mkdir()

        user_skills = []
        extract_cline_user_level_items(fake_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        self.assertEqual(len(user_skills), 0)

    def test_multiple_user_skills(self):
        fake_home = self.temp_path / "home"
        fake_home.mkdir()
        self._create_skill(fake_home, ".cline", "skill-a", "# A")
        self._create_skill(fake_home, ".cline", "skill-b", "# B")

        user_skills = []
        extract_cline_user_level_items(fake_home, user_skills, extract_single_rule_file, CLINE_ITEM_CONFIGS)

        self.assertEqual(len(user_skills), 2)
        names = {s["skill_name"] for s in user_skills}
        self.assertEqual(names, {"skill-a", "skill-b"})


class TestClineSkillThreadSafety(unittest.TestCase):
    """Tests for thread-safe operations with Cline skills."""

    def test_concurrent_add_skill_to_project(self):
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
        for i in range(100):
            t = threading.Thread(target=add_with_lock, args=(f"skill-{i}", "/test/project"))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(projects["/test/project"]), 100)


class TestClineVsCursorSkillPaths(unittest.TestCase):
    """Tests to verify Cline accepts .cline/.clinerules/.claude but not .cursor."""

    def test_cline_path_structure(self):
        skill = Path("/Users/test/project/.cline/skills/my-skill/SKILL.md")
        result = find_cline_item_project_root(skill, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/project"))

    def test_clinerules_path_structure(self):
        skill = Path("/Users/test/project/.clinerules/skills/my-skill/SKILL.md")
        result = find_cline_item_project_root(skill, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/project"))

    def test_claude_path_structure(self):
        skill = Path("/Users/test/project/.claude/skills/my-skill/SKILL.md")
        result = find_cline_item_project_root(skill, CLINE_SKILL_CONFIG)
        self.assertEqual(result, Path("/Users/test/project"))

    def test_cursor_directory_not_recognized(self):
        skill = Path("/Users/test/project/.cursor/skills/my-skill/SKILL.md")
        result = find_cline_item_project_root(skill, CLINE_SKILL_CONFIG)
        self.assertNotEqual(result, Path("/Users/test/project"))


class TestMacOSClineSkillsExtractor(unittest.TestCase):
    """Tests for macOS Cline skills extractor."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.temp_path = Path(self.temp_dir)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_user_level_skills(self, mock_helpers_home, mock_extractor_home, mock_root):
        from scripts.coding_discovery_tools.macos.cline.skills_extractor import MacOSClineSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        skill_dir = fake_home / ".cline" / "skills" / "my-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# My Skill")

        extractor = MacOSClineSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 1)
        self.assertEqual(user_skills[0]["type"], "skill")
        self.assertEqual(user_skills[0]["skill_name"], "my-skill")
        self.assertNotIn("project_root", user_skills[0])
        self.assertIn("project_path", user_skills[0])

    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.is_running_as_root')
    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.Path.home')
    @patch('scripts.coding_discovery_tools.claude_code_skills_helpers.Path.home')
    def test_no_skills_dir_returns_empty(self, mock_helpers_home, mock_extractor_home, mock_root):
        from scripts.coding_discovery_tools.macos.cline.skills_extractor import MacOSClineSkillsExtractor

        fake_home = self.temp_path / "fakehome"
        fake_home.mkdir()
        mock_helpers_home.return_value = fake_home
        mock_extractor_home.return_value = fake_home
        mock_root.return_value = False

        extractor = MacOSClineSkillsExtractor()
        user_skills = []
        extractor._extract_user_level_skills(user_skills)

        self.assertEqual(len(user_skills), 0)

    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_project_skills(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.macos.cline.skills_extractor import MacOSClineSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".cline" / "skills" / "proj-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Project Skill")

        extractor = MacOSClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "proj-skill")

    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_clinerules_skills(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.macos.cline.skills_extractor import MacOSClineSkillsExtractor

        project = self.temp_path / "project"
        skill_dir = project / ".clinerules" / "skills" / "api-skill"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# API Skill")

        extractor = MacOSClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 1)
        self.assertEqual(skills[0]["skill_name"], "api-skill")

    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_system_path', return_value=False)
    @patch('scripts.coding_discovery_tools.macos.cline.skills_extractor.should_skip_path', return_value=False)
    def test_walk_finds_skills_across_all_parent_dirs(self, _mock_skip, _mock_sys_skip):
        from scripts.coding_discovery_tools.macos.cline.skills_extractor import MacOSClineSkillsExtractor

        project = self.temp_path / "project"
        for parent_dir, skill_name in [(".cline", "skill-a"), (".clinerules", "skill-b"), (".claude", "skill-c")]:
            skill_dir = project / parent_dir / "skills" / skill_name
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(f"# {skill_name}")

        extractor = MacOSClineSkillsExtractor()
        projects_by_root = {}
        extractor._walk_for_skills(self.temp_path, self.temp_path, projects_by_root, current_depth=0)

        self.assertIn(str(project), projects_by_root)
        skills = projects_by_root[str(project)]
        self.assertEqual(len(skills), 3)
        names = {s["skill_name"] for s in skills}
        self.assertEqual(names, {"skill-a", "skill-b", "skill-c"})


if __name__ == "__main__":
    unittest.main()
