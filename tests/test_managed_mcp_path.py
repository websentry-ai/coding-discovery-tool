"""Where the organization-deployed MCP config is read from, per platform.

A path spelled wrong and a file never deployed look identical to a scan, so
nothing surfaces when this is broken. Two places here read the same system
directory and are free to disagree, which is what the drift guard covers.
"""
import json
import platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.hooks_extractor import _HOOK_FILES
from scripts.coding_discovery_tools.mcp_extraction_helpers import (
    extract_managed_mcp_config,
    get_managed_mcp_path,
)

_MCP_HELPER = "scripts.coding_discovery_tools.mcp_extraction_helpers"


def _hooks_managed_dir(os_name: str) -> Path:
    """The system directory the hooks extractor reads managed settings from."""
    managed_per_os = _HOOK_FILES["claude code"][2]
    return managed_per_os[os_name][0].parent


class TheManagedMcpPathIsTheOneClaudeCodeReads(unittest.TestCase):

    def test_linux_resolves_the_lowercase_system_directory(self):
        with patch.object(platform, "system", return_value="Linux"):
            resolved = get_managed_mcp_path()

        self.assertEqual(
            resolved, Path("/etc/claude-code/managed-mcp.json"),
            "Linux reads a different directory from the one Claude Code writes"
            " managed configuration to, and the filesystem is case-sensitive, so"
            " every organization-deployed MCP server is invisible to the scan",
        )

    def test_the_linux_directory_agrees_with_the_hooks_extractor(self):
        """Both read Claude Code's system directory; one spelling has to win."""
        with patch.object(platform, "system", return_value="Linux"):
            resolved = get_managed_mcp_path()

        self.assertEqual(
            resolved.parent, _hooks_managed_dir("Linux"),
            "the two readers of this directory disagree, so managed policy is"
            " found by one and missed by the other",
        )

    def test_macos_and_windows_keep_pointing_where_they_did(self):
        """These resolved correctly already; the Linux fix must not move them."""
        for os_name, expected in (
            ("Darwin", Path("/Library/Application Support/ClaudeCode/managed-mcp.json")),
            ("Windows", Path("C:/Program Files/ClaudeCode/managed-mcp.json")),
        ):
            with self.subTest(os=os_name):
                with patch.object(platform, "system", return_value=os_name):
                    self.assertEqual(get_managed_mcp_path(), expected)

    def test_an_unknown_platform_yields_no_path_rather_than_a_guess(self):
        with patch.object(platform, "system", return_value="SunOS"):
            self.assertIsNone(get_managed_mcp_path())


class AManagedFileInThatDirectoryIsActuallyRead(unittest.TestCase):
    """The reader had no tests of its own. Exercised against a real file on disk,
    so a correct path behind a broken reader cannot pass."""

    def test_the_servers_in_it_reach_the_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            managed = Path(tmp) / "managed-mcp.json"
            managed.write_text(json.dumps({
                "mcpServers": {"corp-gateway": {"command": "corp-mcp",
                                                "args": ["--stdio"]}},
            }))

            projects = []
            with patch(f"{_MCP_HELPER}.get_managed_mcp_path", return_value=managed):
                extract_managed_mcp_config(projects)

        served = [
            server
            for project in projects
            for server in project.get("mcpServers", [])
        ]
        self.assertTrue(
            any(s.get("name") == "corp-gateway" for s in served),
            f"a deployed managed MCP server was not reported: {projects}",
        )

    def test_a_missing_file_is_not_an_error(self):
        """The common case on a device with no managed policy at all."""
        with tempfile.TemporaryDirectory() as tmp:
            absent = Path(tmp) / "managed-mcp.json"

            projects = []
            with patch(f"{_MCP_HELPER}.get_managed_mcp_path", return_value=absent):
                extract_managed_mcp_config(projects)

        self.assertEqual(projects, [])


if __name__ == "__main__":
    unittest.main()
