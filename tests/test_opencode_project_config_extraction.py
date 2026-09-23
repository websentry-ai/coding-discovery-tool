"""Project-config extraction tests for OpenCode (macOS + Linux).

The original extractor only read ``.opencode/agent/*.md``, so a project
configured purely through ``.opencode/opencode.json`` (the common case) was
never reported. These tests pin:
  1. a project is reported when any of ``.opencode/opencode.json[c]``, the
     project-root sibling ``opencode.json[c]``, or ``.opencode/{agent,agents,
     commands}/*.md`` exists,
  2. a ``.opencode/`` holding only session state is NOT reported,
  3. JSONC content is recorded byte-for-byte and never parsed, and
  4. global ``~/.config/opencode`` files land under the home root with
     ``scope == "user"``.

The extractors are driven at ``_extract_rules_from_opencode_directory`` /
``_extract_global_rules`` so no test ever walks ``/``.
"""

import contextlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.linux.opencode.opencode_rules_extractor import (
    LinuxOpenCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos.opencode.opencode_rules_extractor import (
    MacOSOpenCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list

_MACOS_EXT_MOD = "scripts.coding_discovery_tools.macos.opencode.opencode_rules_extractor"
_LINUX_EXT_MOD = "scripts.coding_discovery_tools.linux.opencode.opencode_rules_extractor"

JSONC_BODY = (
    '{\n'
    '  // model routed through the gateway\n'
    '  "model": "anthropic/claude-sonnet-5",\n'
    '  "permission": { "bash": "ask", },\n'
    '}\n'
)


class _OpenCodeConfigMixin:
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

    def _oc_dir(self) -> Path:
        d = self.proj / ".opencode"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _project_rules(self, oc_dir: Path):
        projects = {}
        self.extractor._extract_rules_from_opencode_directory(oc_dir, projects)
        return build_project_list(projects)

    def _names(self, projects):
        self.assertEqual(len(projects), 1, projects)
        self.assertEqual(Path(projects[0]["project_root"]), self.proj)
        return sorted(r["file_name"] for r in projects[0]["rules"])

    # --- project-level ------------------------------------------------------

    def test_opencode_json_alone_is_reported(self):
        oc = self._oc_dir()
        (oc / "opencode.json").write_text('{"model": "x"}', encoding="utf-8")
        self.assertEqual(self._names(self._project_rules(oc)), ["opencode.json"])

    def test_plural_agents_dir_reported(self):
        oc = self._oc_dir()
        (oc / "agents").mkdir()
        (oc / "agents" / "reviewer.md").write_text("# reviewer", encoding="utf-8")
        self.assertEqual(self._names(self._project_rules(oc)), ["reviewer.md"])

    def test_singular_agent_dir_still_reported(self):
        oc = self._oc_dir()
        (oc / "agent").mkdir()
        (oc / "agent" / "legacy.md").write_text("# legacy", encoding="utf-8")
        self.assertEqual(self._names(self._project_rules(oc)), ["legacy.md"])

    def test_commands_dir_reported(self):
        oc = self._oc_dir()
        (oc / "commands").mkdir()
        (oc / "commands" / "deploy.md").write_text("# deploy", encoding="utf-8")
        self.assertEqual(self._names(self._project_rules(oc)), ["deploy.md"])

    def test_skills_dir_not_duplicated_here(self):
        oc = self._oc_dir()
        (oc / "skills" / "foo").mkdir(parents=True)
        (oc / "skills" / "foo" / "SKILL.md").write_text("# skill", encoding="utf-8")
        self.assertEqual(self._project_rules(oc), [])

    def test_root_sibling_jsonc_recorded_verbatim(self):
        oc = self._oc_dir()
        (self.proj / "opencode.jsonc").write_text(JSONC_BODY, encoding="utf-8")
        projects = self._project_rules(oc)
        self.assertEqual(self._names(projects), ["opencode.jsonc"])
        rule = projects[0]["rules"][0]
        self.assertEqual(rule["content"], JSONC_BODY)
        self.assertEqual(Path(rule["file_path"]), self.proj / "opencode.jsonc")

    def test_all_sources_combined(self):
        oc = self._oc_dir()
        (oc / "opencode.jsonc").write_text("{}", encoding="utf-8")
        (self.proj / "opencode.json").write_text("{}", encoding="utf-8")
        (oc / "agents").mkdir()
        (oc / "agents" / "a.md").write_text("a", encoding="utf-8")
        (oc / "commands").mkdir()
        (oc / "commands" / "c.md").write_text("c", encoding="utf-8")
        self.assertEqual(
            self._names(self._project_rules(oc)),
            ["a.md", "c.md", "opencode.json", "opencode.jsonc"],
        )

    def test_sessions_only_dir_not_reported(self):
        oc = self._oc_dir()
        (oc / "sessions").mkdir()
        (oc / "sessions" / "abc.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._project_rules(oc), [])

    def test_bare_dir_not_reported(self):
        self.assertEqual(self._project_rules(self._oc_dir()), [])

    # --- global -------------------------------------------------------------

    def test_global_config_reported_under_home_with_user_scope(self):
        gdir = self.root / ".config" / "opencode"
        (gdir / "agents").mkdir(parents=True)
        (gdir / "opencode.json").write_text('{"theme": "dark"}', encoding="utf-8")
        (gdir / "agents" / "x.md").write_text("# x", encoding="utf-8")
        projects = {}
        with self._home_ctx():
            self.extractor._extract_global_rules(projects)
        result = build_project_list(projects)
        self.assertEqual(len(result), 1, result)
        self.assertEqual(Path(result[0]["project_root"]), self.root)
        self.assertEqual(
            sorted(r["file_name"] for r in result[0]["rules"]),
            ["opencode.json", "x.md"],
        )
        self.assertTrue(all(r["scope"] == "user" for r in result[0]["rules"]))

    def test_global_missing_dir_reports_nothing(self):
        projects = {}
        with self._home_ctx():
            self.extractor._extract_global_rules(projects)
        self.assertEqual(build_project_list(projects), [])


class TestMacOSOpenCodeConfigExtraction(_OpenCodeConfigMixin, unittest.TestCase):
    Extractor = MacOSOpenCodeRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_MACOS_EXT_MOD}.is_running_as_root", return_value=False), \
             patch.object(Path, "home", return_value=self.root):
            yield


class TestLinuxOpenCodeConfigExtraction(_OpenCodeConfigMixin, unittest.TestCase):
    Extractor = LinuxOpenCodeRulesExtractor

    @contextlib.contextmanager
    def _home_ctx(self):
        with patch(f"{_LINUX_EXT_MOD}.get_linux_user_homes", return_value=[self.root]):
            yield


if __name__ == "__main__":
    unittest.main()
