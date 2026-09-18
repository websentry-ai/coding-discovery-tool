"""Routing and attribution for the Windows GitHub Copilot app.

The app is a desktop client with no IDE config, but its name contains "GitHub
Copilot", so the generic IDE branch would attach another surface's workspace
rules and — mapping to no VS Code editor — the JetBrains MCP servers.

The machine-wide Claude root is the other half: it is attributed to every
scanned profile on purpose, so that must be pinned rather than left to the
POSIX owner gate, which cannot run on Windows.
"""

import os
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.user_tool_detector import find_claude_binary_for_user

_APP = {"name": "GitHub Copilot App", "version": None,
        "install_path": r"C:\Program Files\GitHubCopilot"}


def _detector():
    d = AIToolsDetector(os_name="Windows")
    d._github_copilot_rules_extractor = MagicMock()
    d._github_copilot_rules_extractor.extract_all_github_copilot_rules.return_value = [
        {"project_root": r"C:\repo", "rules": [{"file_name": "copilot-instructions.md"}]},
    ]
    d._github_copilot_mcp_extractor = MagicMock()
    d._github_copilot_mcp_extractor.extract_mcp_config.return_value = {
        "projects": [{"path": r"C:\Users\x\AppData\Local\github-copilot\intellij",
                      "mcpServers": [{"name": "jetbrains-srv"}]}],
    }
    return d


class CopilotAppRoutingTests(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""

    def test_app_row_carries_no_ide_artifacts(self):
        result = _detector().process_single_tool(dict(_APP))

        self.assertEqual(result["name"], "GitHub Copilot App")
        self.assertEqual(result["projects"], [])

    def test_ide_extractors_are_not_run_for_the_app(self):
        d = _detector()
        d.process_single_tool(dict(_APP))

        d._github_copilot_rules_extractor.extract_all_github_copilot_rules.assert_not_called()
        d._github_copilot_mcp_extractor.extract_mcp_config.assert_not_called()

    def test_the_cli_branch_still_wins_over_the_app_branch(self):
        """Both names hit the substring check; neither may take the other's path."""
        d = _detector()
        d._process_copilot_cli_tool = MagicMock(return_value={"name": "GitHub Copilot CLI"})

        d.process_single_tool({"name": "GitHub Copilot CLI", "install_path": "/x"})

        d._process_copilot_cli_tool.assert_called_once()

    def test_probe_is_a_queryable_sentry_tag(self):
        self.assertIn("copilot_app_probe", utils_mod._SENTRY_TAG_KEYS)


class MachineWideClaudeAttributionTests(unittest.TestCase):
    """The enterprise root needs admin to write and every profile can run it, so
    it attributes to each scanned user — never through the POSIX owner gate."""

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
        p = patch.object(platform, "system", return_value="Windows")
        p.start()
        self.addCleanup(p.stop)

    def _env(self, home):
        return {"USERPROFILE": str(home), "ProgramW6432": str(self.program_files)}

    def test_every_profile_resolves_the_enterprise_install(self):
        for home in self.homes:
            with patch.dict(os.environ, self._env(home), clear=True):
                self.assertEqual(find_claude_binary_for_user(home), str(self.binary))

    def test_the_posix_owner_gate_is_never_consulted(self):
        """st_uid is always 0 on Windows, so the gate cannot tell a shared binary
        from a root-owned one; it must stay out of this path entirely."""
        import scripts.coding_discovery_tools.user_tool_detector as detector_mod

        with patch.object(detector_mod, "machine_global_binary_owned_by_user") as gate:
            with patch.dict(os.environ, self._env(self.homes[0]), clear=True):
                find_claude_binary_for_user(self.homes[0])

        gate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
