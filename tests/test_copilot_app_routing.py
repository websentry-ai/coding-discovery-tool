"""Windows GitHub Copilot app routing, and the machine-wide Claude root.

The app's name contains "GitHub Copilot", so the generic IDE branch would attach
another surface's workspace rules and, mapping to no VS Code editor, the
JetBrains MCP servers.

The Claude root is the other half: it must reach every scanned profile rather
than pass through the POSIX owner gate, which cannot judge it (st_uid is always
0 on Windows). The gate is forced on here — left to the runner it never fires,
so the assertion would hold no matter what the code did.
"""

import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.user_tool_detector as detector_mod
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.user_tool_detector import find_claude_binary_for_user

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


class MachineWideClaudeAttributionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.program_files = root / "Program Files"
        install = self.program_files / "ClaudeCode"
        install.mkdir(parents=True)
        self.binary = install / "claude.exe"
        self.binary.write_text("")
        os.chmod(self.binary, 0o755)
        self.homes = []
        for name in ("Shobana.K", "Naresh.R"):
            home = root / "Users" / name
            home.mkdir(parents=True)
            self.homes.append(home)
        self.addCleanup(self._tmp.cleanup)
        # Root scan, and the gate disowns whatever it is handed: the only state
        # in which routing the root through machine_global changes the answer.
        patches = [
            patch.object(platform, "system", return_value="Windows"),
            patch.object(detector_mod, "is_running_as_root", return_value=True),
            patch.object(detector_mod, "machine_global_binary_owned_by_user", return_value=False),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_every_profile_resolves_the_enterprise_install(self):
        for home in self.homes:
            env = {"USERPROFILE": str(home), "ProgramW6432": str(self.program_files)}
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(find_claude_binary_for_user(home), str(self.binary))


if __name__ == "__main__":
    unittest.main()
