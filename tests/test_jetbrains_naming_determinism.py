"""Tests for shared JetBrains folder naming/skip rules (WEB-5391).

Two defects this locks down:
  1. SKIP_FOLDERS was matched by exact equality, so version-suffixed system
     folders like "JetBrainsClient241.18034.62" were never skipped.
  2. Unmapped products fell back to `(folder_name, "Unknown")`, so a new IDE
     was reported under its raw config folder name with no version.

unittest, not pytest: CI runs `python -m unittest discover -s tests -t .`.
"""

import tempfile
import time
import unittest
from pathlib import Path
from typing import Iterable
from unittest import mock

from scripts.coding_discovery_tools.jetbrains_naming_helpers import (
    VERSION_SUFFIX,
    should_skip_folder,
    version_sort_key,
)
from scripts.coding_discovery_tools.linux.github_copilot.copilot_rules_extractor import (
    LinuxGitHubCopilotRulesExtractor,
)
from scripts.coding_discovery_tools.linux.jetbrains.jetbrains import LinuxJetBrainsDetector
from scripts.coding_discovery_tools.linux.jetbrains.mcp_config_extractor import (
    LinuxJetBrainsMCPConfigExtractor,
)
from scripts.coding_discovery_tools.macos.github_copilot.copilot_rules_extractor import (
    MacOSGitHubCopilotRulesExtractor,
)
from scripts.coding_discovery_tools.macos.jetbrains import jetbrains as jetbrains_macos
from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from scripts.coding_discovery_tools.macos.jetbrains.mcp_config_extractor import (
    MacOSJetBrainsMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.github_copilot.copilot_rules_extractor import (
    WindowsGitHubCopilotRulesExtractor,
)
from scripts.coding_discovery_tools.windows.jetbrains.jetbrains import WindowsJetBrainsDetector
from scripts.coding_discovery_tools.windows.jetbrains.mcp_config_extractor import (
    WindowsJetBrainsMCPConfigExtractor,
)

DETECTORS = [MacOSJetBrainsDetector, LinuxJetBrainsDetector, WindowsJetBrainsDetector]

NAMING_TABLE = [
    ("Aqua2024.3", "Aqua", "2024.3"),
    ("Aqua2023.3", "Aqua", "2023.3"),
    ("IdeaIE2022.2", "IntelliJ IDEA Educational", "2022.2"),
    ("IntelliJIdea2025.2", "IntelliJ IDEA", "2025.2"),
    ("IdeaIC2024.1", "IntelliJ IDEA Community", "2024.1"),
    ("PyCharmCE2024.1", "PyCharm Community", "2024.1"),
    ("PyCharm2025.2", "PyCharm", "2025.2"),
    ("Fleet1.0", "Fleet", "1.0"),
    ("Writerside2024.1", "Writerside", "2024.1"),
    ("MPS2023.3", "MPS", "2023.3"),
    ("DataSpell", "DataSpell", "Unknown"),
    ("RustRover2025.1", "RustRover", "2025.1"),
    # Resolves via the mapping prefix, not the dotted-version regex.
    ("Rider2024", "Rider", "2024"),
    ("JetBrainsGateway2025.1", "JetBrainsGateway", "2025.1"),
    # Separator between name and version is stripped, not kept.
    ("Space Desktop 1.0", "Space Desktop", "1.0"),
    ("Big_Data_Tools_2024.1", "Big_Data_Tools", "2024.1"),
    # Unmapped and unversioned: nothing better to report than the raw folder name.
    ("RemoteDev-IU", "RemoteDev-IU", "Unknown"),
    # Own identity, or _filter_old_versions drops one of the two installs.
    ("PyCharmEdu2024.1", "PyCharmEdu", "2024.1"),
    ("CLionNova2024.3", "CLionNova", "2024.3"),
    # A decorated version still belongs to the mapped product.
    ("IntelliJIdea2024.1-EAP", "IntelliJ IDEA", "2024.1-EAP"),
    # ...but a decorated edition is still its own product, not the base one.
    ("PyCharmEdu2024.1-EAP", "PyCharmEdu", "2024.1-EAP"),
    ("CLionNova2024.3-RC", "CLionNova", "2024.3-RC"),
]

SKIP_TABLE = [
    ("JetBrainsClient241.18034.62", True),
    ("JetBrainsClient", True),
    ("Toolbox2.1.3", True),
    ("consent", True),
    ("DeviceId", True),
    ("consentOptions", True),
    ("PrivacyPolicy", True),
    # Accepted over-skip: prefix matching also catches these hypothetical products.
    ("DeviceIdentityManager2024.1", True),
    ("ToolboxIDE2025.1", True),
    ("MyToolbox2024.1", False),
    ("IntelliJIdea2025.2", False),
    ("Aqua2024.3", False),
    ("JetBrainsGateway2025.1", False),
]

FREE_PLAN_FOLDERS = ["IdeaIE2022.2", "IdeaIC2024.1", "PyCharmCE2024.1"]

FIXTURE_FOLDERS = ["Aqua2023.3", "Aqua2024.3", "JetBrainsClient241.18034.62", "Fleet1.0"]
EXPECTED_SCAN = {("Aqua", "2024.3"), ("Fleet", "1.0")}


def _make_config_dir(root: Path, folders: Iterable[str]) -> Path:
    """Create JetBrains config subfolders, each with the options/ dir detection gates on."""
    root.mkdir(parents=True, exist_ok=True)
    for folder in folders:
        (root / folder / "options").mkdir(parents=True)
    return root


class _TempHomeTestCase(unittest.TestCase):
    """Gives each test a throwaway directory to stand in for a user home."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.tmp_path = Path(temp_dir.name)


class TestNameAndVersionParsing(unittest.TestCase):

    def test_parse_ide_name_and_version(self) -> None:
        for detector_cls in DETECTORS:
            for folder, expected_name, expected_version in NAMING_TABLE:
                with self.subTest(folder=folder, detector=detector_cls.__name__):
                    self.assertEqual(
                        detector_cls()._parse_ide_name_and_version(folder),
                        (expected_name, expected_version),
                    )

    def test_all_detectors_agree(self) -> None:
        for folder, expected_name, expected_version in NAMING_TABLE:
            with self.subTest(folder=folder):
                results = {cls()._parse_ide_name_and_version(folder) for cls in DETECTORS}
                self.assertEqual(results, {(expected_name, expected_version)})


class TestSkipFolderMatching(unittest.TestCase):

    def test_skip_folder_matching(self) -> None:
        for detector_cls in DETECTORS:
            for folder, expected in SKIP_TABLE:
                with self.subTest(folder=folder, detector=detector_cls.__name__):
                    self.assertIs(should_skip_folder(folder, detector_cls.SKIP_FOLDERS), expected)

    def test_bare_string_skip_list_is_one_prefix_not_its_characters(self) -> None:
        self.assertIs(should_skip_folder("Toolbox2.1.3", "Toolbox"), True)
        self.assertIs(should_skip_folder("Terminal2024.1", "Toolbox"), False)


class TestPlanDetection(unittest.TestCase):

    def test_all_detectors_agree_on_plan(self) -> None:
        for detector_cls in DETECTORS:
            for folder in FREE_PLAN_FOLDERS:
                with self.subTest(folder=folder, detector=detector_cls.__name__):
                    self.assertEqual(detector_cls()._detect_plan(folder), "Free")
            for folder in ["IntelliJIdea2025.2", "PyCharm2025.2", "Aqua2024.3", "Fleet1.0"]:
                with self.subTest(folder=folder, detector=detector_cls.__name__):
                    self.assertEqual(detector_cls()._detect_plan(folder), "Licensed")


class TestConfigDirScan(_TempHomeTestCase):

    def test_macos_scan_skips_system_folders_and_names_unmapped_ides(self) -> None:
        _make_config_dir(
            self.tmp_path / "Library" / "Application Support" / "JetBrains", FIXTURE_FOLDERS
        )
        detector = MacOSJetBrainsDetector()

        found = MacOSJetBrainsDetector._filter_old_versions(
            detector._scan_jetbrains_config_dir(self.tmp_path)
        )

        self.assertEqual({(ide["display_name"], ide["version"]) for ide in found}, EXPECTED_SCAN)

    def test_linux_scan_skips_system_folders_and_names_unmapped_ides(self) -> None:
        _make_config_dir(self.tmp_path / ".config" / "JetBrains", FIXTURE_FOLDERS)
        detector = LinuxJetBrainsDetector()

        found = LinuxJetBrainsDetector._filter_old_versions(
            detector._scan_jetbrains_config_dir(self.tmp_path)
        )

        self.assertEqual({(ide["display_name"], ide["version"]) for ide in found}, EXPECTED_SCAN)

    def test_windows_scan_skips_system_folders_and_names_unmapped_ides(self) -> None:
        config_dir = _make_config_dir(self.tmp_path / "Roaming" / "JetBrains", FIXTURE_FOLDERS)
        detector = WindowsJetBrainsDetector()

        found = detector._scan_for_ides(config_dir)

        self.assertEqual({(ide["display_name"], ide["version"]) for ide in found}, EXPECTED_SCAN)


class TestUnreadableConfigDir(_TempHomeTestCase):
    """``Path.exists()`` raises on an unreadable parent rather than returning False,
    so probing another user's home on a non-root scan threw out of the detector."""

    SETTINGS_DIRS = {
        MacOSJetBrainsDetector: Path("Library") / "Application Support",
        LinuxJetBrainsDetector: Path(".config"),
    }

    def test_access_denied_is_not_a_scan_failure(self) -> None:
        for detector_cls, settings_dir in self.SETTINGS_DIRS.items():
            with self.subTest(detector=detector_cls.__name__):
                home = self.tmp_path / detector_cls.__name__
                _make_config_dir(home / settings_dir / "JetBrains", ["Rider2025.2"])

                with mock.patch.object(
                    Path, "exists", side_effect=PermissionError(13, "Permission denied")
                ):
                    self.assertEqual([], detector_cls()._scan_jetbrains_config_dir(home))


class TestAndroidStudioVendorDir(_TempHomeTestCase):
    """Android Studio is an IntelliJ-platform IDE, but Google ships it under its
    own vendor dir, so a JetBrains-only root never saw it."""

    SETTINGS_DIRS = {
        MacOSJetBrainsDetector: Path("Library") / "Application Support",
        LinuxJetBrainsDetector: Path(".config"),
    }

    def test_android_studio_is_found_beside_jetbrains_ides(self) -> None:
        for detector_cls, settings_dir in self.SETTINGS_DIRS.items():
            with self.subTest(detector=detector_cls.__name__):
                home = self.tmp_path / detector_cls.__name__
                _make_config_dir(home / settings_dir / "JetBrains", ["IntelliJIdea2025.2"])
                _make_config_dir(home / settings_dir / "Google", ["AndroidStudio2025.2.3"])

                found = detector_cls()._scan_jetbrains_config_dir(home)

                self.assertEqual(
                    {(ide["display_name"], ide["version"]) for ide in found},
                    {("IntelliJ IDEA", "2025.2"), ("Android Studio", "2025.2.3")},
                )

    def test_windows_scan_covers_both_vendor_dirs(self) -> None:
        roaming = self.tmp_path / "AppData" / "Roaming"
        _make_config_dir(roaming / "JetBrains", ["IntelliJIdea2025.2"])
        _make_config_dir(roaming / "Google", ["AndroidStudio2025.2.3"])
        detector = WindowsJetBrainsDetector()
        detector.user_home = self.tmp_path

        found = detector._scan_all_config_dirs()

        self.assertEqual(
            {(ide["display_name"], ide["version"]) for ide in found},
            {("IntelliJ IDEA", "2025.2"), ("Android Studio", "2025.2.3")},
        )

    def test_missing_google_dir_is_not_an_error(self) -> None:
        """The vendor dir is absent on every machine without Android Studio."""
        _make_config_dir(
            self.tmp_path / "Library" / "Application Support" / "JetBrains", ["PyCharm2025.2"]
        )

        found = MacOSJetBrainsDetector()._scan_jetbrains_config_dir(self.tmp_path)

        self.assertEqual({ide["display_name"] for ide in found}, {"PyCharm"})

    def test_mcp_extractors_do_not_filter_out_android_studio(self) -> None:
        """The extractors gate folders on IDE_PATTERNS, a list separate from the
        detector's name mapping, so it has to know Android Studio too."""
        for extractor_cls in (
            MacOSJetBrainsMCPConfigExtractor,
            LinuxJetBrainsMCPConfigExtractor,
            WindowsJetBrainsMCPConfigExtractor,
        ):
            with self.subTest(extractor=extractor_cls.__name__):
                patterns = extractor_cls.IDE_PATTERNS
                self.assertTrue(
                    any(p in "AndroidStudio2026.1.4" for p in patterns),
                    f"{extractor_cls.__name__} would skip the Android Studio config folder",
                )

    def test_copilot_rules_extractors_treat_android_studio_as_jetbrains(self) -> None:
        """Global JetBrains Copilot rules live at a shared, IDE-agnostic path, but
        are only read when the tool name is recognised as a JetBrains IDE."""
        for extractor_cls in (
            MacOSGitHubCopilotRulesExtractor,
            LinuxGitHubCopilotRulesExtractor,
            WindowsGitHubCopilotRulesExtractor,
        ):
            with self.subTest(extractor=extractor_cls.__name__):
                self.assertTrue(
                    extractor_cls()._is_jetbrains_tool("GitHub Copilot (Android Studio)")
                )


class TestPrefixCollisionSurvivesFiltering(_TempHomeTestCase):

    def test_edu_edition_is_not_dropped_alongside_regular_install(self) -> None:
        _make_config_dir(
            self.tmp_path / "Library" / "Application Support" / "JetBrains",
            ["PyCharmEdu2024.1", "PyCharm2025.2", "CLionNova2024.3", "CLion2023.1"],
        )
        detector = MacOSJetBrainsDetector()

        found = MacOSJetBrainsDetector._filter_old_versions(
            detector._scan_jetbrains_config_dir(self.tmp_path)
        )

        self.assertEqual(
            {(ide["display_name"], ide["version"]) for ide in found},
            {
                ("PyCharmEdu", "2024.1"),
                ("PyCharm", "2025.2"),
                ("CLionNova", "2024.3"),
                ("CLion", "2023.1"),
            },
        )


class TestConsentResidueIsNotAnIde(_TempHomeTestCase):

    def test_bare_product_folder_without_config_is_dropped(self) -> None:
        jetbrains = self.tmp_path / "Library" / "Application Support" / "JetBrains"
        _make_config_dir(jetbrains, ["GoLand2025.1"])
        # What JetBrains leaves behind after an uninstall: product name, consent data only.
        for residue in ["GoLand", "PyCharm", "Clion", "consentOptions"]:
            (jetbrains / residue / "localConsents").mkdir(parents=True)
        detector = MacOSJetBrainsDetector()

        found = detector._scan_jetbrains_config_dir(self.tmp_path)

        self.assertEqual({ide["display_name"] for ide in found}, {"GoLand"})
        self.assertEqual([ide["version"] for ide in found], ["2025.1"])

    def test_versionless_folder_with_real_config_still_detected(self) -> None:
        _make_config_dir(
            self.tmp_path / "Library" / "Application Support" / "JetBrains", ["DataSpell"]
        )
        detector = MacOSJetBrainsDetector()

        found = detector._scan_jetbrains_config_dir(self.tmp_path)

        self.assertEqual(
            [(ide["display_name"], ide["version"]) for ide in found], [("DataSpell", "Unknown")]
        )


class TestPerUserVersionFiltering(unittest.TestCase):

    def test_one_users_newer_ide_does_not_evict_anothers(self) -> None:
        def install(version: str, user: str) -> dict:
            return {
                "folder_name": "PyCharm" + version,
                "display_name": "PyCharm",
                "version": version,
                "plan": "Licensed",
                "config_path": "/Users/{}/PyCharm{}".format(user, version),
            }

        alice = MacOSJetBrainsDetector._filter_old_versions([install("2024.1", "alice")])
        bob = MacOSJetBrainsDetector._filter_old_versions([install("2025.2", "bob")])

        self.assertEqual(
            {ide["config_path"] for ide in alice + bob},
            {"/Users/alice/PyCharm2024.1", "/Users/bob/PyCharm2025.2"},
        )

    def test_prerelease_does_not_lose_to_the_stable_it_supersedes(self) -> None:
        """A non-numeric segment used to be dropped, so 2025.2-EAP scored (2025,)
        and lost to 2025.1 — reporting the old version and skipping the EAP's plugins."""
        ides = [
            {"display_name": "Rider", "version": "2025.1"},
            {"display_name": "Rider", "version": "2025.2-EAP"},
        ]
        for detector_cls in DETECTORS:
            with self.subTest(detector=detector_cls.__name__):
                kept = detector_cls._filter_old_versions(list(ides))
                self.assertEqual(["2025.2-EAP"], [ide["version"] for ide in kept])


    def test_same_number_stable_beats_eap_whatever_the_scan_order(self) -> None:
        """Config dirs arrive in os.listdir order, so a tie would resolve by
        filesystem layout and could drop the stable install's plugins."""
        stable = {"display_name": "Rider", "version": "2025.2"}
        eap = {"display_name": "Rider", "version": "2025.2-EAP"}
        for order in ([stable, eap], [eap, stable]):
            for detector_cls in DETECTORS:
                with self.subTest(detector=detector_cls.__name__,
                                  order=[i["version"] for i in order]):
                    kept = detector_cls._filter_old_versions(list(order))
                    self.assertEqual(["2025.2"], [ide["version"] for ide in kept])


class TestVersionSortKey(unittest.TestCase):

    def test_orders_versions_newest_highest(self) -> None:
        self.assertLess(version_sort_key("2024.3"), version_sort_key("2025.1"))
        self.assertLess(version_sort_key("2025.1"), version_sort_key("2025.2-EAP"))
        self.assertLess(version_sort_key("2025.2-EAP"), version_sort_key("2025.2"))
        self.assertLess(version_sort_key("2025.2"), version_sort_key("2025.2.3"))

    def test_unparseable_sorts_lowest(self) -> None:
        for version in ("Unknown", "", "EAP"):
            with self.subTest(version=version):
                self.assertLess(version_sort_key(version), version_sort_key("1.0"))


class TestRootScanDoesNotRescanHome(_TempHomeTestCase):

    def test_invoking_users_home_is_scanned_once(self) -> None:
        users = self.tmp_path / "Users"
        home = users / "alice"
        _make_config_dir(home / "Library" / "Application Support" / "JetBrains", ["PyCharm2025.2"])
        detector = MacOSJetBrainsDetector()

        with mock.patch.object(jetbrains_macos, "is_running_as_root", return_value=True), \
                mock.patch.object(jetbrains_macos, "Path", wraps=Path) as patched_path:
            patched_path.side_effect = lambda *a: users if a == ("/Users",) else Path(*a)
            patched_path.home.return_value = home
            found = detector._scan_for_ides()

        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["display_name"], "PyCharm")


class TestVersionSuffixRegex(unittest.TestCase):

    def test_version_suffix_regex_does_not_backtrack(self) -> None:
        pathological = "A" + "-" * 255 + "1.1." * 255

        start = time.monotonic()
        VERSION_SUFFIX.match(pathological)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.1)


if __name__ == "__main__":
    unittest.main()
