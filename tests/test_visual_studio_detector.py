r"""Visual Studio + Copilot-in-Visual-Studio detection.

Visual Studio installs machine-wide under `%ProgramData%\Microsoft\VisualStudio\
Packages\_Instances\<id>\state.json` and ships Copilot as an Installer component
rather than a marketplace extension, so both signals live in one JSON file.

No real state.json has been read yet — these fixtures follow the documented schema
(learn.microsoft.com/visualstudio/install/tools-for-managing-visual-studio-instances),
which carries no `displayName`, so the row name must compose from `product.id`.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.windows_extraction_helpers as helpers
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.coding_tool_factory import (
    ToolDetectorFactory,
    VisualStudioMCPConfigExtractorFactory,
    VisualStudioRulesExtractorFactory,
)
from scripts.coding_discovery_tools.windows.visual_studio.visual_studio import (
    COPILOT_TOOL_NAME,
    WindowsVisualStudioDetector,
    _copilot_version,
    _display_name,
    _is_reportable,
    _version_tuple,
)

ENTERPRISE = "Microsoft.VisualStudio.Product.Enterprise"
COMMUNITY = "Microsoft.VisualStudio.Product.Community"
BUILD_TOOLS = "Microsoft.VisualStudio.Product.BuildTools"


def state(version="17.14.3", product=ENTERPRISE, copilot=True):
    """A documented-shape instance state file."""
    packages = [{"id": "Microsoft.VisualStudio.Workload.CoreEditor", "version": version}]
    if copilot:
        packages.append({"id": "Component.GitHub.Copilot", "version": "17.14.1"})
    return {
        "channelId": "VisualStudio.17.Release",
        "installationName": f"VisualStudio/{version}",
        "installationVersion": version,
        "installationPath": r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise",
        "installDate": "2026-01-18T03:45:00Z",
        "product": {"id": product, "version": version, "type": "Product"},
        "packages": packages,
    }


class VersionFilterTests(unittest.TestCase):
    def test_17_9_sorts_below_17_10(self):
        """A string compare puts "17.9" above "17.10" and would drop the entire
        17.10-17.99 population this detector exists to find."""
        self.assertLess(_version_tuple("17.9.5"), _version_tuple("17.10.0"))
        self.assertGreater("17.9.5", "17.10.0")  # the trap being avoided

    def test_malformed_version_sorts_earliest(self):
        self.assertEqual(_version_tuple("not-a-version"), ())
        self.assertEqual(_version_tuple(None), ())

    def test_only_17_10_and_newer_is_reportable(self):
        self.assertTrue(_is_reportable(state("17.10.0")))
        self.assertTrue(_is_reportable(state("18.7.1")))
        self.assertFalse(_is_reportable(state("17.9.9")))

    def test_build_tools_is_never_reported(self):
        """No IDE, no Copilot — a CI agent must not land in the device inventory."""
        self.assertFalse(_is_reportable(state("17.14.3", product=BUILD_TOOLS)))


class RowNameTests(unittest.TestCase):
    def test_edition_and_year_compose_the_name(self):
        self.assertEqual(_display_name(state("17.14.3")), "Visual Studio 2022 Enterprise")
        self.assertEqual(_display_name(state("18.7.1", product=COMMUNITY)), "Visual Studio 2026 Community")

    def test_name_is_deterministic_when_keys_are_missing(self):
        """The name is half of the install key, so a name that varies between runs
        produces duplicate rows and prune thrash."""
        self.assertEqual(_display_name({}), "Visual Studio")
        self.assertEqual(_display_name({"product": {}}), "Visual Studio")


class CopilotComponentTests(unittest.TestCase):
    def test_component_version_is_read_from_packages(self):
        self.assertEqual(_copilot_version(state()), "17.14.1")

    def test_absent_component_returns_none(self):
        self.assertIsNone(_copilot_version(state(copilot=False)))

    def test_malformed_packages_do_not_raise(self):
        self.assertIsNone(_copilot_version({"packages": "not-a-list"}))
        self.assertIsNone(_copilot_version({"packages": [None, 3, {"id": None}]}))


class DetectTests(unittest.TestCase):
    def setUp(self):
        utils_mod.reset_sentry_run_state()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.program_data = root / "ProgramData"
        self.instances = self.program_data / "Microsoft" / "VisualStudio" / "Packages" / "_Instances"
        self.instances.mkdir(parents=True)
        self.home = root / "Users" / "nanda"
        self.home.mkdir(parents=True)
        self.detector = WindowsVisualStudioDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        self._tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def write_instance(self, name="abc123", **kwargs):
        instance = self.instances / name
        instance.mkdir(parents=True, exist_ok=True)
        (instance / "state.json").write_text(json.dumps(state(**kwargs)), encoding="utf-8")

    def give_user_a_vs_config(self, version_dir="17.14_9a8b7c"):
        target = self.home / "AppData" / "Local" / "Microsoft" / "VisualStudio" / version_dir
        target.mkdir(parents=True)
        return target

    def detect(self):
        with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
            return self.detector.detect()

    def test_ide_row_and_copilot_row(self):
        self.write_instance()
        user_dir = self.give_user_a_vs_config()
        rows = self.detect()
        self.assertEqual(len(rows), 2)
        ide, copilot = rows
        self.assertEqual(ide["name"], "Visual Studio 2022 Enterprise")
        self.assertEqual(ide["version"], "17.14.3")
        self.assertEqual(ide["plugins"], ["GitHub Copilot"])
        self.assertEqual(copilot["name"], COPILOT_TOOL_NAME)
        self.assertEqual(copilot["version"], "17.14.1")

    def test_copilot_row_carries_the_per_user_path(self):
        """Machine-wide installationPath would fan the row out identically to every
        profile under a root scan, with nothing to disown it."""
        self.write_instance()
        user_dir = self.give_user_a_vs_config()
        ide, copilot = self.detect()
        self.assertEqual(copilot["install_path"], str(user_dir))
        self.assertNotEqual(ide["install_path"], copilot["install_path"])

    def test_ide_only_when_the_component_is_absent(self):
        self.write_instance(copilot=False)
        self.give_user_a_vs_config()
        rows = self.detect()
        self.assertEqual([row["name"] for row in rows], ["Visual Studio 2022 Enterprise"])
        self.assertEqual(rows[0]["plugins"], [])

    def test_user_without_a_vs_config_gets_nothing(self):
        """A machine-wide install must not be attributed to every profile on a
        shared box."""
        self.write_instance()
        self.assertIsNone(self.detect())

    def test_older_and_build_tools_instances_are_skipped(self):
        self.write_instance("old", version="17.9.9")
        self.write_instance("bt", product=BUILD_TOOLS)
        self.give_user_a_vs_config()
        self.assertIsNone(self.detect())

    def test_unreadable_never_raises(self):
        """A single raising detector marks the whole scan incomplete, which disables
        pruning for every tool on the device (test_scan_completed_manifest.py:682)."""
        self.give_user_a_vs_config()
        with patch.object(Path, "iterdir", side_effect=PermissionError("denied")):
            with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
                self.assertIsNone(self.detector.detect())  # must not raise

    def test_malformed_state_json_is_skipped_not_fatal(self):
        instance = self.instances / "broken"
        instance.mkdir()
        (instance / "state.json").write_text("{not json", encoding="utf-8")
        self.write_instance("good")
        self.give_user_a_vs_config()
        rows = self.detect()
        self.assertEqual(len(rows), 2)
        self.assertIn("state_json:malformed", utils_mod.vs_probes())

    def test_probe_records_the_missing_half_of_the_gate(self):
        self.write_instance()
        self.detect()
        self.assertIn("user_config:absent", utils_mod.vs_probes())


class SharedWalkTests(unittest.TestCase):
    """`.vs` and `.vscode` are both in SKIP_DIRS and sit in the same project trees,
    so one pass collects both — a walk per consumer would traverse the whole drive
    twice, and WEB-4755 was a 600s timeout on a single traversal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        helpers.reset_workspace_config_dirs()

    def tearDown(self):
        self._tmp.cleanup()
        helpers.reset_workspace_config_dirs()

    def walk(self, start):
        found = {leaf: [] for leaf in helpers._WORKSPACE_CONFIG_LEAVES}
        helpers._walk_workspace_config_dirs(self.root, start, found, set(), current_depth=1)
        return found

    def test_both_leaves_are_collected_in_one_pass(self):
        repo = self.root / "Users" / "nanda" / "repo"
        (repo / ".vs").mkdir(parents=True)
        (repo / ".vscode").mkdir(parents=True)
        found = self.walk(self.root / "Users")
        self.assertEqual(found[".vs"], [repo / ".vs"])
        self.assertEqual(found[".vscode"], [repo / ".vscode"])

    def test_leaf_exemption_does_not_expose_skipped_parents(self):
        """Exempting the leaf must not reach a `.vs` beneath node_modules/.git/venv."""
        for parent in ("node_modules", ".git", "venv"):
            (self.root / "Users" / "nanda" / "repo" / parent / "pkg" / ".vs").mkdir(parents=True)
        self.assertEqual(self.walk(self.root / "Users")[".vs"], [])

    def test_symlinked_leaf_is_skipped(self):
        """A `.vs` symlink must not pull an out-of-tree solution into the project."""
        outside = self.root / "outside" / ".vs"
        outside.mkdir(parents=True)
        repo = self.root / "Users" / "nanda" / "repo"
        repo.mkdir(parents=True)
        (repo / ".vs").symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.walk(self.root / "Users")[".vs"], [])


class DispatchTests(unittest.TestCase):
    """The row name ends in an unrecognised "(...)" suffix, which in the substring
    branch resolves to want_jetbrains rather than to nothing — so without an exact
    match the row would inherit JetBrains Copilot's intellij/mcp.json servers."""

    def setUp(self):
        self.detector = object.__new__(AIToolsDetector)
        self.detector._visual_studio_mcp_extractor = None
        self.detector._visual_studio_rules_extractor = None
        # Any touch of these means the row fell through to the Copilot branch.
        self.detector._github_copilot_mcp_extractor = self.fail_if_used()
        self.detector._github_copilot_rules_extractor = self.fail_if_used()

    def fail_if_used(self):
        test = self

        class Tripwire:
            def __getattr__(self, name):
                test.fail(f"Visual Studio row reached the GitHub Copilot extractor ({name})")

        return Tripwire()

    def test_visual_studio_row_does_not_reach_the_copilot_branch(self):
        row = self.detector._process_single_tool_raw({
            "name": COPILOT_TOOL_NAME,
            "version": "17.14.1",
            "install_path": r"C:\Users\nanda\AppData\Local\Microsoft\VisualStudio\17.14_9a8b7c",
        })
        self.assertEqual(row["name"], COPILOT_TOOL_NAME)
        self.assertEqual(row["projects"], [])


class FactoryTests(unittest.TestCase):
    def test_windows_only(self):
        """Visual Studio for Mac retired 2024-08-31 and never shipped for Linux.
        A raising factory would crash every macOS and Linux scan at startup."""
        for os_name in ("Darwin", "Linux", "SunOS"):
            self.assertIsNone(ToolDetectorFactory.create_visual_studio_detector(os_name))
            self.assertIsNone(VisualStudioMCPConfigExtractorFactory.create(os_name))
            self.assertIsNone(VisualStudioRulesExtractorFactory.create(os_name))

    def test_registered_in_the_full_detector_list(self):
        names = [type(d).__name__ for d in ToolDetectorFactory.create_all_tool_detectors("Windows")]
        self.assertIn("WindowsVisualStudioDetector", names)

    def test_not_registered_off_windows(self):
        names = [type(d).__name__ for d in ToolDetectorFactory.create_all_tool_detectors("Darwin")]
        self.assertNotIn("WindowsVisualStudioDetector", names)


if __name__ == "__main__":
    unittest.main()
