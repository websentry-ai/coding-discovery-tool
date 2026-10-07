"""A CLI's version recovered from its install path when discovery can't run it.

Under an MDM scan (root) ``<tool> --version`` is refused for user-writable
binaries and PATH is the scanner's, so most CLIs came back "Unknown". These build
the real on-disk layouts (Homebrew keg, native installer, editor extension, npm
via nvm, a Windows npm shim) and pin that the version is read without executing
anything, and that a tool's other versions (node, python) are never mistaken for it.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools import ai_tools_discovery
from scripts.coding_discovery_tools.install_path_version import (
    is_unknown_version,
    version_from_install_path,
)


class _Layout(unittest.TestCase):
    def setUp(self):
        # Under HOME: the hardened reader checks the file belongs to the home's owner.
        self.tmp = tempfile.TemporaryDirectory(dir=Path.home())
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        never = patch("subprocess.run", side_effect=AssertionError("a binary was executed"))
        never.start()
        self.addCleanup(never.stop)

    def file(self, rel, text="#!/bin/sh\n"):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def link(self, rel, target):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        return path

    def package(self, package_dir_rel, version, name="pkg"):
        self.file(f"{package_dir_rel}/package.json", json.dumps({"name": name, "version": version}))

    def version(self, path):
        return version_from_install_path(path, self.home)


@unittest.skipIf(os.name == "nt", "POSIX symlink layouts")
class TestPathLayouts(_Layout):
    def test_homebrew_cask(self):
        self.file("opt/homebrew/Caskroom/codex/0.139.0/bin/codex")
        link = self.link("opt/homebrew/bin/codex", "../Caskroom/codex/0.139.0/bin/codex")
        self.assertEqual(self.version(link), "0.139.0")

    def test_homebrew_formula(self):
        self.file("usr/local/Cellar/opencode/0.15.2/bin/opencode")
        link = self.link("usr/local/bin/opencode", "../Cellar/opencode/0.15.2/bin/opencode")
        self.assertEqual(self.version(link), "0.15.2")

    def test_claude_native_installer(self):
        target = self.file("home/.local/share/claude/versions/2.0.14")
        link = self.link("home/.local/bin/claude", str(target))
        self.assertEqual(self.version(link), "2.0.14")

    def test_cursor_agent_native_installer(self):
        target = self.file("home/.local/share/cursor-agent/versions/2025.09.18-7ae6800/cursor-agent")
        link = self.link("home/.local/bin/cursor-agent", str(target))
        self.assertEqual(self.version(link), "2025.09.18-7ae6800")

    def test_a_link_chain_is_followed(self):
        self.file("opt/homebrew/Caskroom/codex/0.140.1/bin/codex")
        middle = self.link("opt/homebrew/bin/codex", "../Caskroom/codex/0.140.1/bin/codex")
        outer = self.link("home/bin/codex", str(middle))
        self.assertEqual(self.version(outer), "0.140.1")

    def test_editor_extension_binary(self):
        path = self.file("home/.vscode/extensions/anthropic.claude-code-2.0.5-darwin-arm64"
                         "/resources/native-binary/claude")
        self.assertEqual(self.version(path), "2.0.5")

    def test_npm_package_via_nvm(self):
        self.package("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex", "0.141.0")
        self.file("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex/bin/codex.js")
        link = self.link("home/.nvm/versions/node/v22.11.0/bin/codex",
                         "../lib/node_modules/@openai/codex/bin/codex.js")
        self.assertEqual(self.version(link), "0.141.0")

    def test_unscoped_npm_package(self):
        self.package("home/.npm-global/lib/node_modules/opencode-ai", "0.15.9")
        self.file("home/.npm-global/lib/node_modules/opencode-ai/bin/opencode")
        link = self.link("home/.npm-global/bin/opencode", "../lib/node_modules/opencode-ai/bin/opencode")
        self.assertEqual(self.version(link), "0.15.9")


@unittest.skipIf(os.name == "nt", "POSIX symlink layouts")
class TestSelfUpdatingCli(_Layout):
    """Copilot CLI runs the newest runtime it downloaded, not its npm package's version."""

    def _copilot(self, package_version):
        prefix = "opt/homebrew/lib/node_modules/@github/copilot"
        self.package(prefix, package_version, name="@github/copilot")
        self.file(f"{prefix}/npm-loader.js")
        return self.link("opt/homebrew/bin/copilot", "../lib/node_modules/@github/copilot/npm-loader.js")

    def test_newest_cached_runtime_wins(self):
        link = self._copilot("1.0.56")
        for version in ("1.0.57", "1.0.63", "1.0.9"):
            (self.home / "Library/Caches/copilot/pkg/darwin-arm64" / version).mkdir(parents=True)
        (self.home / "Library/Caches/copilot/pkg/tmp").mkdir()
        self.assertEqual(self.version(link), "1.0.63")

    def test_no_cache_falls_back_to_the_package(self):
        self.assertEqual(self.version(self._copilot("1.0.56")), "1.0.56")

    def test_an_older_cache_never_lowers_the_version(self):
        link = self._copilot("1.0.56")
        (self.home / "Library/Caches/copilot/pkg/darwin-arm64/1.0.40").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.56")

    def test_other_packages_ignore_the_copilot_cache(self):
        (self.home / "Library/Caches/copilot/pkg/darwin-arm64/9.9.9").mkdir(parents=True)
        self.package("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex", "0.141.0",
                     name="@openai/codex")
        self.file("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex/bin/codex.js")
        link = self.link("home/.nvm/versions/node/v22.11.0/bin/codex",
                         "../lib/node_modules/@openai/codex/bin/codex.js")
        self.assertEqual(self.version(link), "0.141.0")


@unittest.skipIf(os.name == "nt", "POSIX symlink layouts")
class TestNeverMistakesAnotherVersion(_Layout):
    def test_node_version_in_an_nvm_path_is_not_the_tool_version(self):
        self.file("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex/bin/codex.js")
        link = self.link("home/.nvm/versions/node/v22.11.0/bin/codex",
                         "../lib/node_modules/@openai/codex/bin/codex.js")
        self.assertIsNone(self.version(link))  # no package.json: unknown, not "22.11.0"

    def test_pyenv_versions_dir_is_not_a_native_installer(self):
        path = self.file("home/.pyenv/versions/3.12.1/bin/aider")
        self.assertIsNone(self.version(path))

    def test_a_config_directory_has_no_version(self):
        (self.home / ".claude").mkdir()
        self.assertIsNone(self.version(self.home / ".claude"))

    def test_a_link_loop_ends(self):
        a = self.root / "home/bin/a"
        a.parent.mkdir(parents=True)
        os.symlink(str(self.root / "home/bin/b"), a)
        os.symlink(str(a), self.root / "home/bin/b")
        self.assertIsNone(self.version(a))

    def test_files_are_read_only_through_the_owner_checked_reader(self):
        """Every read goes through the hardened reader, which requires the opened
        file to belong to the scanned home's owner, so the root scan can't be
        pointed at another account's files."""
        self.package("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex", "0.141.0")
        self.file("home/.nvm/versions/node/v22.11.0/lib/node_modules/@openai/codex/bin/codex.js")
        link = self.link("home/.nvm/versions/node/v22.11.0/bin/codex",
                         "../lib/node_modules/@openai/codex/bin/codex.js")
        from scripts.coding_discovery_tools import install_path_version as mod
        with patch.object(mod, "_read_own_regular_file", return_value=None) as reader, \
                patch("builtins.open", side_effect=AssertionError("unchecked read")):
            self.assertIsNone(self.version(link))
        self.assertEqual(reader.call_args.args[1], self.home)


class TestWindowsShim(_Layout):
    def test_npm_cmd_shim_names_its_package(self):
        npm = "home/AppData/Roaming/npm"
        self.package(f"{npm}/node_modules/@openai/codex", "0.142.0")
        shim = self.file(f"{npm}/codex.cmd",
                         '@ECHO off\r\n"%_prog%" "%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n')
        self.assertEqual(self.version(shim), "0.142.0")

    def test_a_shim_without_a_package_is_unknown(self):
        shim = self.file("home/AppData/Roaming/npm/tool.cmd", "@ECHO off\r\necho hi\r\n")
        self.assertIsNone(self.version(shim))


class TestIsUnknownVersion(unittest.TestCase):
    def test_values(self):
        for value in (None, "", "Unknown", "unknown", "  ", 3):
            self.assertTrue(is_unknown_version(value), value)
        for value in ("0.139.0", "codex-cli 0.139.0"):
            self.assertFalse(is_unknown_version(value), value)


@unittest.skipIf(os.name == "nt", "POSIX symlink layouts")
class TestDetectAllToolsFillsVersions(_Layout):
    """The real discovery loop applies the fallback to every detector's result."""

    def _discover(self, *results):
        detectors = []
        for i, result in enumerate(results):
            detector = type(f"D{i}", (), {"tool_name": f"Tool{i}", "user_home": None})()
            detectors.append(detector)
        discovery = object.__new__(ai_tools_discovery.AIToolsDetector)
        discovery._tool_detectors = detectors
        with patch.object(ai_tools_discovery, "detect_tool_for_user", side_effect=list(results)):
            return discovery.detect_all_tools(user_home=self.home)

    def test_unknown_is_filled_and_known_is_kept(self):
        self.file("opt/homebrew/Caskroom/codex/0.139.0/bin/codex")
        brew = self.link("opt/homebrew/bin/codex", "../Caskroom/codex/0.139.0/bin/codex")
        tools = self._discover(
            {"name": "Codex", "version": "Unknown", "install_path": str(brew)},
            {"name": "Cursor", "version": "1.7.0", "install_path": str(brew)},
            [{"name": "Junie", "version": "unknown", "install_path": str(brew)}],
        )
        self.assertEqual([t["version"] for t in tools], ["0.139.0", "1.7.0", "0.139.0"])

    def test_a_failing_fallback_keeps_the_tool(self):
        with patch.object(ai_tools_discovery, "version_from_install_path", side_effect=RuntimeError):
            tools = self._discover({"name": "Codex", "version": "Unknown", "install_path": "/x"})
        self.assertEqual(tools, [{"name": "Codex", "version": "Unknown", "install_path": "/x"}])

    def test_no_install_path_is_left_alone(self):
        tools = self._discover({"name": "Codex", "version": "Unknown"})
        self.assertEqual(tools[0]["version"], "Unknown")


if __name__ == "__main__":
    unittest.main()
