"""Config-extraction tests for the pi coding agent (macOS + Linux).

Pins three things:
  1. a project is reported only when it carries a real ``.pi/settings.json``
     (a bare ``.pi/`` holding only session state is NOT an install signal),
  2. every documented project/global config file is collected, and
  3. ``auth.json`` — which holds OAuth/API credentials — is never read and its
     contents never reach the payload.

The extractors are driven at ``_extract_rules_from_pi_directory`` /
``_extract_global_rules`` so no test ever walks ``/``.
"""

import contextlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.pi.pi_rules_extractor import LinuxPiRulesExtractor
from scripts.coding_discovery_tools.macos.pi.pi_rules_extractor import MacOSPiRulesExtractor
from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list

_MACOS_EXT_MOD = "scripts.coding_discovery_tools.macos.pi.pi_rules_extractor"
_LINUX_EXT_MOD = "scripts.coding_discovery_tools.linux.pi.pi_rules_extractor"

SECRET = "sk-secret-token-do-not-leak"


class _PiConfigMixin:
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

    def _pi_dir(self) -> Path:
        pi_dir = self.proj / ".pi"
        pi_dir.mkdir(parents=True, exist_ok=True)
        return pi_dir

    def _project_rules(self, pi_dir: Path):
        projects = {}
        self.extractor._extract_rules_from_pi_directory(pi_dir, projects)
        return build_project_list(projects)

    # --- project-level ----------------------------------------------------

    def test_settings_json_reports_project(self):
        pi_dir = self._pi_dir()
        (pi_dir / "settings.json").write_text('{"model": "pi-1"}', encoding="utf-8")
        projects = self._project_rules(pi_dir)
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(self.proj))
        self.assertIn("settings.json", [r["file_name"] for r in projects[0]["rules"]])

    def test_bare_pi_directory_not_reported(self):
        """A ``.pi/`` holding only session state is not a configured project."""
        pi_dir = self._pi_dir()
        (pi_dir / "sessions").mkdir()
        (pi_dir / "sessions" / "abc.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._project_rules(pi_dir), [])

    def test_all_project_config_files_collected(self):
        pi_dir = self._pi_dir()
        (pi_dir / "settings.json").write_text("{}", encoding="utf-8")
        (pi_dir / "SYSTEM.md").write_text("# system", encoding="utf-8")
        (pi_dir / "APPEND_SYSTEM.md").write_text("# append", encoding="utf-8")
        projects = self._project_rules(pi_dir)
        self.assertEqual(len(projects), 1)
        names = sorted(r["file_name"] for r in projects[0]["rules"])
        self.assertEqual(names, ["APPEND_SYSTEM.md", "SYSTEM.md", "settings.json"])

    def test_auth_json_never_read(self):
        """SECURITY: ``.pi/auth.json`` holds OAuth/API credentials."""
        pi_dir = self._pi_dir()
        (pi_dir / "settings.json").write_text("{}", encoding="utf-8")
        (pi_dir / "auth.json").write_text(
            json.dumps({"access_token": SECRET}), encoding="utf-8"
        )
        projects = self._project_rules(pi_dir)
        self.assertEqual(len(projects), 1)
        self.assertNotIn("auth.json", [r["file_name"] for r in projects[0]["rules"]])
        self.assertNotIn(SECRET, json.dumps(projects))

    def test_content_recorded_verbatim(self):
        """No JSON parsing: settings may be JSONC; content is raw bytes-in-text."""
        raw = '{\n  // comment\n  "model": "pi-1",\n}\n'
        pi_dir = self._pi_dir()
        (pi_dir / "settings.json").write_text(raw, encoding="utf-8")
        projects = self._project_rules(pi_dir)
        rule = next(r for r in projects[0]["rules"] if r["file_name"] == "settings.json")
        self.assertEqual(rule["content"], raw)

    # --- global -----------------------------------------------------------

    def _global_rules(self):
        projects = {}
        with self._home_ctx():
            self.extractor._extract_global_rules(projects)
        return build_project_list(projects)

    def test_global_settings_reported_under_home_with_user_scope(self):
        agent = self.root / ".pi" / "agent"
        agent.mkdir(parents=True)
        (agent / "settings.json").write_text("{}", encoding="utf-8")
        (agent / "models.json").write_text("{}", encoding="utf-8")
        projects = self._global_rules()
        self.assertEqual(len(projects), 1)
        self.assertEqual(projects[0]["project_root"], str(self.root))
        settings = next(
            r for r in projects[0]["rules"] if r["file_name"] == "settings.json"
        )
        self.assertEqual(settings["scope"], "user")
        self.assertIn("models.json", [r["file_name"] for r in projects[0]["rules"]])

    def test_global_bare_agent_dir_not_reported(self):
        agent = self.root / ".pi" / "agent"
        (agent / "sessions").mkdir(parents=True)
        self.assertEqual(self._global_rules(), [])

    def test_global_auth_json_never_read(self):
        agent = self.root / ".pi" / "agent"
        agent.mkdir(parents=True)
        (agent / "settings.json").write_text("{}", encoding="utf-8")
        (agent / "auth.json").write_text(
            json.dumps({"refresh_token": SECRET}), encoding="utf-8"
        )
        projects = self._global_rules()
        self.assertNotIn(SECRET, json.dumps(projects))
        self.assertNotIn(
            "auth.json", [r["file_name"] for p in projects for r in p["rules"]]
        )


class TestMacOSPiConfigExtraction(_PiConfigMixin, unittest.TestCase):
    Extractor = MacOSPiRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_MACOS_EXT_MOD}.is_running_as_root", return_value=False), \
             patch.object(Path, "home", return_value=self.root):
            yield


class TestLinuxPiConfigExtraction(_PiConfigMixin, unittest.TestCase):
    Extractor = LinuxPiRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_LINUX_EXT_MOD}.get_linux_user_homes", return_value=[self.root]):
            yield

    def test_linux_subclasses_macos_extractor(self):
        self.assertTrue(issubclass(LinuxPiRulesExtractor, MacOSPiRulesExtractor))


if __name__ == "__main__":
    unittest.main()
