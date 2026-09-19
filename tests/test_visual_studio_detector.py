r"""Visual Studio + Copilot-in-Visual-Studio detection.

Visual Studio installs machine-wide under `%ProgramData%\Microsoft\VisualStudio\
Packages\_Instances\<id>\state.json` and ships Copilot as an Installer component
rather than a marketplace extension, so both signals live in one JSON file.

Fixtures mirror a real file, captured from VS 2022 17.14.37710.0 (Community,
channel `VisualStudio.17.Release`). Two things the published documentation gets
wrong and only a live machine revealed:

  * real `state.json` has **no** `packages` key — components live in
    `selectedPackages`, while microsoft/vswhere's published sample has `packages`;
  * the Copilot component id is `Component.VisualStudio.GitHub.Copilot`, not the
    `Component.GitHub.Copilot` the enterprise-deploy doc passes to `--add`. That
    id is absent from the catalog, so the installer accepts it, installs nothing,
    and exits 0.

`displayName` really is absent, so the row name still composes from `product.id`.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.windows_extraction_helpers as helpers
import scripts.coding_discovery_tools.windows.visual_studio.visual_studio as vs_mod
import scripts.coding_discovery_tools.windows.visual_studio.mcp_config_extractor as vs_mcp
from scripts.coding_discovery_tools.windows.visual_studio.mcp_config_extractor import (
    WindowsVisualStudioMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.visual_studio.visual_studio_rules_extractor import (
    WindowsVisualStudioRulesExtractor,
)
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.mcp_extraction_helpers import read_mcp_json
from scripts.coding_discovery_tools.coding_tool_factory import (
    ToolDetectorFactory,
    VisualStudioMCPConfigExtractorFactory,
    VisualStudioRulesExtractorFactory,
)
from scripts.coding_discovery_tools.windows.visual_studio.visual_studio import (
    COPILOT_TOOL_NAME,
    WindowsVisualStudioDetector,
    _copilot_package,
    _display_name,
    _from_vswhere,
    _is_reportable,
    _version_text,
    _version_tuple,
)

ENTERPRISE = "Microsoft.VisualStudio.Product.Enterprise"
COMMUNITY = "Microsoft.VisualStudio.Product.Community"
BUILD_TOOLS = "Microsoft.VisualStudio.Product.BuildTools"


COPILOT_COMPONENT = "Component.VisualStudio.GitHub.Copilot"


def state(version="17.14.3", product=ENTERPRISE, copilot=True):
    """An instance state file in the shape verified on VS 2022 17.14.37710.0.

    `selectedPackages`, not `packages`: real state.json has no `packages` key at all,
    though the published vswhere fixture does. Entry ids and shape are verbatim from
    the live machine.
    """
    packages = [
        {"id": "Microsoft.VisualStudio.Component.CoreEditor", "version": version,
         "type": "Component", "selectedState": "IndividuallySelected"},
        {"id": "Microsoft.VisualStudio.Workload.CoreEditor", "version": version,
         "type": "Workload", "selectedState": "IndividuallySelected"},
    ]
    if copilot:
        packages.append({"id": COPILOT_COMPONENT, "version": "17.14.1",
                         "type": "Component", "selectedState": "IndividuallySelected"})
    return {
        "channelId": "VisualStudio.17.Release",
        "installationName": f"VisualStudio/{version}",
        "installationVersion": version,
        "installationPath": r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise",
        "installDate": "2026-01-18T03:45:00Z",
        "product": {"id": product, "version": version, "type": "Product"},
        "selectedPackages": packages,
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
        self.assertEqual(_version_text(_copilot_package(state())["version"]), "17.14.1")

    def test_absent_component_returns_none(self):
        self.assertIsNone(_copilot_package(state(copilot=False)))

    def test_malformed_packages_do_not_raise(self):
        self.assertIsNone(_copilot_package({"selectedPackages": "not-a-list"}))
        self.assertIsNone(_copilot_package({"selectedPackages": [None, 3, {"id": None}]}))

    def test_the_published_vswhere_fixture_shape_still_works(self):
        """microsoft/vswhere's sample carries `packages`; real VS carries
        `selectedPackages`. Both are read."""
        legacy = {"packages": [{"id": COPILOT_COMPONENT, "version": "1.0"}]}
        self.assertEqual(_copilot_package(legacy)["version"], "1.0")

    def test_copilot_for_azure_is_not_mistaken_for_copilot(self):
        """`Component.VisualStudio.GitHubCopilotForAzure.x64` is a different product
        and has no dot, so the marker must not match it."""
        other = {"selectedPackages": [
            {"id": "Component.VisualStudio.GitHubCopilotForAzure.x64", "version": "1.0"}]}
        self.assertIsNone(_copilot_package(other))


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

    def denied_instance_registry(self):
        """Deny only the machine-wide registry; the user's own config dir reads fine.

        Patching ``Path.iterdir`` wholesale would instead trip the per-user gate and
        never reach this branch at all.
        """
        real = vs_mod._listable_state
        return patch.object(
            vs_mod, "_listable_state",
            side_effect=lambda path: "unreadable" if "_Instances" in str(path) else real(path),
        )

    def test_a_denied_instance_registry_is_not_a_clean_absence(self):
        """This user HAS a VS config dir, so an unreadable registry is a denial, not
        an absence — and a clean absence is what lets the backend prune."""
        self.give_user_a_vs_config()
        with self.denied_instance_registry(), \
                patch.object(WindowsVisualStudioDetector, "_vswhere_states", return_value=([], False)), \
                patch.object(utils_mod, "_is_root", return_value=True):
            with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
                with self.assertRaises(PermissionError):
                    self.detector.detect()
        self.assertIn("instances:unreadable", utils_mod.vs_probes())

    def test_a_denied_registry_on_an_unprivileged_scan_still_does_not_raise(self):
        """Raising there would mark every scan on every multi-user box incomplete."""
        self.give_user_a_vs_config()
        with self.denied_instance_registry(), \
                patch.object(WindowsVisualStudioDetector, "_vswhere_states", return_value=([], False)), \
                patch.object(utils_mod, "_is_root", return_value=False), \
                patch.object(utils_mod, "_is_scanning_users_own_home", return_value=False):
            with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
                self.assertIsNone(self.detector.detect())

    def test_a_denied_registry_with_a_vswhere_answer_reports_normally(self):
        """vswhere answered, so nothing is unknown — no raise, rows as usual."""
        self.give_user_a_vs_config()
        with self.denied_instance_registry(), \
                patch.object(WindowsVisualStudioDetector, "_vswhere_states", return_value=([state()], True)), \
                patch.object(utils_mod, "_is_root", return_value=True):
            with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
                rows = self.detector.detect()
        self.assertEqual(len(rows), 2)

    def test_an_instance_without_an_install_path_is_dropped(self):
        """A falsy install_path turns the ownership gate off rather than failing it."""
        instance = self.instances / "nopath"
        instance.mkdir()
        broken = state()
        del broken["installationPath"]
        (instance / "state.json").write_text(json.dumps(broken), encoding="utf-8")
        self.give_user_a_vs_config()
        self.assertIsNone(self.detect())
        self.assertIn("instances:no_install_path", utils_mod.vs_probes())

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


class DeniedUserConfigTests(unittest.TestCase):
    """A denial must not read as "this user does not use Visual Studio".

    A clean absence is what lets the backend prune a live install, so the three
    states `_listable_state` returns have to stay three at the decision point.
    `chmod 000` on the real directory, not a `Path.exists` patch: the detector
    probes via `os.scandir`, so a patched `exists` never reaches this code.
    """

    def setUp(self):
        utils_mod.reset_sentry_run_state()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.program_data = root / "ProgramData"
        instances = self.program_data / "Microsoft" / "VisualStudio" / "Packages" / "_Instances" / "a1"
        instances.mkdir(parents=True)
        (instances / "state.json").write_text(json.dumps(state()), encoding="utf-8")
        self.home = root / "Users" / "nanda"
        self.vs_dir = self.home / "AppData" / "Local" / "Microsoft" / "VisualStudio"
        (self.vs_dir / "17.14_abc").mkdir(parents=True)
        os.chmod(self.vs_dir, 0o000)
        self.detector = WindowsVisualStudioDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        os.chmod(self.vs_dir, 0o755)
        self._tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def detect(self):
        with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
            return self.detector.detect()

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "root can read a 0000 directory")
    def test_denial_is_recorded_not_silently_absent(self):
        self.detect()
        self.assertIn("user_config:unreadable", utils_mod.vs_probes())

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "root can read a 0000 directory")
    def test_privileged_scan_raises_so_the_run_is_marked_incomplete(self):
        with patch.object(utils_mod, "_is_root", return_value=True):
            with self.assertRaises(PermissionError):
                self.detect()

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0, "root can read a 0000 directory")
    def test_unprivileged_sibling_home_does_not_raise(self):
        """Raising there would mark every scan on every multi-user box incomplete."""
        with patch.object(utils_mod, "_is_root", return_value=False), \
                patch.object(utils_mod, "_is_scanning_users_own_home", return_value=False):
            self.assertIsNone(self.detect())  # must not raise


class VswhereFallbackTests(unittest.TestCase):
    """vswhere emits a flat `productId` and no `packages` array at all; the state.json
    helpers expect a nested `product.id`. Unmapped, Build Tools passes the SKU filter,
    the edition drops out of the row name, and the Copilot row silently vanishes."""

    # Verbatim shape of a `vswhere -format json` row.
    ROW = {
        "instanceId": "a1b2c3",
        "installationPath": r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise",
        "installationVersion": "17.14.3",
        "productId": ENTERPRISE,
        "displayName": "Visual Studio Enterprise 2022",
        "channelId": "VisualStudio.17.Release",
    }

    def setUp(self):
        utils_mod.reset_sentry_run_state()
        self.detector = WindowsVisualStudioDetector()

    def tearDown(self):
        utils_mod.reset_sentry_run_state()

    def test_flat_product_id_is_mapped_into_the_nested_shape(self):
        mapped = _from_vswhere(self.ROW)
        self.assertTrue(_is_reportable(mapped))
        self.assertEqual(_display_name(mapped), "Visual Studio 2022 Enterprise")

    def test_build_tools_from_vswhere_is_still_rejected(self):
        mapped = _from_vswhere({**self.ROW, "productId": BUILD_TOOLS})
        self.assertFalse(_is_reportable(mapped))

    def test_copilot_comes_from_the_requires_probe_not_from_silence(self):
        with patch.object(self.detector, "_run_vswhere", side_effect=[[self.ROW], [self.ROW]]):
            states, copilot_known = self.detector._vswhere_states()
        self.assertTrue(copilot_known)
        package = _copilot_package(states[0])
        self.assertIsNotNone(package)          # present...
        self.assertIsNone(package["version"])  # ...but vswhere cannot name the version

    def test_copilot_absence_is_not_asserted_when_the_probe_fails(self):
        """An unanswerable probe must not read as "Copilot is not installed"."""
        with patch.object(self.detector, "_run_vswhere", side_effect=[[self.ROW], None]):
            states, copilot_known = self.detector._vswhere_states()
        self.assertIsNone(_copilot_package(states[0]))
        self.assertFalse(copilot_known)   # silence is not evidence of absence
        self.assertIn("vswhere_copilot:unknown", utils_mod.vs_probes())


class DefensiveParsingTests(unittest.TestCase):
    """No real state.json has ever been read, so nothing out of it is trusted."""

    def test_non_ide_skus_are_rejected(self):
        for sku in ("Microsoft.VisualStudio.Product.TestAgent",
                    "Microsoft.VisualStudio.Product.TeamExplorer",
                    "Microsoft.VisualStudio.Product.Server"):
            with self.subTest(sku=sku):
                self.assertFalse(_is_reportable(state(product=sku)))

    def test_preview_and_release_get_different_names(self):
        release = state()
        preview = {**state(), "channelId": "VisualStudio.17.Preview"}
        self.assertNotEqual(_display_name(release), _display_name(preview))
        self.assertEqual(_display_name(preview), "Visual Studio 2022 Enterprise Preview")

    def test_a_nested_version_object_never_reaches_a_row(self):
        broken = state()
        broken["selectedPackages"][-1]["version"] = {"nested": "dict"}
        self.assertIsNone(_version_text(_copilot_package(broken)["version"]))

    def test_a_wrong_typed_product_does_not_crash_the_scan(self):
        """`or {}` covers null and missing but not a truthy non-dict. An
        AttributeError out of detect() marks the run incomplete, which stops the
        backend pruning anything at all on that device."""
        for bogus in (["Microsoft.VisualStudio.Product.Enterprise"], "Enterprise", 3, None):
            with self.subTest(product=bogus):
                self.assertFalse(_is_reportable({**state(), "product": bogus}))
                self.assertEqual(_display_name({**state(), "product": bogus}), "Visual Studio 2022")

    def test_version_is_bounded(self):
        broken = state()
        broken["selectedPackages"][-1]["version"] = "9" * 500
        self.assertLessEqual(len(_version_text(_copilot_package(broken)["version"])), 64)


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


class SharedMcpReadTests(unittest.TestCase):
    """`read_mcp_json` is shared with the GitHub Copilot extractor, so anything that
    escapes it takes out every OTHER workspace and global config for that surface —
    `_extract_workspace_configs` has no per-file guard, and the caller logs the
    exception as a warning, so the device reports zero MCP servers with no
    scan-incomplete signal."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_a_non_object_json_root_returns_none_rather_than_raising(self):
        """`[]`, `null`, a bare string and a number are all valid JSON, so they reach
        the caller with no exception and `.get` would AttributeError on them."""
        for name, text in (("arr.json", "[]"), ("null.json", "null"),
                           ("str.json", '"hi"'), ("num.json", "3")):
            with self.subTest(body=text):
                self.assertIsNone(read_mcp_json(self.write(name, text), "/p", "Test"))

    def test_one_bad_file_does_not_take_out_the_good_ones(self):
        good = self.write("good.json", json.dumps({"servers": {"s": {"command": "x"}}}))
        bad = self.write("bad.json", "[]")
        results = [read_mcp_json(p, "/p", "Test") for p in (bad, good)]
        self.assertIsNone(results[0])
        self.assertEqual([s["name"] for s in results[1]["mcpServers"]], ["s"])


class SideBySideInstanceTests(unittest.TestCase):
    """Copilot is an optional component, so an Enterprise install carrying it says
    nothing about the Community install beside it."""

    def setUp(self):
        utils_mod.reset_sentry_run_state()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.program_data = root / "ProgramData"
        self.instances = self.program_data / "Microsoft" / "VisualStudio" / "Packages" / "_Instances"
        self.instances.mkdir(parents=True)
        self.home = root / "Users" / "nanda"
        (self.home / "AppData" / "Local" / "Microsoft" / "VisualStudio" / "17.14_a").mkdir(parents=True)
        self.detector = WindowsVisualStudioDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        self._tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def write(self, name, product, copilot, path):
        body = {**state(product=product, copilot=copilot), "installationPath": path}
        (self.instances / name).mkdir()
        (self.instances / name / "state.json").write_text(json.dumps(body), encoding="utf-8")

    def test_copilot_does_not_cross_instances(self):
        self.write("ent", ENTERPRISE, True, r"C:\VS\2022\Enterprise")
        self.write("com", COMMUNITY, False, r"C:\VS\2022\Community")
        with patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
            rows = self.detector.detect()
        by_name = {r["name"]: r for r in rows}
        self.assertEqual(by_name["Visual Studio 2022 Enterprise"]["plugins"], ["GitHub Copilot"])
        self.assertEqual(by_name["Visual Studio 2022 Community"]["plugins"], [])
        self.assertEqual(by_name[COPILOT_TOOL_NAME]["version"], "17.14.1")


class UnresolvedInstanceTests(unittest.TestCase):
    """Only returned rows enter the manifest, so a readable instance beside an
    unknown one would pass as a complete inventory and the missing live install
    would be pruned. `unresolved` must not be gated on `rows` being empty."""

    def setUp(self):
        utils_mod.reset_sentry_run_state()
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.program_data = root / "ProgramData"
        self.instances = self.program_data / "Microsoft" / "VisualStudio" / "Packages" / "_Instances"
        self.instances.mkdir(parents=True)
        self.home = root / "Users" / "nanda"
        (self.home / "AppData" / "Local" / "Microsoft" / "VisualStudio" / "17.14_a").mkdir(parents=True)
        good = self.instances / "good"
        good.mkdir()
        (good / "state.json").write_text(json.dumps(state()), encoding="utf-8")
        self.detector = WindowsVisualStudioDetector()
        self.detector.user_home = self.home

    def tearDown(self):
        self._tmp.cleanup()
        utils_mod.reset_sentry_run_state()

    def detect_privileged(self):
        with patch.object(utils_mod, "_is_root", return_value=True), \
                patch.dict(os.environ, {"ProgramData": str(self.program_data)}, clear=False):
            return self.detector.detect()

    def test_a_malformed_sibling_makes_the_inventory_incomplete(self):
        bad = self.instances / "bad"
        bad.mkdir()
        (bad / "state.json").write_text("{not json", encoding="utf-8")
        with self.assertRaises(PermissionError):
            self.detect_privileged()

    def test_an_empty_instance_dir_is_normal_and_does_not_raise(self):
        """A dir with no state file is routine — it must not disable pruning."""
        (self.instances / "empty").mkdir()
        rows = self.detect_privileged()
        self.assertEqual([r["name"] for r in rows][0], "Visual Studio 2022 Enterprise")

    def test_unknowable_copilot_from_vswhere_is_not_an_absence(self):
        """vswhere lists the IDEs but cannot report packages, so emitting IDE rows
        without a Copilot row would prune an existing one."""
        with patch.object(vs_mod, "_listable_state", return_value="unreadable"), \
                patch.object(WindowsVisualStudioDetector, "_vswhere_states",
                             return_value=([state()], False)):
            with self.assertRaises(PermissionError):
                self.detect_privileged()


class RuleSymlinkTests(unittest.TestCase):
    r"""These paths sit inside a profile its owner controls, but an all-user scan
    reads them as Administrator or LOCAL SYSTEM. A junction at
    `copilot-instructions.md` would otherwise put up to 50 KB of a file only the
    elevated scanner can read into the uploaded report."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "nanda"
        (self.home / ".github" / "agents").mkdir(parents=True)
        self.secret = root / "protected" / "secret.md"
        self.secret.parent.mkdir()
        self.secret.write_text("elevated-only content", encoding="utf-8")
        self.extractor = WindowsVisualStudioRulesExtractor()

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_symlinked_instructions_file_is_not_read(self):
        (self.home / "copilot-instructions.md").symlink_to(self.secret)
        self.assertEqual(self.extractor._rule_files(self.home), [])

    def test_a_symlinked_agents_dir_is_not_walked(self):
        outside = Path(self._tmp.name) / "elsewhere"
        outside.mkdir()
        (outside / "x.agent.md").write_text("---\nname: x\n---\n", encoding="utf-8")
        agents = self.home / ".github" / "agents"
        agents.rmdir()
        agents.symlink_to(outside, target_is_directory=True)
        self.assertEqual(self.extractor._rule_files(self.home), [])

    def test_a_symlinked_agent_file_is_not_read(self):
        (self.home / ".github" / "agents" / "x.agent.md").symlink_to(self.secret)
        self.assertEqual(self.extractor._rule_files(self.home), [])

    def test_real_files_inside_the_profile_are_still_read(self):
        (self.home / "copilot-instructions.md").write_text("mine", encoding="utf-8")
        (self.home / ".github" / "agents" / "x.agent.md").write_text("---\nname: x\n---\n",
                                                                    encoding="utf-8")
        self.assertEqual(
            sorted(f.name for f in self.extractor._rule_files(self.home)),
            ["copilot-instructions.md", "x.agent.md"])


class UserScopeMcpTests(unittest.TestCase):
    r"""`%USERPROFILE%\.mcp.json` is Visual Studio's global MCP config AND what
    Claude Code reads as a home-rooted project config. Whoever claims it must be
    exactly one tool, or every server is counted twice."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "nanda"
        self.home.mkdir(parents=True)
        (self.home / ".mcp.json").write_text(
            json.dumps({"servers": {"github": {"url": "https://api.githubcopilot.com/mcp/"}}}),
            encoding="utf-8")
        helpers.reset_workspace_config_dirs()
        self.extractor = WindowsVisualStudioMCPConfigExtractor()

    def tearDown(self):
        self._tmp.cleanup()
        helpers.reset_workspace_config_dirs()

    def extract(self, claim_user_scope):
        with patch.object(vs_mcp, "collect_workspace_config_dirs", return_value={".vs": []}), \
                patch.object(vs_mcp, "scan_windows_user_directories",
                             side_effect=lambda cb: cb(self.home)):
            return self.extractor.extract_mcp_config(claim_user_scope=claim_user_scope)

    def test_claimed_when_claude_code_is_absent(self):
        config = self.extract(claim_user_scope=True)
        self.assertEqual([p["path"] for p in config["projects"]], [str(self.home)])
        self.assertEqual([s["name"] for s in config["projects"][0]["mcpServers"]], ["github"])

    def test_left_alone_when_claude_code_would_report_it(self):
        self.assertIsNone(self.extract(claim_user_scope=False))

    def test_the_flag_defaults_to_not_claiming(self):
        """The orchestrator starts the flag at "Claude Code present": a wrong True
        costs a missing row, a wrong False double-counts every server."""
        detector = object.__new__(AIToolsDetector)
        self.assertNotIn("_claude_code_detected", vars(detector))
        detector._set_claude_code_detected([{"name": "Claude Code"}])
        self.assertTrue(detector._claude_code_detected)
        detector._set_claude_code_detected([{"name": "Visual Studio 2022 Enterprise"}])
        self.assertFalse(detector._claude_code_detected)


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
