"""Config-extraction tests for Zed (macOS + Linux).

Zed settings are JSONC — comments and trailing commas are legal — so the
extractor must record RAW text and never parse. These tests pin that, plus the
"only a real ``settings.json`` marks a configured project" gate and the
project-root ``.rules`` instructions file.

The extractors are driven at ``_extract_rules_from_zed_directory`` /
``_extract_global_rules`` so no test ever walks ``/``.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.zed.zed_rules_extractor import LinuxZedRulesExtractor
from scripts.coding_discovery_tools.macos.zed.zed_rules_extractor import MacOSZedRulesExtractor
from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list

_MACOS_EXT_MOD = "scripts.coding_discovery_tools.macos.zed.zed_rules_extractor"
_LINUX_EXT_MOD = "scripts.coding_discovery_tools.linux.zed.zed_rules_extractor"

JSONC_BODY = '{\n  // the default model\n  "assistant": {"version": "2"},\n}\n'


class _ZedConfigMixin:
    """Shared extraction assertions; subclasses set ``Extractor`` + ``_home_ctx``."""

    Extractor = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.proj = self.root / "proj"
        self.proj.mkdir()
        self.extractor = self.Extractor()

    def tearDown(self):
        self.tmp.cleanup()

    @contextlib.contextmanager
    def _home_ctx(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def _zed_dir(self) -> Path:
        zed_dir = self.proj / ".zed"
        zed_dir.mkdir(parents=True, exist_ok=True)
        return zed_dir

    def _project_rules(self, zed_dir: Path):
        projects = {}
        self.extractor._extract_rules_from_zed_directory(zed_dir, projects)
        return build_project_list(projects)

    # --- project-level ----------------------------------------------------

    def test_settings_json_reports_project(self):
        zed_dir = self._zed_dir()
        (zed_dir / "settings.json").write_text("{}", encoding="utf-8")
        projects = self._project_rules(zed_dir)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(self.proj))
        self.assertIn("settings.json", [r["file_name"] for r in projects[0]["rules"]])

    def test_bare_zed_directory_not_reported(self):
        """A ``.zed/`` with only ``debug.json`` is not a configured project."""
        zed_dir = self._zed_dir()
        (zed_dir / "debug.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self._project_rules(zed_dir), [])

    def test_all_project_config_files_and_root_rules_collected(self):
        zed_dir = self._zed_dir()
        (zed_dir / "settings.json").write_text("{}", encoding="utf-8")
        (zed_dir / "tasks.json").write_text("[]", encoding="utf-8")
        (zed_dir / "debug.json").write_text("[]", encoding="utf-8")
        (self.proj / ".rules").write_text("be terse\n", encoding="utf-8")
        projects = self._project_rules(zed_dir)
        self.assertEqual(len(projects), 1)
        self.assertEqual(
            sorted(r["file_name"] for r in projects[0]["rules"]),
            [".rules", "debug.json", "settings.json", "tasks.json"],
        )

    def test_jsonc_settings_recorded_verbatim(self):
        """No ``json.loads``: JSONC would raise, and parsing would reformat."""
        zed_dir = self._zed_dir()
        # write_bytes: write_text would turn \n into \r\n on Windows, and the
        # hardened reader records the file byte-for-byte.
        (zed_dir / "settings.json").write_bytes(JSONC_BODY.encode("utf-8"))
        projects = self._project_rules(zed_dir)
        rule = next(r for r in projects[0]["rules"] if r["file_name"] == "settings.json")
        self.assertEqual(rule["content"], JSONC_BODY)

    # --- global -----------------------------------------------------------

    def _global_rules(self):
        projects = {}
        with self._home_ctx():
            self.extractor._extract_global_rules(projects)
        return build_project_list(projects)

    def test_global_config_reported_under_home_with_user_scope(self):
        cfg = self.root / ".config" / "zed"
        cfg.mkdir(parents=True)
        (cfg / "settings.json").write_text(JSONC_BODY, encoding="utf-8")
        (cfg / "keymap.json").write_text("[]", encoding="utf-8")
        (cfg / "AGENTS.md").write_text("# agents", encoding="utf-8")
        projects = self._global_rules()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(self.root))
        names = sorted(r["file_name"] for r in projects[0]["rules"])
        self.assertEqual(names, ["AGENTS.md", "keymap.json", "settings.json"])
        for rule in projects[0]["rules"]:
            self.assertEqual(rule["scope"], "user")

    def test_global_settings_alone_gates_on_global_settings_json(self):
        """``global_settings.json`` (no ``settings.json``) still opens the gate."""
        cfg = self.root / ".config" / "zed"
        cfg.mkdir(parents=True)
        (cfg / "global_settings.json").write_text("{}", encoding="utf-8")
        projects = self._global_rules()
        self.assertEqual(len(projects), 1)
        self.assertIn(
            "global_settings.json", [r["file_name"] for r in projects[0]["rules"]]
        )

    def test_global_bare_config_dir_not_reported(self):
        cfg = self.root / ".config" / "zed"
        (cfg / "extensions").mkdir(parents=True)
        (cfg / "keymap.json").write_text("[]", encoding="utf-8")
        self.assertEqual(self._global_rules(), [])


class TestMacOSZedConfigExtraction(_ZedConfigMixin, unittest.TestCase):
    Extractor = MacOSZedRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_MACOS_EXT_MOD}.is_running_as_root", return_value=False), \
             patch.object(Path, "home", return_value=self.root):
            yield


class TestLinuxZedConfigExtraction(_ZedConfigMixin, unittest.TestCase):
    Extractor = LinuxZedRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_LINUX_EXT_MOD}.get_linux_user_homes", return_value=[self.root]):
            yield

    def test_linux_subclasses_macos_extractor(self):
        self.assertTrue(issubclass(LinuxZedRulesExtractor, MacOSZedRulesExtractor))


if __name__ == "__main__":
    unittest.main()
