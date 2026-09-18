"""Detection and MCP-extraction tests for Claude Desktop.

Both detectors AND-require an install and the per-user data directory, mirroring
the Cowork gate: the config tree survives an uninstall (anthropics/claude-code#25013),
so the data dir alone is residue.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.macos.claude_desktop import (
    MacOSClaudeDesktopDetector,
    MacOSClaudeDesktopMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.claude_desktop import (
    WindowsClaudeDesktopDetector,
    WindowsClaudeDesktopMCPConfigExtractor,
)

_BUNDLE_MOD = "scripts.coding_discovery_tools.macos.claude_bundle"
_MOD = "scripts.coding_discovery_tools.macos.claude_desktop.claude_desktop"
_UTILS_MOD = "scripts.coding_discovery_tools.utils"
_WIN_MOD = "scripts.coding_discovery_tools.windows.claude_desktop.claude_desktop"


def _make_bundle(app: Path, version="1.2.3"):
    contents = app / "Contents"
    contents.mkdir(parents=True, exist_ok=True)
    with (contents / "Info.plist").open("wb") as fh:
        plistlib.dump({"CFBundleShortVersionString": version}, fh)
    return app


class TestClaudeDesktopDetect(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.data_dir = self.home / "Library" / "Application Support" / "Claude"
        self.detector = MacOSClaudeDesktopDetector()
        self.detector.user_home = self.home
        # Keep the machine-wide path and Spotlight out of every test's way.
        patcher = patch(f"{_BUNDLE_MOD}.CLAUDE_DESKTOP_APP_PATH", self.home / "absent" / "Claude.app")
        patcher.start()
        self.addCleanup(patcher.stop)
        mdfind = patch(f"{_BUNDLE_MOD}.run_command_status", return_value=(None, True))
        mdfind.start()
        self.addCleanup(mdfind.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_bundle_and_data_dir_detected(self):
        _make_bundle(self.home / "Applications" / "Claude.app", version="2.110.1")
        self.data_dir.mkdir(parents=True)

        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Claude Desktop")
        self.assertEqual(result["version"], "2.110.1")
        self.assertEqual(result["install_path"], str(self.data_dir))

    def test_data_dir_without_bundle_is_residue(self):
        self.data_dir.mkdir(parents=True)
        self.assertIsNone(self.detector.detect())

    def test_bundle_without_data_dir_is_never_opened(self):
        _make_bundle(self.home / "Applications" / "Claude.app")
        self.assertIsNone(self.detector.detect())

    def test_version_missing_does_not_block_detection(self):
        app = self.home / "Applications" / "Claude.app"
        (app / "Contents").mkdir(parents=True)
        self.data_dir.mkdir(parents=True)

        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertIsNone(result["version"])

    def test_scan_uses_scanned_user_home_not_scanner(self):
        """Under a root/MDM run the scanned user's home wins over the scanner's."""
        scanner_home = Path(self._tmp.name) / "scanner"
        _make_bundle(scanner_home / "Applications" / "Claude.app")
        (scanner_home / "Library" / "Application Support" / "Claude").mkdir(parents=True)

        with patch(f"{_BUNDLE_MOD}.Path.home", return_value=scanner_home):
            self.assertIsNone(self.detector.detect())

    def test_unreadable_data_dir_raises_for_our_own_home(self):
        """Our own home denied is a real anomaly: raise so nothing is pruned."""
        with patch(f"{_MOD}.dir_state", return_value="unreadable"), \
                patch(f"{_UTILS_MOD}._is_scanning_users_own_home", return_value=True):
            with self.assertRaises(PermissionError):
                self.detector.detect()

    def test_unreadable_sibling_home_does_not_fail_the_scan(self):
        """A 0700 sibling is expected on an unprivileged multi-user box. Raising
        here sets incomplete_reasons, which nulls the manifest device-wide."""
        with patch(f"{_MOD}.dir_state", return_value="unreadable"), \
                patch(f"{_UTILS_MOD}._is_scanning_users_own_home", return_value=False), \
                patch(f"{_UTILS_MOD}._is_root", return_value=False), \
                patch(f"{_UTILS_MOD}._windows_process_is_elevated", return_value=False):
            self.assertIsNone(self.detector.detect())

    def test_spotlight_that_could_not_run_does_not_read_as_absent(self):
        """Incident 326: a failed probe is ignorance, not 'no Claude installed'."""
        self.data_dir.mkdir(parents=True)
        with patch(f"{_BUNDLE_MOD}.run_command_status", return_value=(None, False)), \
                patch(f"{_UTILS_MOD}._is_scanning_users_own_home", return_value=True):
            with self.assertRaises(PermissionError):
                self.detector.detect()


class TestWindowsClaudeDesktopDetect(unittest.TestCase):
    """Same gate as macOS, resolved against %APPDATA%/Claude and the Windows
    install locations already used by the Cowork detector."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        self.data_dir = self.home / "AppData" / "Roaming" / "Claude"
        self.install = self.home / "AppData" / "Local" / "Programs" / "Claude"
        self.detector = WindowsClaudeDesktopDetector()
        self.detector.user_home = self.home
        self.addCleanup(self._tmp.cleanup)

    def test_install_and_data_dir_detected(self):
        self.data_dir.mkdir(parents=True)
        self.install.mkdir(parents=True)

        result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Claude Desktop")
        self.assertEqual(result["install_path"], str(self.data_dir))

    def test_data_dir_without_install_is_residue(self):
        self.data_dir.mkdir(parents=True)
        self.assertIsNone(self.detector.detect())

    def test_install_without_data_dir_is_never_opened(self):
        self.install.mkdir(parents=True)
        self.assertIsNone(self.detector.detect())

    def test_scan_uses_scanned_user_home_not_scanner(self):
        """An MDM scan runs elevated across profiles; the scanned user's AppData wins."""
        scanner_home = Path(self._tmp.name) / "scanner"
        (scanner_home / "AppData" / "Roaming" / "Claude").mkdir(parents=True)
        (scanner_home / "AppData" / "Local" / "Programs" / "Claude").mkdir(parents=True)

        with patch(f"{_WIN_MOD}.Path.home", return_value=scanner_home):
            self.assertIsNone(self.detector.detect())

    def test_unreadable_sibling_home_does_not_fail_the_scan(self):
        with patch(f"{_WIN_MOD}.dir_state", return_value="unreadable"), \
                patch(f"{_UTILS_MOD}._is_scanning_users_own_home", return_value=False), \
                patch(f"{_UTILS_MOD}._is_root", return_value=False), \
                patch(f"{_UTILS_MOD}._windows_process_is_elevated", return_value=False):
            self.assertIsNone(self.detector.detect())


class TestClaudeDesktopMCPExtractor(unittest.TestCase):
    def test_global_config_path_is_the_documented_one(self):
        """Both paths are four levels below the home the root/admin walk rewrites."""
        for extractor in (MacOSClaudeDesktopMCPConfigExtractor,
                          WindowsClaudeDesktopMCPConfigExtractor):
            path = extractor.GLOBAL_MCP_CONFIG_PATH
            self.assertEqual(path.name, "claude_desktop_config.json")
            self.assertEqual(path.parent.name, "Claude")
            self.assertEqual(path.parents[3], Path.home())

    def test_no_config_returns_none(self):
        extractor = MacOSClaudeDesktopMCPConfigExtractor()
        with patch.object(extractor, "_extract_global_config", return_value=[]):
            self.assertIsNone(extractor.extract_mcp_config())

    def test_config_is_wrapped_in_projects(self):
        extractor = MacOSClaudeDesktopMCPConfigExtractor()
        project = {"project_root": "global", "mcp_servers": [{"name": "filesystem"}]}
        with patch.object(extractor, "_extract_global_config", return_value=[project]):
            self.assertEqual(extractor.extract_mcp_config(), {"projects": [project]})


if __name__ == "__main__":
    unittest.main()
