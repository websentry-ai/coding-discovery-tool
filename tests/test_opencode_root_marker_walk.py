"""Walk-level tests: a project whose only OpenCode config is a root-level
``opencode.json[c]`` (no ``.opencode/`` dir) must be discovered.

This is opencode's primary project-config location, so it is the common case.
The marker files ride the shared directory index (``INDEXED_FILE_MARKERS``) —
the same single pass that indexes hidden dirs — so no extra walk is added.

Fixtures live under a non-hidden dir inside ``$HOME``: the walk deliberately
skips hidden home dirs and OS temp roots, so ``tempfile`` would be pruned.
"""

import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools import project_dir_index
from scripts.coding_discovery_tools.linux.opencode.opencode_rules_extractor import (
    LinuxOpenCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos.opencode.opencode_rules_extractor import (
    MacOSOpenCodeRulesExtractor,
)
from scripts.coding_discovery_tools.macos_extraction_helpers import build_project_list

_LINUX_EXT_MOD = "scripts.coding_discovery_tools.linux.opencode.opencode_rules_extractor"
_NO_SYMLINK = os.name == "nt"


class _RootMarkerWalkMixin:
    run_walk = None  # (self, home, projects) -> None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.base = Path.home() / ("cdt-test-" + uuid.uuid4().hex[:8])
        self.home = self.base / "u"
        self.home.mkdir(parents=True)
        project_dir_index.clear_cache()

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)
        project_dir_index.clear_cache()

    def _build_tree(self):
        code = self.home / "code"
        root_only = code / "root-only"
        root_only.mkdir(parents=True)
        (root_only / "opencode.jsonc").write_text('{ "model": "m" }\n', encoding="utf-8")

        both = code / "both"
        (both / ".opencode").mkdir(parents=True)
        (both / "opencode.json").write_text('{"a": 1}', encoding="utf-8")
        (both / ".opencode" / "opencode.json").write_text('{"b": 2}', encoding="utf-8")

        nothing = code / "nothing"
        nothing.mkdir()
        (nothing / "README.md").write_text("x", encoding="utf-8")

        if not _NO_SYMLINK:
            linked = code / "linked"
            linked.mkdir()
            os.symlink(root_only / "opencode.jsonc", linked / "opencode.json")
        return code

    def _results(self):
        projects = {}
        self.run_walk(self.home, projects)
        return {
            str(Path(p["project_root"]).relative_to(self.home)):
                sorted(str(Path(r["file_path"]).relative_to(self.home)) for r in p["rules"])
            for p in build_project_list(projects)
        }

    def test_root_only_project_discovered(self):
        self._build_tree()
        out = self._results()
        self.assertEqual(out.get("code/root-only"), ["code/root-only/opencode.jsonc"])

    def test_both_routes_no_duplicates(self):
        self._build_tree()
        out = self._results()
        self.assertEqual(
            out.get("code/both"),
            ["code/both/.opencode/opencode.json", "code/both/opencode.json"],
        )

    def test_dir_without_config_not_reported(self):
        self._build_tree()
        self.assertNotIn("code/nothing", self._results())

    @unittest.skipIf(_NO_SYMLINK, "symlink creation needs a privilege on Windows")
    def test_symlinked_marker_ignored(self):
        self._build_tree()
        self.assertNotIn("code/linked", self._results())


class TestMacOSRootMarkerWalk(_RootMarkerWalkMixin, unittest.TestCase):
    def run_walk(self, home, projects):
        MacOSOpenCodeRulesExtractor()._extract_project_level_rules(home, projects)


class TestLinuxRootMarkerWalk(_RootMarkerWalkMixin, unittest.TestCase):
    def run_walk(self, home, projects):
        with patch(f"{_LINUX_EXT_MOD}.get_linux_user_homes", return_value=[home]):
            LinuxOpenCodeRulesExtractor()._extract_project_level_rules(projects)


if __name__ == "__main__":
    unittest.main()
