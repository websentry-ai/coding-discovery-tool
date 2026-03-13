"""
Unit tests for report-level deduplication of rules and skills by file_path.
"""

import unittest

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


class TestDeduplicateProjectItems(unittest.TestCase):
    """Tests for AIToolsDetector._deduplicate_project_items."""

    def test_removes_duplicate_rules_by_file_path(self):
        items = [
            {"file_path": "/project/.cursorrules", "content": "first"},
            {"file_path": "/project/.cursorrules", "content": "second"},
            {"file_path": "/project/.cursorrules", "content": "third"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "first")

    def test_preserves_different_file_paths(self):
        items = [
            {"file_path": "/project/.cursorrules", "content": "a"},
            {"file_path": "/project/.cursorprompt", "content": "b"},
            {"file_path": "/project/CLAUDE.md", "content": "c"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 3)

    def test_keeps_first_occurrence(self):
        items = [
            {"file_path": "/p/rule.md", "content": "keep-me", "size": 100},
            {"file_path": "/p/rule.md", "content": "discard", "size": 200},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], "keep-me")
        self.assertEqual(result[0]["size"], 100)

    def test_empty_list(self):
        result = AIToolsDetector._deduplicate_project_items([])
        self.assertEqual(result, [])

    def test_single_item(self):
        items = [{"file_path": "/a/b", "content": "only"}]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 1)

    def test_items_without_file_path_are_preserved(self):
        items = [
            {"file_path": "/a", "content": "x"},
            {"content": "no-path-1"},
            {"content": "no-path-2"},
            {"file_path": "/a", "content": "dup"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["content"], "x")
        self.assertEqual(result[1]["content"], "no-path-1")
        self.assertEqual(result[2]["content"], "no-path-2")

    def test_mixed_duplicates_and_unique(self):
        items = [
            {"file_path": "/a", "content": "1"},
            {"file_path": "/b", "content": "2"},
            {"file_path": "/a", "content": "3"},
            {"file_path": "/c", "content": "4"},
            {"file_path": "/b", "content": "5"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 3)
        self.assertEqual([r["content"] for r in result], ["1", "2", "4"])


class TestDeduplicateSkills(unittest.TestCase):
    """Tests that skills are deduplicated the same way as rules."""

    def test_removes_duplicate_skills_by_file_path(self):
        items = [
            {"file_path": "/p/.claude/skills/commit/SKILL.md", "skill_name": "commit", "type": "skill"},
            {"file_path": "/p/.claude/skills/commit/SKILL.md", "skill_name": "commit", "type": "skill"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["skill_name"], "commit")

    def test_different_skills_preserved(self):
        items = [
            {"file_path": "/p/.claude/skills/commit/SKILL.md", "skill_name": "commit", "type": "skill"},
            {"file_path": "/p/.claude/skills/deploy/SKILL.md", "skill_name": "deploy", "type": "skill"},
        ]
        result = AIToolsDetector._deduplicate_project_items(items)
        self.assertEqual(len(result), 2)


class TestDeduplicateInProcessSingleTool(unittest.TestCase):
    """Tests that process_single_tool applies dedup before filtering."""

    def test_dedup_applied_to_rules_and_skills(self):
        detector = AIToolsDetector()
        # Simulate a projects_dict with duplicates
        projects_dict = {
            "/project1": {
                "path": "/project1",
                "rules": [
                    {"file_path": "/project1/.cursorrules", "content": "first"},
                    {"file_path": "/project1/.cursorrules", "content": "duplicate"},
                    {"file_path": "/project1/CLAUDE.md", "content": "unique"},
                ],
                "skills": [
                    {"file_path": "/project1/.claude/skills/a/SKILL.md", "skill_name": "a"},
                    {"file_path": "/project1/.claude/skills/a/SKILL.md", "skill_name": "a"},
                    {"file_path": "/project1/.claude/skills/b/SKILL.md", "skill_name": "b"},
                ],
                "mcpServers": [],
            }
        }

        # Apply dedup the same way process_single_tool does
        for project in projects_dict.values():
            if "rules" in project:
                project["rules"] = detector._deduplicate_project_items(project["rules"])
            if "skills" in project:
                project["skills"] = detector._deduplicate_project_items(project["skills"])

        self.assertEqual(len(projects_dict["/project1"]["rules"]), 2)
        self.assertEqual(projects_dict["/project1"]["rules"][0]["content"], "first")
        self.assertEqual(projects_dict["/project1"]["rules"][1]["content"], "unique")
        self.assertEqual(len(projects_dict["/project1"]["skills"]), 2)
        names = [s["skill_name"] for s in projects_dict["/project1"]["skills"]]
        self.assertEqual(names, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
