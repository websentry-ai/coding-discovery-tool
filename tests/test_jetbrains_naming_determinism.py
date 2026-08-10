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

from scripts.coding_discovery_tools.jetbrains_naming_helpers import (
    VERSION_SUFFIX,
    should_skip_folder,
)
from scripts.coding_discovery_tools.linux.jetbrains.jetbrains import LinuxJetBrainsDetector
from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import MacOSJetBrainsDetector
from scripts.coding_discovery_tools.windows.jetbrains.jetbrains import WindowsJetBrainsDetector

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
    # Unmapped AND unversioned (JetBrains Remote Development) still falls back
    # to the raw folder name; there is nothing better to report.
    ("RemoteDev-IU", "RemoteDev-IU", "Unknown"),
    # Distinct products that merely start with a mapped prefix keep their own
    # identity, or _filter_old_versions would drop one of the two installs.
    ("PyCharmEdu2024.1", "PyCharmEdu", "2024.1"),
    ("CLionNova2024.3", "CLionNova", "2024.3"),
    # No clean split available, so the branded name is kept rather than lost.
    ("IntelliJIdea2024.1-EAP", "IntelliJ IDEA", "2024.1-EAP"),
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


class TestPlanDetection(_TempHomeTestCase):

    def test_free_plan_unix(self) -> None:
        for detector_cls in [MacOSJetBrainsDetector, LinuxJetBrainsDetector]:
            for folder in FREE_PLAN_FOLDERS:
                with self.subTest(folder=folder, detector=detector_cls.__name__):
                    self.assertEqual(detector_cls()._detect_plan(folder), "Free")

    def test_community_plan_windows(self) -> None:
        # Windows says "Community" where macOS/Linux say "Free"; the vocabulary
        # divergence is deliberate and out of scope for WEB-5391.
        plan = WindowsJetBrainsDetector()._detect_plan("IdeaIE2022.2", self.tmp_path)
        self.assertEqual(plan, "Community")


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

        found = detector._scan_for_ides(config_dir, self.tmp_path / "Local" / "JetBrains")

        self.assertEqual({(ide["display_name"], ide["version"]) for ide in found}, EXPECTED_SCAN)


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


class TestVersionSuffixRegex(unittest.TestCase):

    def test_version_suffix_regex_does_not_backtrack(self) -> None:
        pathological = "A" + "-" * 255 + "1.1." * 255

        start = time.monotonic()
        VERSION_SUFFIX.match(pathological)
        elapsed = time.monotonic() - start

        self.assertLess(elapsed, 0.1)


if __name__ == "__main__":
    unittest.main()
