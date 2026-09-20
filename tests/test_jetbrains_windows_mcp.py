"""Windows JetBrains MCP extraction: the per-user signature + home resolution.

Guards two defects that only bit Windows (macOS/Linux siblings were already
correct):
  1. ``_extract_ide_projects`` took ``(config_path, ide_name)`` while the discovery
     caller passes a third ``user_home`` — a TypeError that zeroed JetBrains
     projects/MCP whenever a JetBrains IDE was installed on Windows.
  2. ``_normalize_path`` expanded ``$USER_HOME$``/``~`` against ``Path.home()`` (the
     scanner's profile), so a root/SYSTEM scan resolved project paths under the
     wrong user.

unittest, not pytest: CI runs ``python -m unittest discover -s tests -t .``.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.windows.jetbrains.mcp_config_extractor import (
    WindowsJetBrainsMCPConfigExtractor,
)

_RECENT_PROJECTS_XML = (
    '<application><component name="RecentProjectsManager">'
    '<option name="additionalInfo"><map>'
    '<entry key="$USER_HOME$/myproject"><value><RecentProjectMetaInfo/></value></entry>'
    '</map></option></component></application>'
)


class TestWindowsJetBrainsUserHome(unittest.TestCase):
    def setUp(self):
        self.ex = WindowsJetBrainsMCPConfigExtractor()
        self.tmp = Path(tempfile.mkdtemp(prefix="jb-win-mcp-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ide_config(self, folder="IntelliJIdea2025.2"):
        options = self.tmp / folder / "options"
        options.mkdir(parents=True)
        return self.tmp / folder

    # 1) The discovery caller passes user_home; the method must accept it.
    def test_extract_ide_projects_accepts_user_home_without_typeerror(self):
        config = self._ide_config()
        try:
            projects = self.ex._extract_ide_projects(config, "IntelliJ IDEA", Path(self.tmp))
        except TypeError as e:
            self.fail(f"_extract_ide_projects must accept user_home: {e}")
        self.assertEqual(projects, [])  # no recent projects planted

    # 2) $USER_HOME$ resolves against the PASSED home, not the scanner's Path.home().
    def test_normalize_path_uses_passed_user_home(self):
        result = self.ex._normalize_path("$USER_HOME$/myproject", Path("/passed/profile"))
        self.assertEqual(result, "\\passed\\profile\\myproject")
        # And never silently falls back to the scanner's own home.
        scanner_home = str(Path.home()).replace("/", "\\")
        self.assertNotIn(scanner_home, result)

    # 3) End-to-end: recentProjects.xml -> normalize(user_home) -> project MCP.
    #    A relative user_home keeps the backslash-normalized path resolvable under a
    #    controlled working dir on any OS (the extractor's Windows path handling
    #    converts "/"->"\\", so a real absolute POSIX path could not be planted).
    def test_extract_ide_projects_resolves_under_user_home_and_finds_mcp(self):
        config = self._ide_config()
        (config / "options" / "recentProjects.xml").write_text(_RECENT_PROJECTS_XML, encoding="utf-8")
        user_home = Path("userhome")  # relative single component, no path separators
        normalized = self.ex._normalize_path("$USER_HOME$/myproject", user_home)
        project_dir = self.tmp / normalized  # the dir the extractor will resolve to
        project_dir.mkdir(parents=True)
        (project_dir / "mcp.json").write_text(
            json.dumps({"mcpServers": {"srv": {"command": "run", "args": ["-x"]}}}),
            encoding="utf-8",
        )

        cwd = os.getcwd()
        os.chdir(self.tmp)
        try:
            projects = self.ex._extract_ide_projects(config, "IntelliJ IDEA", user_home)
        finally:
            os.chdir(cwd)

        self.assertEqual(len(projects), 1)
        # Resolved under the PASSED home (not Path.home()).
        self.assertIn("userhome", projects[0]["path"])
        # The project-level MCP server is surfaced.
        self.assertEqual([s["name"] for s in projects[0]["mcpServers"]], ["srv"])


if __name__ == "__main__":
    unittest.main()
