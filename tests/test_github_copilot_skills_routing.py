"""The 'github copilot (vs code)' processing branch attaches agent skills.

VS Code Copilot loads agent skills from the same locations as the Copilot CLI
(project .github/.claude/.agents + personal ~/.copilot/~/.claude/~/.agents), so
the branch reuses the Copilot CLI skills extractor and attaches results to
projects[].skills[].
"""
import unittest
from unittest.mock import MagicMock

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


class TestGithubCopilotVscodeSkillsRouting(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.det = AIToolsDetector(os_name="Darwin")
        self.det._github_copilot_rules_extractor = MagicMock()
        self.det._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = []
        self.det._github_copilot_mcp_extractor = MagicMock()
        self.det._github_copilot_mcp_extractor.extract_mcp_config.return_value = None
        self.det._copilot_cli_skills_extractor = MagicMock()
        self.det._copilot_cli_skills_extractor.extract_all_skills.return_value = {
            "user_skills": [self._skill("user-skill", "/Users/x/.copilot/skills/user-skill/SKILL.md", "user")],
            "project_skills": [{
                "project_root": "/repo",
                "skills": [self._skill("proj-skill", "/repo/.github/skills/proj-skill/SKILL.md", "project")],
            }],
        }
        self.tool = {
            "name": "GitHub Copilot (VS Code)", "version": "0.51.0",
            "install_path": "/Applications/Visual Studio Code.app/Contents/Resources/app/extensions/copilot",
        }

    @staticmethod
    def _skill(name, fp, scope):
        return {"file_path": fp, "file_name": "SKILL.md", "content": "x", "size": 1,
                "last_modified": "t", "truncated": False, "scope": scope,
                "skill_name": name, "type": "skill"}

    def test_project_and_user_skills_attached(self):
        result = self.det.process_single_tool(dict(self.tool))
        projs = {p["path"]: p for p in result["projects"]}
        self.assertIn("/repo", projs)
        self.assertEqual([s["skill_name"] for s in projs["/repo"]["skills"]], ["proj-skill"])
        self.assertIn(self.tool["install_path"], projs)
        self.assertEqual([s["skill_name"] for s in projs[self.tool["install_path"]]["skills"]], ["user-skill"])

    def test_no_skills_extractor_does_not_crash(self):
        self.det._copilot_cli_skills_extractor = None
        result = self.det.process_single_tool(dict(self.tool))
        self.assertEqual(result["projects"], [])


if __name__ == "__main__":
    unittest.main()
