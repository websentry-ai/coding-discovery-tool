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

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools import ai_tools_discovery
from scripts.coding_discovery_tools.install_path_version import (
    is_unknown_version,
    runtime_version,
    version_from_install_path,
)

utils_mod._SENTRY_DSN = ""


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
        # Same shape `--version` stores (extract_version_number drops the build hash).
        self.assertEqual(self.version(link), "2025.09.18")

    def test_a_link_chain_is_followed(self):
        self.file("opt/homebrew/Caskroom/codex/0.140.1/bin/codex")
        middle = self.link("opt/homebrew/bin/codex", "../Caskroom/codex/0.140.1/bin/codex")
        outer = self.link("home/bin/codex", str(middle))
        self.assertEqual(self.version(outer), "0.140.1")

    def test_an_npm_cli_under_homebrew_node_reports_the_package_not_node(self):
        prefix = "opt/homebrew/Cellar/node/22.11.0/lib/node_modules/@google/gemini-cli"
        self.package(prefix, "0.9.0")
        self.file(f"{prefix}/dist/index.js")
        link = self.link("opt/homebrew/bin/gemini",
                         "../Cellar/node/22.11.0/lib/node_modules/@google/gemini-cli/dist/index.js")
        self.assertEqual(self.version(link), "0.9.0")

    def test_an_npm_cli_under_homebrew_node_without_package_json_is_unknown(self):
        self.file("opt/homebrew/Cellar/node/22.11.0/lib/node_modules/@google/gemini-cli/dist/index.js")
        link = self.link("opt/homebrew/bin/gemini",
                         "../Cellar/node/22.11.0/lib/node_modules/@google/gemini-cli/dist/index.js")
        self.assertIsNone(self.version(link))  # never node's 22.11.0

    def test_the_tools_own_keg_backs_up_an_unreadable_package_json(self):
        """A root-owned formula fails the owner check on package.json; its own keg
        still names the version."""
        prefix = "opt/homebrew/Cellar/gemini-cli/0.9.0/libexec/lib/node_modules/@google/gemini-cli"
        self.file(f"{prefix}/dist/index.js")
        link = self.link("opt/homebrew/bin/gemini",
                         "../Cellar/gemini-cli/0.9.0/libexec/lib/node_modules/@google/gemini-cli/dist/index.js")
        self.assertEqual(self.version(link), "0.9.0")

    def test_a_runtime_keg_is_never_the_tools_version(self):
        for keg in ("node/22.11.0", "node@20/20.18.1", "python@3.12/3.12.7"):
            with self.subTest(keg=keg):
                path = self.file(f"opt/homebrew/Cellar/{keg}/bin/gemini")
                self.assertIsNone(self.version(path))

    def test_homebrew_revision_and_build_suffixes_are_dropped(self):
        for keg, expected in (("Cellar/opencode/0.15.2_1", "0.15.2"), ("Caskroom/codex/1.2.3,4567", "1.2.3")):
            with self.subTest(keg=keg):
                self.file(f"opt/homebrew/{keg}/bin/tool")
                self.assertEqual(self.version(self.root / f"opt/homebrew/{keg}/bin/tool"), expected)

    def test_windows_native_cursor_agent(self):
        path = self.file("home/AppData/Local/cursor-agent/versions/2026.05.28-7ae6800/cursor-agent.exe")
        self.assertEqual(self.version(path), "2026.05.28")

    def test_prerelease_extension_and_a_path_ending_at_the_folder(self):
        for rel, expected in ((".vscode/extensions/github.copilot-chat-1.2.3-alpha.1/dist/x", "1.2.3"),
                              (".vscode/extensions/anthropic.claude-code-2.0.5", "2.0.5")):
            with self.subTest(rel=rel):
                path = self.home / rel
                path.mkdir(parents=True, exist_ok=True)
                self.assertEqual(self.version(path), expected)

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

    def version(self, path, home=None):
        """What discovery reports for one user: their newer runtime, else the package."""
        home = home or self.home
        return runtime_version(path, home) or version_from_install_path(path, home)

    def _cache(self, root="Library/Caches/copilot/pkg", platform_dir=None):
        from scripts.coding_discovery_tools.install_path_version import _platform_dir
        return self.home / root / (platform_dir or _platform_dir())

    def test_newest_cached_runtime_wins(self):
        link = self._copilot("1.0.56")
        for version in ("1.0.57", "1.0.63", "1.0.9"):
            (self._cache() / version).mkdir(parents=True)
        (self._cache().parent / "tmp").mkdir()
        self.assertEqual(self.version(link), "1.0.63")

    def test_the_copilot_home_pkg_dir_counts_too(self):
        link = self._copilot("1.0.56")
        (self._cache(".copilot/pkg") / "1.0.70").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.70")

    def test_another_platforms_runtime_is_ignored(self):
        link = self._copilot("1.0.56")
        (self._cache(platform_dir="plan9-mips") / "9.9.9").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.56")

    def test_a_symlinked_cache_is_not_walked(self):
        link = self._copilot("1.0.56")
        elsewhere = self.root / "elsewhere"
        (elsewhere / self._cache().name / "9.9.9").mkdir(parents=True)
        (self.home / "Library/Caches/copilot").mkdir(parents=True)
        os.symlink(str(elsewhere), self.home / "Library/Caches/copilot/pkg")
        self.assertEqual(self.version(link), "1.0.56")

    def test_an_unreadable_package_json_still_uses_the_cache(self):
        """A root-owned Homebrew npm prefix fails the owner check; the package name
        is still in the path."""
        link = self._copilot("1.0.56")
        (self._cache() / "1.0.63").mkdir(parents=True)
        from scripts.coding_discovery_tools import install_path_version as mod
        with patch.object(mod, "_read_own_regular_file", return_value=None):
            self.assertEqual(self.version(link), "1.0.63")

    def test_a_prerelease_package_beats_an_older_cache(self):
        link = self._copilot("1.0.70-rc.1")
        (self._cache() / "1.0.40").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.70-rc.1")
        for later in ("1.0.70-rc.2", "1.0.70-rc.10"):  # identifiers compare numerically
            (self._cache() / later).mkdir()
            self.assertEqual(self.version(link), later)
        (self._cache() / "1.0.70-rc.9").mkdir()
        self.assertEqual(self.version(link), "1.0.70-rc.10")
        (self._cache() / "1.0.70").mkdir()
        self.assertEqual(self.version(link), "1.0.70")  # the release beats its own prerelease
        (self._cache() / "1.0.80").mkdir()
        self.assertEqual(self.version(link), "1.0.80")

    def test_each_user_gets_their_own_runtime_from_a_shared_install(self):
        """Two users share one Copilot install; each runs the newest runtime in
        their own cache."""
        link = self._copilot("1.0.56")
        from scripts.coding_discovery_tools.install_path_version import _platform_dir
        other = self.root / "other"
        (self._cache() / "1.0.63").mkdir(parents=True)
        (other / "Library/Caches/copilot/pkg" / _platform_dir() / "1.0.70").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.63")
        self.assertEqual(self.version(link, other), "1.0.70")

    def test_a_detector_version_gives_way_to_what_the_user_runs(self):
        """Windows' detector reports the npm package version, and macOS probes the shared
        binary as root; either way the user runs the newer of package and their cache."""
        link = self._copilot("1.0.56")
        (self._cache() / "1.0.63").mkdir(parents=True)
        for detected in ("1.0.56", "1.0.90", "Unknown"):
            info = {"name": "GitHub Copilot CLI", "version": detected, "install_path": str(link)}
            ai_tools_discovery._fill_version_from_install_path(info, self.home)
            self.assertEqual((info["version"], info["_install_version"]), ("1.0.63", "1.0.56"), detected)

    def test_a_root_probe_never_stands_in_for_an_unreadable_package(self):
        """Nobody can read package.json; the scanner's probe of the shared binary is
        not what Carol runs, and must not become anyone's floor."""
        from scripts.coding_discovery_tools import install_path_version as mod
        link = self._copilot("1.0.56")
        carol = self.root / "carol"
        (self._cache() / "1.0.63").mkdir(parents=True)
        rows = {}
        with patch.object(mod, "_read_own_regular_file", return_value=None), \
                patch.object(mod, "_is_safe_exec_path", return_value=False):
            for name, home in (("alice", self.home), ("carol", carol)):
                rows[name] = {"name": "GitHub Copilot CLI", "version": "1.0.90", "install_path": str(link)}
                ai_tools_discovery._fill_version_from_install_path(rows[name], home)
        self.assertEqual(rows["alice"]["version"], "1.0.63")
        self.assertTrue(is_unknown_version(rows["carol"]["version"]))
        self.assertNotIn("_install_version", rows["carol"])
        kept = rows["carol"]
        for name, home in (("carol", carol), ("alice", self.home)):
            ai_tools_discovery._keep_user_version(kept, rows[name], home)
        report = {"version": kept["version"]}
        self.assertEqual(ai_tools_discovery._with_user_version(kept, report, self.home)["version"], "1.0.63")
        self.assertTrue(is_unknown_version(ai_tools_discovery._with_user_version(kept, report, carol)["version"]))

    def test_a_non_updating_cli_keeps_its_detected_version(self):
        self.package("opt/homebrew/lib/node_modules/@openai/codex", "0.139.0", name="@openai/codex")
        self.file("opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js")
        link = self.link("opt/homebrew/bin/codex", "../lib/node_modules/@openai/codex/bin/codex.js")
        self.assertIsNone(runtime_version(link, self.home))
        info = {"name": "Codex", "version": "0.140.0", "install_path": str(link)}
        ai_tools_discovery._fill_version_from_install_path(info, self.home)
        self.assertEqual(info["version"], "0.140.0")

    def test_a_cache_dir_swapped_for_a_link_is_not_followed(self):
        from scripts.coding_discovery_tools import install_path_version as mod
        link = self._copilot("1.0.56")
        elsewhere = self.root / "elsewhere"
        (elsewhere / "9.9.9").mkdir(parents=True)
        self._cache().parent.mkdir(parents=True)
        os.symlink(str(elsewhere), self._cache())
        with patch.object(mod, "_any_redirect", return_value=False):  # the check passed; then the swap
            self.assertEqual(self.version(link), "1.0.56")

    def test_a_root_owned_shared_install_still_reports_its_package(self):
        """The owner check refuses root's package.json; a prefix no other account can
        write is read anyway, so a shared install isn't left unknown."""
        link = self._copilot("1.0.56")
        from scripts.coding_discovery_tools import install_path_version as mod
        with patch.object(mod, "_read_own_regular_file", return_value=None), \
                patch.object(mod, "_is_safe_exec_path", return_value=True):
            self.assertEqual(self.version(link), "1.0.56")

    def test_a_hostile_cache_folder_name_is_not_reported(self):
        link = self._copilot("1.0.56")
        for name in ('1.0.999<img src=x onerror=alert(1)>', "1.0.99\nforged", "1.0.98" + "x" * 80):
            try:
                (self._cache() / name).mkdir(parents=True)
            except OSError:
                pass
        self.assertEqual(self.version(link), "1.0.56")

    def test_no_cache_falls_back_to_the_package(self):
        self.assertEqual(self.version(self._copilot("1.0.56")), "1.0.56")

    def test_an_older_cache_never_lowers_the_version(self):
        link = self._copilot("1.0.56")
        (self._cache() / "1.0.40").mkdir(parents=True)
        self.assertEqual(self.version(link), "1.0.56")

    def test_other_packages_ignore_the_copilot_cache(self):
        (self.home / "Library/Caches/copilot/pkg" / "x" / "9.9.9").mkdir(parents=True)
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
        real_open = os.open

        def no_package_open(path, *args, **kwargs):
            if str(path).endswith("package.json"):
                raise AssertionError("unchecked read")
            return real_open(path, *args, **kwargs)

        with patch.object(mod, "_read_own_regular_file", return_value=None) as reader, \
                patch.object(mod, "_is_safe_exec_path", return_value=False) as planted_proof, \
                patch("builtins.open", side_effect=AssertionError("unchecked read")), \
                patch.object(mod.os, "open", no_package_open):
            self.assertIsNone(self.version(link))
        self.assertEqual(reader.call_args.args[1], self.home)
        planted_proof.assert_called()


class TestWindowsShim(_Layout):
    def test_npm_cmd_shim_names_its_package(self):
        npm = "home/AppData/Roaming/npm"
        self.package(f"{npm}/node_modules/@openai/codex", "0.142.0")
        shim = self.file(f"{npm}/codex.cmd",
                         '@ECHO off\r\n"%_prog%" "%dp0%\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n')
        self.assertEqual(self.version(shim), "0.142.0")

    def test_a_shim_under_node_modules_bin_is_read_as_a_shim(self):
        """The legacy local install is ~/.claude/local/node_modules/.bin/claude.cmd;
        .bin is not a package."""
        base = "home/.claude/local/node_modules"
        self.package(f"{base}/@anthropic-ai/claude-code", "1.0.98")
        shim = self.file(f"{base}/.bin/claude.cmd",
                         '@ECHO off\r\n"%_prog%" "%dp0%\\..\\@anthropic-ai\\claude-code\\cli.js" %*\r\n')
        self.assertEqual(self.version(shim), "1.0.98")

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
class TestPerUserVersionThroughDedup(unittest.TestCase):
    """main() keeps one row per (tool, install path); a user whose recovered version
    differs gets theirs in their own report."""

    def _dedup(self, *users):
        """main()'s loop: the first user's row is kept, every user is recorded."""
        kept = None
        for home, version, install_version in users:
            detected = {"name": "GitHub Copilot CLI", "install_path": "/opt/homebrew/bin/copilot",
                        "version": version, "_install_version": install_version}
            kept = kept or detected
            ai_tools_discovery._keep_user_version(kept, detected, Path(home))
        report = {"name": "GitHub Copilot CLI", "version": kept["version"], "projects": []}
        return {home: ai_tools_discovery._with_user_version(kept, report, Path(home))["version"]
                for home, _, _ in users}

    def test_each_user_reports_their_own_runtime(self):
        self.assertEqual(
            self._dedup(("/Users/alice", "1.0.63", "1.0.56"), ("/Users/bob", "1.0.70", "1.0.56")),
            {"/Users/alice": "1.0.63", "/Users/bob": "1.0.70"})

    def test_a_user_without_a_runtime_never_inherits_anothers(self):
        """Either scan order: Carol's unreadable install falls back to the package
        version someone could read, not Alice's runtime."""
        alice = ("/Users/alice", "1.0.63", "1.0.56")
        carol = ("/Users/carol", "Unknown", None)
        expected = {"/Users/alice": "1.0.63", "/Users/carol": "1.0.56"}
        self.assertEqual(self._dedup(alice, carol), expected)
        self.assertEqual(self._dedup(carol, alice), expected)

    def test_an_older_cache_never_lowers_a_user_below_the_package(self):
        """Bob can't read the shared package; his stale cache is older than the loader
        he runs, which Alice could read."""
        self.assertEqual(
            self._dedup(("/Users/bob", "1.0.40", None), ("/Users/alice", "1.0.63", "1.0.56")),
            {"/Users/bob": "1.0.56", "/Users/alice": "1.0.63"})

    def test_unknown_everywhere_stays_unknown(self):
        self.assertEqual(self._dedup(("/Users/carol", "Unknown", None), ("/Users/dan", "Unknown", None)),
                         {"/Users/carol": "Unknown", "/Users/dan": "Unknown"})

    def test_the_per_user_map_never_reaches_the_report(self):
        kept = {"name": "GitHub Copilot CLI", "install_path": "/x", "version": "1.0.63"}
        ai_tools_discovery._keep_user_version(kept, dict(kept, _install_version="1.0.56"), Path("/Users/alice"))
        detector = object.__new__(ai_tools_discovery.AIToolsDetector)
        with patch.object(ai_tools_discovery, "in_container", return_value=False):
            report = detector.generate_single_tool_report(kept, "dev", "alice")
        self.assertEqual([k for k in report["tools"][0] if k.startswith("_")], [])


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
