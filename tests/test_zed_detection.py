"""Detection tests for Zed (macOS bundle + Linux app dir / binary / flatpak).

Zed ships four macOS channels (Stable/Preview/Nightly/Dev) and, on Linux, a
self-contained ``~/.local/zed.app`` tree, a ``~/.local/bin/zed`` launcher, a
flatpak, and distro packages whose binary is sometimes named ``zeditor``. Every
one of those must produce exactly ONE canonical ``Zed`` row.

The macOS bundle fixture is built inside a temp dir with ``MACHINE_APPS_DIR``
patched, so the test never touches the real ``/Applications`` and runs on Linux
CI unchanged.
"""

import plistlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.zed.zed import LinuxZedDetector
from scripts.coding_discovery_tools.macos.zed.zed import MacOSZedDetector

_MH = "scripts.coding_discovery_tools.macos_extraction_helpers"
_LINUX_ZED_MOD = "scripts.coding_discovery_tools.linux.zed.zed"


def _make_exec(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


class TestMacOSZedDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.machine_apps = root / "Applications"
        self.machine_apps.mkdir(parents=True)
        self.user_home = root / "Users" / "alice"
        (self.user_home / "Applications").mkdir(parents=True)
        self.detector = MacOSZedDetector()
        self.detector.user_home = self.user_home
        self.detector.APP_PATHS = [
            self.machine_apps / name for name in
            ("Zed.app", "Zed Preview.app", "Zed Nightly.app", "Zed Dev.app")
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def _bundle(self, parent: Path, name: str, version: str = "1.20.3") -> Path:
        app = parent / name
        plist = app / "Contents" / "Info.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        with open(plist, "wb") as fh:
            plistlib.dump({"CFBundleShortVersionString": version}, fh)
        return app

    def _detect(self):
        with patch(f"{_MH}.MACHINE_APPS_DIR", self.machine_apps):
            return self.detector.detect()

    def test_machine_wide_bundle_detected_with_version(self):
        app = self._bundle(self.machine_apps, "Zed.app", "1.20.3")
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Zed")
        self.assertEqual(result["version"], "1.20.3")
        self.assertEqual(result["install_path"], str(app))

    def test_user_applications_bundle_detected(self):
        app = self._bundle(self.user_home / "Applications", "Zed.app", "1.21.0")
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(app))
        self.assertEqual(result["version"], "1.21.0")

    def test_preview_channel_reports_canonical_name(self):
        app = self._bundle(self.machine_apps, "Zed Preview.app", "1.22.0")
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Zed")  # no separate "Zed Preview" row
        self.assertEqual(result["install_path"], str(app))

    def test_stable_wins_over_nightly(self):
        stable = self._bundle(self.machine_apps, "Zed.app", "1.20.3")
        self._bundle(self.machine_apps, "Zed Nightly.app", "1.99.0")
        result = self._detect()
        self.assertEqual(result["install_path"], str(stable))
        self.assertEqual(result["version"], "1.20.3")

    def test_empty_home_not_detected(self):
        self.assertIsNone(self._detect())

    def test_bundle_without_version_reports_unknown(self):
        app = self.machine_apps / "Zed.app" / "Contents"
        app.mkdir(parents=True)
        result = self._detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["version"], "Unknown")


class TestLinuxZedDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.detector = LinuxZedDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        self.tmp.cleanup()

    def test_local_zed_app_dir_detected(self):
        app_dir = self.home / ".local" / "zed.app"
        _make_exec(app_dir / "libexec" / "zed-editor")
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Zed")
        self.assertEqual(result["install_path"], str(app_dir))

    def test_emptied_zed_app_dir_not_detected(self):
        # Zed's uninstall script leaves ~/.local/zed.app behind without a launcher.
        (self.home / ".local" / "zed.app").mkdir(parents=True)
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None), \
             patch.object(self.detector, "MACHINE_BIN_PATHS", []), \
             patch.object(self.detector, "MACHINE_FLATPAK_DIRS", []):
            self.assertIsNone(self.detector.detect())

    def test_local_zed_app_version_from_bundled_binary(self):
        app_dir = self.home / ".local" / "zed.app"
        _make_exec(app_dir / "bin" / "zed")
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value="Zed 1.20.3"):
            result = self.detector.detect()
        self.assertEqual(result["version"], "1.20.3")

    def test_local_bin_binary_detected_with_version(self):
        binary = _make_exec(self.home / ".local" / "bin" / "zed")
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value="Zed 1.20.3"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))
        self.assertEqual(result["version"], "1.20.3")

    def test_flatpak_install_detected(self):
        flatpak = self.home / ".local" / "share" / "flatpak" / "app" / "dev.zed.Zed"
        flatpak.mkdir(parents=True)
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(flatpak))
        self.assertEqual(result["version"], "Unknown")

    def test_flatpak_data_dir_alone_not_detected(self):
        # ~/.var/app/<id> is per-app data and survives `flatpak uninstall`.
        (self.home / ".var" / "app" / "dev.zed.Zed").mkdir(parents=True)
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None), \
             patch.object(self.detector, "MACHINE_BIN_PATHS", []), \
             patch.object(self.detector, "MACHINE_FLATPAK_DIRS", []):
            self.assertIsNone(self.detector.detect())

    def test_system_flatpak_install_detected(self):
        flatpak = self.home / "var" / "lib" / "flatpak" / "app" / "dev.zed.Zed"
        flatpak.mkdir(parents=True)
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None), \
             patch.object(self.detector, "MACHINE_BIN_PATHS", []), \
             patch.object(self.detector, "MACHINE_FLATPAK_DIRS", [flatpak]):
            result = self.detector.detect()
        self.assertEqual(result["install_path"], str(flatpak))

    def test_preview_app_dir_detected(self):
        app_dir = self.home / ".local" / "zed-preview.app"
        _make_exec(app_dir / "bin" / "zed")
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "Zed")

    def test_empty_home_not_detected(self):
        with patch(f"{_LINUX_ZED_MOD}.run_command", return_value=None), \
             patch.object(self.detector, "MACHINE_BIN_PATHS", []), \
             patch.object(self.detector, "MACHINE_FLATPAK_DIRS", []):
            self.assertIsNone(self.detector.detect())

    def test_distro_binary_detected(self):
        binary = _make_exec(self.home / "usr" / "bin" / "zeditor")
        with patch.object(self.detector, "MACHINE_BIN_PATHS", [binary]), \
             patch(f"{_LINUX_ZED_MOD}.run_command", return_value="Zed 1.18.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))
        self.assertEqual(result["version"], "1.18.0")

    def test_zfs_zed_daemon_not_detected(self):
        # /usr/bin/zed with no editor marker is the ZFS Event Daemon.
        binary = _make_exec(self.home / "usr" / "bin" / "zed")
        with patch.object(self.detector, "MACHINE_BIN_PATHS", [binary]), \
             patch.object(self.detector, "MACHINE_ZED_MARKERS", []), \
             patch.object(self.detector, "MACHINE_FLATPAK_DIRS", []), \
             patch(f"{_LINUX_ZED_MOD}.run_command", return_value="zed: ZFS Event Daemon"):
            self.assertIsNone(self.detector.detect())

    def test_distro_zed_with_editor_marker_detected(self):
        binary = _make_exec(self.home / "usr" / "bin" / "zed")
        marker = self.home / "usr" / "share" / "applications" / "dev.zed.Zed.desktop"
        marker.parent.mkdir(parents=True)
        marker.write_text("[Desktop Entry]\nName=Zed\n", encoding="utf-8")
        with patch.object(self.detector, "MACHINE_BIN_PATHS", [binary]), \
             patch.object(self.detector, "MACHINE_ZED_MARKERS", [marker]), \
             patch(f"{_LINUX_ZED_MOD}.run_command", return_value="Zed 1.18.0"):
            result = self.detector.detect()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binary))


class TestZedFactory(unittest.TestCase):
    def test_factory_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        self.assertIsInstance(ToolDetectorFactory.create_zed_detector("Darwin"), MacOSZedDetector)
        self.assertIsInstance(ToolDetectorFactory.create_zed_detector("Linux"), LinuxZedDetector)
        self.assertIsNone(ToolDetectorFactory.create_zed_detector("Windows"))
        self.assertIsNone(ToolDetectorFactory.create_zed_detector("Plan9"))

    def test_included_in_all_tool_detectors(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ToolDetectorFactory

        for os_name in ("Darwin", "Linux"):
            names = [d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors(os_name)]
            self.assertIn("Zed", names, f"missing for {os_name}")
        windows_names = [
            d.tool_name for d in ToolDetectorFactory.create_all_tool_detectors("Windows")
        ]
        self.assertNotIn("Zed", windows_names)

    def test_rules_extractor_factory_per_os(self):
        from scripts.coding_discovery_tools.coding_tool_factory import ZedRulesExtractorFactory

        self.assertIsNotNone(ZedRulesExtractorFactory.create("Darwin"))
        self.assertIsNotNone(ZedRulesExtractorFactory.create("Linux"))
        self.assertIsNone(ZedRulesExtractorFactory.create("Windows"))


if __name__ == "__main__":
    unittest.main()
