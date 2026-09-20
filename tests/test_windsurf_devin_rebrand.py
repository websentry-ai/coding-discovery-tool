"""Windsurf ships as Devin after the rebrand; both layouts must be detected.

The bundle, the Windows executable, the Linux binary and the per-user data
folder were all renamed. Existing installs keep the old names, so every check
has to accept either without turning one editor into two.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.coding_discovery_tools.vscode_extension_helpers import (  # noqa: E402
    VSCODE_EDITOR_DISPLAY_NAMES, VSCODE_EDITOR_KEYS, extensions_dir_for_editor,
    find_extension_in_editor, reset_vscode_registry_state, vscode_registry_state,
)

CLINE_EXT_ID = "saoudrizwan.claude-dev"
KILO_EXT_ID = "kilocode.kilo-code"


class ExtensionsDirSurvivesTheRename(unittest.TestCase):

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())

    def test_legacy_windsurf_dir_is_still_returned(self):
        legacy = self.home / ".windsurf" / "extensions"
        legacy.mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), legacy)

    def test_renamed_devin_dir_is_found(self):
        devin = self.home / ".devin" / "extensions"
        devin.mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), devin)

    def test_the_renamed_dir_wins_when_both_exist(self):
        """A migrated machine reports the dir the installed build actually reads."""
        (self.home / ".windsurf" / "extensions").mkdir(parents=True)
        devin = self.home / ".devin" / "extensions"
        devin.mkdir(parents=True)
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"), devin)

    def test_absent_both_falls_back_to_the_legacy_path(self):
        self.assertEqual(extensions_dir_for_editor(self.home, "Windsurf"),
                         self.home / ".windsurf" / "extensions")

    def test_the_rename_did_not_create_a_second_editor(self):
        """One key and one row; only the name shown to a human changed."""
        self.assertNotIn("Devin", VSCODE_EDITOR_KEYS)
        self.assertEqual(VSCODE_EDITOR_DISPLAY_NAMES["Windsurf"], "Devin Desktop")
        self.assertEqual(1, sum(1 for name in VSCODE_EDITOR_DISPLAY_NAMES.values()
                                if "Devin" in name))


class DetectorsAcceptBothNames(unittest.TestCase):

    def test_macos_detector_lists_both_bundles(self):
        from scripts.coding_discovery_tools.macos.windsurf.windsurf import (
            MacOSWindsurfDetector,
        )
        self.assertEqual(MacOSWindsurfDetector.DEFAULT_APP_PATH.name, "Windsurf.app")
        self.assertEqual(MacOSWindsurfDetector.REBRANDED_APP_PATH.name, "Devin.app")

    def test_linux_detector_accepts_the_devin_binary(self):
        from scripts.coding_discovery_tools.linux.windsurf import windsurf as lw
        names = {p.name for p in lw._SYSTEM_PATHS} | {p.name for p in lw._USER_RELATIVE_PATHS}
        self.assertIn("windsurf", names)
        self.assertIn("devin-desktop", names)

    def test_a_devin_binary_anywhere_on_path_is_found(self):
        """The rename is invisible to the enumerated paths when PATH carries it."""
        from scripts.coding_discovery_tools.linux.windsurf import windsurf as lw

        def fake_run(cmd, _timeout=None):
            if cmd == ["which", "devin-desktop"]:
                return "/home/dev/bin/devin-desktop\n"
            if cmd == ["devin-desktop", "--version"]:
                return "1.126.0\n"
            return ""

        with mock.patch.object(lw, "run_command", fake_run), \
                mock.patch.object(lw, "get_linux_user_homes", lambda: []):
            result = lw.LinuxWindsurfDetector().detect()
        self.assertEqual("Devin Desktop", result["name"])
        self.assertEqual("/home/dev/bin/devin-desktop", result["install_path"])
        self.assertEqual("1.126.0", result["version"])

    def test_host_ide_lists_include_the_renamed_bundle(self):
        from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
        from scripts.coding_discovery_tools.macos.roo_code.roo_code import (
            MacOSRooDetector,
        )
        for cls in (MacOSClineDetector, MacOSRooDetector):
            apps = cls.IDE_APP_NAMES["Windsurf"]
            self.assertIn("Windsurf.app", apps, cls.__name__)
            self.assertIn("Devin.app", apps, cls.__name__)


class HostedExtensionsSurviveTheMigration(unittest.TestCase):
    """An upgraded machine keeps a stale ``~/.windsurf`` beside the live ``~/.devin``."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        reset_vscode_registry_state()

    def _write_registry(self, rel, ext_id, version):
        ext_dir = self.home / rel
        ext_dir.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text(json.dumps([{
            "identifier": {"id": ext_id},
            "version": version,
            "relativeLocation": f"{ext_id}-{version}",
        }]), encoding="utf-8")
        return ext_dir

    def test_devin_registry_is_read_when_the_stale_windsurf_dir_survives(self):
        self._write_registry(".windsurf/extensions", "some.other-extension", "1.0.0")
        devin = self._write_registry(".devin/extensions", CLINE_EXT_ID, "3.7.0")
        location, version = find_extension_in_editor(self.home, "Windsurf", CLINE_EXT_ID)
        self.assertEqual(version, "3.7.0")
        self.assertEqual(location, str(devin / f"{CLINE_EXT_ID}-3.7.0"))

    def test_the_live_registry_answers_when_both_list_it(self):
        self._write_registry(".windsurf/extensions", CLINE_EXT_ID, "3.6.0")
        devin = self._write_registry(".devin/extensions", CLINE_EXT_ID, "3.7.0")
        location, version = find_extension_in_editor(self.home, "Windsurf", CLINE_EXT_ID)
        self.assertEqual(version, "3.7.0")
        self.assertEqual(location, str(devin / f"{CLINE_EXT_ID}-3.7.0"))

    def test_an_extension_uninstalled_after_migrating_is_not_reported(self):
        """The leftover registry is frozen at migration; it must not resurrect a tool."""
        self._write_registry(".windsurf/extensions", CLINE_EXT_ID, "3.6.0")
        self._write_registry(".devin/extensions", "some.other-extension", "1.0.0")
        self.assertIsNone(find_extension_in_editor(self.home, "Windsurf", CLINE_EXT_ID))

    def test_a_half_migrated_machine_still_reads_the_old_registry(self):
        """The new dir exists but the build has not written a registry into it yet."""
        legacy = self._write_registry(".windsurf/extensions", CLINE_EXT_ID, "3.6.0")
        (self.home / ".devin" / "extensions").mkdir(parents=True)
        location, version = find_extension_in_editor(self.home, "Windsurf", CLINE_EXT_ID)
        self.assertEqual(version, "3.6.0")
        self.assertEqual(location, str(legacy / f"{CLINE_EXT_ID}-3.6.0"))

    def test_an_editor_without_an_alternate_reports_one_outcome(self):
        self.assertIsNone(find_extension_in_editor(self.home, "Code", CLINE_EXT_ID))
        self.assertEqual(["Code:missing"], vscode_registry_state())

    def test_two_dirs_report_the_outcome_that_explains_the_scan(self):
        self._write_registry(".devin/extensions", "some.other-extension", "1.0.0")
        self.assertIsNone(find_extension_in_editor(self.home, "Windsurf", CLINE_EXT_ID))
        self.assertEqual(["Windsurf:present"], vscode_registry_state())

    def test_kilo_code_is_found_inside_devin(self):
        self._write_registry(".devin/extensions", KILO_EXT_ID, "7.7.5")
        _location, version = find_extension_in_editor(self.home, "Windsurf", KILO_EXT_ID)
        self.assertEqual(version, "7.7.5")


class KiloCodeScansItsWindsurfHost(unittest.TestCase):
    """Kilo ships on Open VSX, so it installs into Windsurf/Devin as Cline and Roo do."""

    def test_every_platform_detector_lists_windsurf(self):
        from scripts.coding_discovery_tools.linux.kilocode.kilocode import (
            LinuxKiloCodeDetector,
        )
        from scripts.coding_discovery_tools.macos.kilocode.kilocode import (
            MacOSKiloCodeDetector,
        )
        from scripts.coding_discovery_tools.windows.kilocode.kilocode import (
            WindowsKiloCodeDetector,
        )
        for cls in (LinuxKiloCodeDetector, MacOSKiloCodeDetector, WindowsKiloCodeDetector):
            self.assertIn("Windsurf", cls.SUPPORTED_IDES, cls.__name__)


class HostedMcpSettingsFollowTheRenamedUserDataDir(unittest.TestCase):
    """The editor's user-data dir moved too: ``Application Support/Windsurf`` -> ``Devin``."""

    def _extractor_classes(self):
        from scripts.coding_discovery_tools.linux.cline.mcp_config_extractor import (
            LinuxClineMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.linux.kilocode.mcp_config_extractor import (
            LinuxKiloCodeMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.linux.roo_code.mcp_config_extractor import (
            LinuxRooMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.macos.cline.mcp_config_extractor import (
            MacOSClineMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.macos.kilocode.mcp_config_extractor import (
            MacOSKiloCodeMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.macos.roo_code.mcp_config_extractor import (
            MacOSRooMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.windows.cline.mcp_config_extractor import (
            WindowsClineMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.windows.kilocode.mcp_config_extractor import (
            WindowsKiloCodeMCPConfigExtractor,
        )
        from scripts.coding_discovery_tools.windows.roo_code.mcp_config_extractor import (
            WindowsRooMCPConfigExtractor,
        )
        return (
            LinuxClineMCPConfigExtractor, LinuxKiloCodeMCPConfigExtractor,
            LinuxRooMCPConfigExtractor, MacOSClineMCPConfigExtractor,
            MacOSKiloCodeMCPConfigExtractor, MacOSRooMCPConfigExtractor,
            WindowsClineMCPConfigExtractor, WindowsKiloCodeMCPConfigExtractor,
            WindowsRooMCPConfigExtractor,
        )

    def test_every_hosted_extractor_accepts_both_dir_names(self):
        for cls in self._extractor_classes():
            self.assertIn("Windsurf", cls.IDE_NAMES, cls.__name__)
            self.assertIn("Devin", cls.IDE_NAMES, cls.__name__)

    def _write_cline_settings(self, home, ide_name, servers):
        settings = (home / "Library" / "Application Support" / ide_name / "User"
                    / "globalStorage" / CLINE_EXT_ID / "settings"
                    / "cline_mcp_settings.json")
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({"mcpServers": servers}), encoding="utf-8")

    def _cline_configs(self, home):
        from scripts.coding_discovery_tools.macos.cline.mcp_config_extractor import (
            MacOSClineMCPConfigExtractor,
        )
        return MacOSClineMCPConfigExtractor()._extract_global_configs_for_user(home)

    def test_cline_servers_configured_in_devin_are_extracted(self):
        home = Path(tempfile.mkdtemp())
        self._write_cline_settings(home, "Devin", {"ripgrep": {"command": "rg"}})
        configs = self._cline_configs(home)
        self.assertEqual(1, len(configs), configs)
        self.assertIn("ripgrep", json.dumps(configs))

    def test_a_migrated_machine_reports_its_servers_once(self):
        """Both user-data dirs survive the upgrade; only the live one answers."""
        home = Path(tempfile.mkdtemp())
        for ide in ("Windsurf", "Devin"):
            self._write_cline_settings(home, ide, {"ripgrep": {"command": "rg"}})
        configs = self._cline_configs(home)
        self.assertEqual(1, len(configs), configs)
        self.assertIn("Devin", configs[0]["path"])

    def test_servers_removed_after_migrating_do_not_come_back(self):
        """An emptied settings file still answers; the leftover must not refill it."""
        home = Path(tempfile.mkdtemp())
        self._write_cline_settings(home, "Windsurf", {"ripgrep": {"command": "rg"}})
        self._write_cline_settings(home, "Devin", {})
        self.assertEqual([], self._cline_configs(home))

    def test_an_unreadable_live_config_falls_back_to_the_old_one(self):
        """Being denied the file tells us nothing, so the old copy is still the answer."""
        home = Path(tempfile.mkdtemp())
        self._write_cline_settings(home, "Windsurf", {"ripgrep": {"command": "rg"}})
        self._write_cline_settings(home, "Devin", {"ripgrep": {"command": "rg"}})
        devin = (home / "Library" / "Application Support" / "Devin" / "User"
                 / "globalStorage" / CLINE_EXT_ID / "settings"
                 / "cline_mcp_settings.json")
        os.chmod(devin, 0)
        try:                        # Windows and root ignore the mode bits
            with open(devin, "rb"):
                self.skipTest("this platform cannot make a file unreadable")
        except OSError:
            pass
        configs = self._cline_configs(home)
        self.assertEqual(1, len(configs), configs)
        self.assertIn("Windsurf", configs[0]["path"])

    def test_a_half_migrated_machine_keeps_its_old_servers(self):
        """The new user-data dir can exist before the extension writes settings in it."""
        home = Path(tempfile.mkdtemp())
        base = home / "Library" / "Application Support"
        (base / "Devin" / "User" / "globalStorage").mkdir(parents=True)
        self._write_cline_settings(home, "Windsurf", {"ripgrep": {"command": "rg"}})
        configs = self._cline_configs(home)
        self.assertEqual(1, len(configs), configs)
        self.assertIn("Windsurf", configs[0]["path"])


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheEditorIsNamedForItsRebrand(unittest.TestCase):
    """The tool reports its current name; rows written under the old one still resolve."""

    def test_every_platform_detector_reports_the_new_name(self):
        from scripts.coding_discovery_tools.linux.windsurf.windsurf import (
            LinuxWindsurfDetector,
        )
        from scripts.coding_discovery_tools.macos.windsurf.windsurf import (
            MacOSWindsurfDetector,
        )
        from scripts.coding_discovery_tools.windows.windsurf.windsurf import (
            WindowsWindsurfDetector,
        )
        for cls in (LinuxWindsurfDetector, MacOSWindsurfDetector, WindowsWindsurfDetector):
            self.assertEqual("Devin Desktop", cls.tool_name.fget(cls), cls.__name__)

    def test_a_hosted_row_names_the_editor_by_its_new_name(self):
        from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
        from scripts.coding_discovery_tools.macos.roo_code.roo_code import MacOSRooDetector
        for cls in (MacOSClineDetector, MacOSRooDetector):
            self.assertEqual("Devin Desktop", cls.SUPPORTED_IDES["Windsurf"], cls.__name__)

    def test_a_row_written_before_the_rename_still_resolves(self):
        """Stored rows read "(Windsurf)"; they must map to the same editor."""
        from scripts.coding_discovery_tools.vscode_extension_helpers import (
            vscode_family_editor_dirs,
        )
        self.assertEqual(["Windsurf"], vscode_family_editor_dirs("GitHub Copilot (Windsurf)"))
        self.assertEqual(["Windsurf"],
                         vscode_family_editor_dirs("GitHub Copilot (Devin Desktop)"))
