"""The Windows GitHub Copilot app must not be processed as an IDE surface.

Its name contains "GitHub Copilot", so the generic IDE branch would attach
another surface's workspace rules and, mapping to no VS Code editor, the
JetBrains MCP servers.
"""

import unittest
from unittest.mock import MagicMock

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector

_APP = {"name": "GitHub Copilot App", "version": None,
        "install_path": r"C:\Program Files\GitHubCopilot"}


class CopilotAppRoutingTests(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_app_row_carries_no_ide_artifacts(self):
        detector = AIToolsDetector(os_name="Windows")
        detector._github_copilot_rules_extractor = MagicMock()
        detector._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = [
            {"project_root": r"C:\repo", "rules": [{"file_name": "copilot-instructions.md"}]},
        ]
        detector._github_copilot_mcp_extractor = MagicMock()
        detector._github_copilot_mcp_extractor.extract_mcp_config.return_value = {
            "projects": [{"path": r"C:\Users\x\AppData\Local\github-copilot\intellij",
                          "mcpServers": [{"name": "jetbrains-srv"}]}],
        }

        result = detector.process_single_tool(dict(_APP))

        self.assertEqual(result["name"], "GitHub Copilot App")
        self.assertEqual(result["projects"], [])

    def test_probe_is_a_queryable_sentry_tag(self):
        self.assertIn("copilot_app_probe", utils_mod._SENTRY_TAG_KEYS)


if __name__ == "__main__":
    unittest.main()
