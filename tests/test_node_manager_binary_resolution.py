"""POSIX Node version-manager paths in ``resolve_npm_global_tool_bin``.

A CLI installed with ``npm install -g`` lands wherever the user's Node version
manager puts it, so the resolver probes a fixed list of per-manager locations.
That list is the ONLY route to a binary on an MDM fleet: the ``which`` backstop
runs only when the scan walks the scanner's own home, and a root scan never
does. A path missing here is a silent false negative for every tool.

All cases pass ``is_root=True``. That is the fleet case, and it also skips the
two scanner-scoped candidates (``npm prefix -g`` and ``/opt/homebrew/bin``) by
design, so a real binary on the box running the tests cannot answer in place of
the hermetic home.
"""

import os
import tempfile
import unittest
from pathlib import Path

from scripts.coding_discovery_tools.utils import resolve_npm_global_tool_bin

# manager -> install path under the home, as each manager actually lays it out.
MANAGER_PATHS = {
    "volta": ".volta/bin/{tool}",
    "pnpm-macos": "Library/pnpm/{tool}",
    "pnpm-linux": ".local/share/pnpm/{tool}",
    "fnm": ".local/share/fnm/node-versions/v22.3.0/installation/bin/{tool}",
    "asdf": ".asdf/shims/{tool}",
    "mise": ".local/share/mise/installs/node/22.3.0/bin/{tool}",
    "nvm": ".nvm/versions/node/v22.3.0/bin/{tool}",
    "npm-global": ".npm-global/bin/{tool}",
}


class TestNodeManagerBinaryResolution(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _plant(self, rel, mode=0o755):
        binary = self.home / rel
        binary.parent.mkdir(parents=True, exist_ok=True)
        binary.write_text("#!/bin/sh\necho 1.0.0\n")
        binary.chmod(mode)
        return binary

    def test_every_manager_layout_resolves(self):
        for manager, template in MANAGER_PATHS.items():
            with self.subTest(manager=manager):
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                self.home = Path(self.tmp.name)
                binary = self._plant(template.format(tool="claude"))
                self.assertEqual(
                    resolve_npm_global_tool_bin("claude", self.home, True), str(binary)
                )

    def test_every_tool_sharing_the_resolver_is_covered(self):
        for tool in ("claude", "gemini", "junie", "cursor-agent", "copilot", "openclaw"):
            with self.subTest(tool=tool):
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                self.home = Path(self.tmp.name)
                binary = self._plant(f".volta/bin/{tool}")
                self.assertEqual(
                    resolve_npm_global_tool_bin(tool, self.home, True), str(binary)
                )

    def test_empty_home_resolves_to_none(self):
        self.assertIsNone(resolve_npm_global_tool_bin("claude", self.home, True))

    def test_config_dir_without_binary_resolves_to_none(self):
        (self.home / ".claude").mkdir()
        (self.home / ".claude" / "settings.json").write_text("{}")
        self.assertIsNone(resolve_npm_global_tool_bin("claude", self.home, True))

    @unittest.skipIf(os.name == "nt", "no execute bit to clear, so X_OK stays true")
    def test_non_executable_binary_resolves_to_none(self):
        self._plant(".volta/bin/claude", mode=0o644)
        self.assertIsNone(resolve_npm_global_tool_bin("claude", self.home, True))

    @unittest.skipIf(os.name == "nt", "chmod(0o000) does not deny on Windows")
    def test_version_dir_walk_survives_an_unreadable_sibling(self):
        self._plant(".local/share/mise/installs/node/22.3.0/bin/claude")
        denied = self.home / ".local" / "share" / "fnm" / "node-versions"
        denied.mkdir(parents=True)
        denied.chmod(0o000)
        self.addCleanup(denied.chmod, 0o755)
        self.assertIsNotNone(resolve_npm_global_tool_bin("claude", self.home, True))


if __name__ == "__main__":
    unittest.main()
