"""
Integration tests for Augment Code discovery (macOS + Windows/Linux smoke).

Augment Code ships three surfaces — the Auggie CLI, the VS Code extension, and the
JetBrains plugin — that share one ``~/.augment`` config dir. These tests exercise
the outermost surfaces:

  - The detector's ``detect()`` emits a separate row per surface.
  - ``_resolve_augment_dir`` default + ``_parse_cli_version`` cases.
  - ``AIToolsDetector.process_single_tool`` routing — the Augment branch wins over
    the generic JetBrains ``_config_path`` fallback (R2).
  - The MCP extractor reads both the top-level and nested ``mcpServers`` nestings.
  - The Windows + Linux subclasses import/instantiate.

Conventions mirror the existing suite: temp HOME dirs, the globally-stubbed MCP
scanner (``tests/__init__.py``), ``_SENTRY_DSN`` forced empty.
"""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.coding_tool_factory import (
    AugmentMCPConfigExtractorFactory,
    ToolDetectorFactory,
)
from scripts.coding_discovery_tools.macos.augment.augment import (
    MacOSAugmentDetector,
    _parse_cli_version,
    _resolve_auggie_binary,
    _resolve_augment_dir,
)
from scripts.coding_discovery_tools.macos.augment.augment_mcp_config_extractor import (
    MacOSAugmentMCPConfigExtractor,
    _extract_servers_obj,
)
from scripts.coding_discovery_tools.windows.augment.augment import WindowsAugmentDetector
from scripts.coding_discovery_tools.windows.augment.augment_mcp_config_extractor import (
    WindowsAugmentMCPConfigExtractor,
)
from scripts.coding_discovery_tools.windows.augment.augment_settings_extractor import (
    WindowsAugmentSettingsExtractor,
)
from scripts.coding_discovery_tools.windows.augment.augment_skills_extractor import (
    WindowsAugmentSkillsExtractor,
)
from scripts.coding_discovery_tools.linux.augment.augment import LinuxAugmentDetector
from scripts.coding_discovery_tools.linux.augment.augment_mcp_config_extractor import (
    LinuxAugmentMCPConfigExtractor,
)
from scripts.coding_discovery_tools.linux.augment.augment_skills_extractor import (
    LinuxAugmentSkillsExtractor,
)

_DETECTOR_MOD = "scripts.coding_discovery_tools.macos.augment.augment"


def _write_auggie_binary(user_home: Path) -> Path:
    """Drop an executable ``~/.local/bin/auggie`` under ``user_home``."""
    binary = user_home / ".local" / "bin" / "auggie"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\necho auggie\n", encoding="utf-8")
    os.chmod(binary, 0o755)
    return binary


# ---------------------------------------------------------------------------
# 1. Version parsing + config-dir resolution (focused unit tests)
# ---------------------------------------------------------------------------

class TestAugmentParsing(unittest.TestCase):
    def test_parse_cli_version_semver_from_banner(self):
        self.assertEqual(_parse_cli_version("0.30.0 (commit 690bba03)"), "0.30.0")

    def test_parse_cli_version_plain(self):
        self.assertEqual(_parse_cli_version("1.2.3"), "1.2.3")

    def test_parse_cli_version_none_and_garbage(self):
        self.assertIsNone(_parse_cli_version(None))
        self.assertIsNone(_parse_cli_version(""))
        # No semver -> falls back to first non-empty line (capped).
        self.assertEqual(_parse_cli_version("auggie dev build"), "auggie dev build")

    def test_resolve_augment_dir_default(self):
        home = Path("/Users/alice")
        self.assertEqual(_resolve_augment_dir(home), home / ".augment")


# ---------------------------------------------------------------------------
# 2. Detection: separate rows per surface
# ---------------------------------------------------------------------------

class TestAugmentDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = MacOSAugmentDetector()
        self.tmp_dir = tempfile.mkdtemp()
        self.user_home = Path(self.tmp_dir) / "user"
        self.user_home.mkdir(parents=True)
        self.detector.user_home = self.user_home
        self._patchers = [
            patch(f"{_DETECTOR_MOD}.is_running_as_root", return_value=False),
        ]
        for p in self._patchers:
            p.start()

    def tearDown(self):
        for p in self._patchers:
            p.stop()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_vscode_ext(self, ext_id="augment.vscode-augment", version="1.5.0"):
        ext_path = self.user_home / ".vscode" / "extensions" / "extensions.json"
        ext_path.parent.mkdir(parents=True, exist_ok=True)
        ext_path.write_text(json.dumps([
            {"identifier": {"id": ext_id}, "version": version},
        ]), encoding="utf-8")
        return ext_path

    def test_cli_row_gated_on_binary(self):
        binary = _write_auggie_binary(self.user_home)
        with patch.object(self.detector, "get_version", return_value=None):
            rows = self.detector.detect()
        self.assertIsNotNone(rows)
        cli = [r for r in rows if r["name"] == "Auggie CLI"]
        self.assertEqual(len(cli), 1)
        self.assertEqual(cli[0]["install_path"], str(binary))
        self.assertEqual(cli[0]["publisher"], "Augment Computer")
        self.assertEqual(cli[0]["_config_path"], str(self.user_home / ".augment"))
        self.assertEqual(cli[0]["version"], "unknown")

    def test_no_binary_no_cli_row(self):
        # No auggie binary and no VS Code/JetBrains -> no rows at all.
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = []
            self.assertIsNone(self.detector.detect())

    def test_vscode_row_from_extensions_json(self):
        self._write_vscode_ext(version="2.0.1")
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = []
            rows = self.detector.detect()
        vsc = [r for r in rows if r["name"] == "Augment (VS Code)"]
        self.assertEqual(len(vsc), 1)
        self.assertEqual(vsc[0]["version"], "2.0.1")
        self.assertEqual(vsc[0]["publisher"], "Augment Computer")

    def _write_vscode_exts(self, exts):
        """Write multiple extensions: ``exts`` is a list of (ext_id, version)."""
        ext_path = self.user_home / ".vscode" / "extensions" / "extensions.json"
        ext_path.parent.mkdir(parents=True, exist_ok=True)
        ext_path.write_text(json.dumps([
            {"identifier": {"id": ext_id}, "version": version}
            for ext_id, version in exts
        ]), encoding="utf-8")
        return ext_path

    def test_vscode_nightly_id_matched(self):
        self._write_vscode_ext(ext_id="augment.vscode-augment-nightly", version="2.0.1-nightly")
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = []
            rows = self.detector.detect()
        vsc = [r for r in rows if r["name"] == "Augment (VS Code)"]
        # FIX C: nightly-only -> one row with nightly's version.
        self.assertEqual(len(vsc), 1)
        self.assertEqual(vsc[0]["version"], "2.0.1-nightly")

    def test_vscode_stable_and_nightly_emit_single_stable_row(self):
        """FIX C: both stable + nightly installed -> exactly ONE "Augment (VS Code)"
        row, carrying the STABLE extension's version (preferred over nightly)."""
        self._write_vscode_exts([
            ("augment.vscode-augment-nightly", "2.0.1-nightly"),
            ("augment.vscode-augment", "1.5.0"),
        ])
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = []
            rows = self.detector.detect()
        vsc = [r for r in rows if r["name"] == "Augment (VS Code)"]
        self.assertEqual(len(vsc), 1)
        self.assertEqual(vsc[0]["version"], "1.5.0")

    def test_corrupt_extensions_json_yields_no_vscode_row(self):
        ext_path = self.user_home / ".vscode" / "extensions" / "extensions.json"
        ext_path.parent.mkdir(parents=True, exist_ok=True)
        ext_path.write_text("{ not json", encoding="utf-8")
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = []
            rows = self.detector.detect() or []
        self.assertEqual([r for r in rows if r["name"] == "Augment (VS Code)"], [])

    def test_jetbrains_row_when_plugin_matches(self):
        fake_ide = {
            "name": "IntelliJ IDEA",
            "version": "2024.1",
            "plugins": ["Augment", "Some Other Plugin"],
            "config_path": "/cfg/IntelliJIdea2024.1",
            "install_path": "/cfg/IntelliJIdea2024.1",
        }
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = [fake_ide]
            rows = self.detector.detect()
        jbrows = [r for r in rows if r["name"] == "Augment (IntelliJ IDEA)"]
        self.assertEqual(len(jbrows), 1)
        self.assertEqual(jbrows[0]["ide"], "IntelliJ IDEA")
        self.assertEqual(jbrows[0]["version"], "2024.1")
        self.assertEqual(jbrows[0]["_config_path"], str(self.user_home / ".augment"))

    def test_jetbrains_no_row_when_no_augment_plugin(self):
        fake_ide = {
            "name": "PyCharm",
            "version": "2024.1",
            "plugins": ["GitHub Copilot"],
            "config_path": "/cfg/PyCharm2024.1",
            "install_path": "/cfg/PyCharm2024.1",
        }
        with patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = [fake_ide]
            rows = self.detector.detect() or []
        self.assertEqual([r for r in rows if r["name"].startswith("Augment (")], [])

    def test_jetbrains_root_scan_attributes_config_to_ide_owner(self):
        """FIX H2: under a root all-users scan the JetBrains detector returns
        EVERY user's IDEs; each Augment JetBrains row's ``_config_path`` must point
        at the IDE OWNER's ``~/.augment`` (derived from the IDE config path), not
        the outer scan home."""
        self.detector.user_home = None  # simulate a root all-users scan
        bob_ide = {
            "name": "IntelliJ IDEA",
            "version": "2024.1",
            "plugins": ["Augment"],
            "config_path": "/Users/bob/Library/Application Support/JetBrains/IntelliJIdea2024.1",
            "install_path": "/Users/bob/Library/Application Support/JetBrains/IntelliJIdea2024.1",
        }
        with patch.object(self.detector, "_iter_scan_homes",
                          return_value=[Path("/Users/alice"), Path("/Users/bob")]), \
             patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = [bob_ide]
            rows = self.detector.detect() or []
        jbrows = [r for r in rows if r["name"] == "Augment (IntelliJ IDEA)"]
        self.assertEqual(len(jbrows), 1)
        # Attributed to bob (the IDE owner), NOT alice (the other scanned home).
        # Build the expected via Path so the separator matches the host OS
        # (production stringifies a Path; a literal "/Users/..." breaks on Windows).
        self.assertEqual(jbrows[0]["_config_path"], str(Path("/Users/bob") / ".augment"))

    def test_all_three_surfaces_as_separate_rows(self):
        _write_auggie_binary(self.user_home)
        self._write_vscode_ext()
        fake_ide = {
            "name": "GoLand", "version": "2024.1", "plugins": ["augment-jetbrains"],
            "config_path": "/cfg/GoLand", "install_path": "/cfg/GoLand",
        }
        with patch.object(self.detector, "get_version", return_value=None), \
             patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.return_value = [fake_ide]
            rows = self.detector.detect()
        names = sorted(r["name"] for r in rows)
        self.assertEqual(names, ["Auggie CLI", "Augment (GoLand)", "Augment (VS Code)"])

    def test_detect_never_raises_on_jetbrains_error(self):
        _write_auggie_binary(self.user_home)
        with patch.object(self.detector, "get_version", return_value=None), \
             patch.object(self.detector, "_make_jetbrains_detector") as jb:
            jb.return_value.detect.side_effect = OSError("boom")
            rows = self.detector.detect()
        # CLI row still surfaces; the JetBrains error is swallowed.
        self.assertTrue(any(r["name"] == "Auggie CLI" for r in rows))


# ---------------------------------------------------------------------------
# 3. Routing: Augment branch wins over the JetBrains _config_path fallback (R2)
# ---------------------------------------------------------------------------

class TestAugmentRouting(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = AIToolsDetector(os_name="Darwin")
        # Stub the shared extractors so process_single_tool runs no real walk.
        self.detector._augment_mcp_extractor = MagicMock()
        self.detector._augment_mcp_extractor.extract_mcp_config.return_value = None
        self.detector._augment_rules_extractor = MagicMock()
        self.detector._augment_rules_extractor.extract_all_augment_rules.return_value = []
        self.detector._augment_skills_extractor = MagicMock()
        self.detector._augment_skills_extractor.extract_all_skills.return_value = {
            "user_skills": [], "project_skills": [],
        }
        self.detector._augment_settings_extractor = MagicMock()
        self.detector._augment_settings_extractor.extract_settings.return_value = []

    def test_auggie_cli_routes_to_augment_branch(self):
        tool = {"name": "Auggie CLI", "version": "0.30.0",
                "install_path": "/Users/x/.local/bin/auggie", "_config_path": "/Users/x/.augment"}
        self.detector._set_canonical_augment_surface([tool])
        sentinel = {"routed": "augment"}
        with patch.object(self.detector, "_process_augment_tool", return_value=sentinel) as branch:
            result = self.detector.process_single_tool(tool)
        self.assertEqual(result, sentinel)
        self.assertEqual(branch.call_count, 1)

    def test_vscode_augment_routes_to_augment_branch(self):
        tool = {"name": "Augment (VS Code)", "version": "1.0",
                "install_path": "/x", "_config_path": "/Users/x/.augment"}
        self.detector._set_canonical_augment_surface([tool])
        sentinel = {"routed": "augment"}
        with patch.object(self.detector, "_process_augment_tool", return_value=sentinel) as branch:
            result = self.detector.process_single_tool(tool)
        self.assertEqual(result, sentinel)
        self.assertEqual(branch.call_count, 1)

    def test_jetbrains_augment_does_not_fall_into_jetbrains_branch(self):
        """R2 regression: an Augment JetBrains row carries ``_config_path`` and must
        route to the Augment branch, NOT the generic JetBrains handler."""
        tool = {"name": "Augment (IntelliJ IDEA)", "version": "2024.1", "ide": "IntelliJ IDEA",
                "install_path": "/cfg", "_config_path": "/Users/x/.augment"}
        self.detector._set_canonical_augment_surface([tool])
        with patch.object(self.detector, "_process_augment_tool", return_value={"routed": "augment"}) as aug, \
             patch.object(self.detector, "_process_jetbrains_tool", return_value={}) as jb:
            result = self.detector.process_single_tool(tool)
        self.assertEqual(result, {"routed": "augment"})
        self.assertEqual(aug.call_count, 1)
        self.assertEqual(jb.call_count, 0)

    def test_real_jetbrains_ide_still_routes_to_jetbrains_branch(self):
        """A non-Augment JetBrains IDE row must still take the JetBrains branch."""
        tool = {"name": "PyCharm", "version": "2024.1", "_config_path": "/cfg/PyCharm",
                "_ide_folder": "PyCharm2024.1"}
        with patch.object(self.detector, "_process_augment_tool") as aug, \
             patch.object(self.detector, "_process_jetbrains_tool", return_value={}) as jb:
            self.detector.process_single_tool(tool)
        self.assertEqual(aug.call_count, 0)
        self.assertEqual(jb.call_count, 1)


# ---------------------------------------------------------------------------
# 4. MCP: reads both top-level and nested mcpServers nestings
# ---------------------------------------------------------------------------

class TestAugmentMCPNestings(unittest.TestCase):
    def test_extract_servers_top_level(self):
        data = {"mcpServers": {"srv": {"command": "node"}}}
        self.assertEqual(_extract_servers_obj(data), {"srv": {"command": "node"}})

    def test_extract_servers_nested_augment_advanced(self):
        data = {"augment": {"advanced": {"mcpServers": {"srv": {"command": "node"}}}}}
        self.assertEqual(_extract_servers_obj(data), {"srv": {"command": "node"}})

    def test_extract_servers_flat_form(self):
        data = {"srv": {"command": "node"}, "metadata": "ignored"}
        self.assertEqual(_extract_servers_obj(data), {"srv": {"command": "node"}})

    def test_top_level_wins_over_nested(self):
        data = {
            "mcpServers": {"top": {"command": "a"}},
            "augment": {"advanced": {"mcpServers": {"nested": {"command": "b"}}}},
        }
        self.assertEqual(_extract_servers_obj(data), {"top": {"command": "a"}})

    def test_user_config_reads_nested_nesting(self):
        tmp = tempfile.mkdtemp()
        try:
            home = Path(tmp) / "user"
            augment_dir = home / ".augment"
            augment_dir.mkdir(parents=True)
            (augment_dir / "settings.json").write_text(json.dumps({
                "augment": {"advanced": {"mcpServers": {"db": {"command": "mcp-db"}}}},
            }), encoding="utf-8")
            extractor = MacOSAugmentMCPConfigExtractor()
            configs = extractor._extract_user_configs_for_user(home)
            self.assertEqual(len(configs), 1)
            self.assertEqual(configs[0]["path"], str(augment_dir))
            self.assertEqual(len(configs[0]["mcpServers"]), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_workspace_walk_skips_user_augment_dir(self):
        """A user-home ``~/.augment`` already collected as USER scope must NOT be
        re-collected by the workspace walk as PROJECT scope — otherwise the same
        MCP servers are duplicated under two project paths (Greptile finding). A
        genuine project ``.augment`` is still collected."""
        tmp = tempfile.mkdtemp()
        try:
            root = Path(tmp)
            user_augment = root / "home" / ".augment"
            user_augment.mkdir(parents=True)
            (user_augment / "settings.json").write_text(json.dumps({
                "mcpServers": {"db": {"command": "mcp-db"}},
            }), encoding="utf-8")
            proj_augment = root / "repo" / ".augment"
            proj_augment.mkdir(parents=True)
            (proj_augment / "settings.json").write_text(json.dumps({
                "mcpServers": {"proj": {"command": "mcp-proj"}},
            }), encoding="utf-8")

            extractor = MacOSAugmentMCPConfigExtractor()
            # Pretend the user-home ~/.augment was already collected as USER scope.
            extractor._scanned_user_augment_dirs = {user_augment.resolve()}

            projects = []
            # Drive the walk from the temp ancestor (current_depth=0 avoids the
            # relative_to('/') path that breaks on Windows) with the system-skip
            # predicate neutralised for the temp tree.
            with patch.object(extractor, "_should_skip_workspace_path", return_value=False):
                extractor._walk_for_workspace_configs(root, root, projects, current_depth=0)

            paths = {p["path"] for p in projects}
            # User-home .augment SKIPPED (no project entry); the real repo kept.
            self.assertNotIn(str(user_augment.parent), paths)
            self.assertIn(str(proj_augment.parent), paths)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. Windows + Linux subclasses import/instantiate (smoke)
# ---------------------------------------------------------------------------

class TestAugmentCrossPlatformSmoke(unittest.TestCase):
    def test_factory_creates_per_os(self):
        for os_name in ("Darwin", "Windows", "Linux"):
            self.assertIsNotNone(ToolDetectorFactory.create_augment_detector(os_name))
            self.assertIsNotNone(AugmentMCPConfigExtractorFactory.create(os_name))

    def test_windows_subclasses_instantiate(self):
        self.assertEqual(WindowsAugmentDetector().tool_name, "Augment Code")
        self.assertIsInstance(WindowsAugmentMCPConfigExtractor(), MacOSAugmentMCPConfigExtractor)
        WindowsAugmentSettingsExtractor()
        WindowsAugmentSkillsExtractor()

    def test_linux_subclasses_instantiate(self):
        self.assertEqual(LinuxAugmentDetector().tool_name, "Augment Code")
        self.assertIsInstance(LinuxAugmentMCPConfigExtractor(), MacOSAugmentMCPConfigExtractor)
        LinuxAugmentSkillsExtractor()

    def test_windows_skills_skip_predicate_parity(self):
        """FIX E: the Windows skills walk-skip predicate must, like the macOS base
        and the Windows RULES extractor, skip other-tool config dirs (``~/.<tool>``)
        while still descending into ``.augment``.

        Paths are built from separate components (not a backslash literal) so
        ``.parts`` splits correctly on BOTH the macOS and Windows CI runners.
        """
        extractor = WindowsAugmentSkillsExtractor()
        anchor = Path(Path.home().anchor)
        # Other-tool config dirs are skipped (no descent into another tool's bundle).
        self.assertTrue(
            extractor._should_skip_walk_item(anchor / "Users" / "alice" / ".cursor"))
        self.assertTrue(
            extractor._should_skip_walk_item(
                anchor / "Users" / "alice" / "repo" / ".claude" / "skills"))
        # ``.augment`` itself must NOT be skipped (it must stay traversable).
        self.assertFalse(
            extractor._should_skip_walk_item(
                anchor / "Users" / "alice" / "repo" / ".augment"))


if __name__ == "__main__":
    unittest.main()
