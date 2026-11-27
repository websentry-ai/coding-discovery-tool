"""Root/all-users scans must file each user's skills under that user's home.

Under MDM (Jamf, Intune, ...) discovery runs as root, so ``Path.home()`` is the
scanner's home (``/var/root``, ``/root``), not any real user's. ``main()`` runs
``process_single_tool`` once for all users and then ``filter_tool_projects_by_user``
for each user, which keeps only projects under that user's home. Any tool that
keys user-level data by ``Path.home()`` therefore loses ALL of it on a root scan.
The backend then replaces the user's stored rows with the empty report, so a
root scan wipes what an earlier user scan stored.

This happened for Claude Code skills (fixed in #94), then again for Claude Cowork
skills and Claude Code / Cursor plugin-bundled skills. These tests run the real
``process_single_tool`` -> ``filter_tool_projects_by_user`` pair with two users
under a simulated root scan, and add a static guard against the pattern.
"""

import ast
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.coding_discovery_tools.ai_tools_discovery as atd
import scripts.coding_discovery_tools.path_containment as path_containment
import scripts.coding_discovery_tools.utils as utils_mod
import scripts.coding_discovery_tools.macos.claude_cowork.skills_extractor as cowork_se
from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools.macos.claude_cowork.skills_extractor import (
    MacOSClaudeCoworkSkillsExtractor,
)

USERS = ("alice", "bob")


def _write_skill(skill_dir: Path, name: str) -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: test skill {name}\n---\n# {name}\n", encoding="utf-8"
    )


def _symlink(test: unittest.TestCase, link: Path, target: Path) -> None:
    """Create ``link`` -> ``target`` or skip (Windows CI may lack symlink rights)."""
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(str(target), str(link), target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as e:
        test.skipTest(f"symlinks unavailable: {e}")


def _skill_names(tool: dict) -> set:
    return {s.get("skill_name") for p in tool.get("projects", []) for s in p.get("skills", [])}


def _plugin_names(tool: dict) -> set:
    return {p.get("plugin_name") for p in tool.get("plugins", [])}


class _RootScanCase(unittest.TestCase):
    """Two user homes plus a separate scanner home that plays root's home."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="root-scan-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.scanner_home = self.tmp / "var-root"
        self.scanner_home.mkdir()
        self.homes = {u: self.tmp / "home" / u for u in USERS}
        for home in self.homes.values():
            home.mkdir(parents=True)
        # Path.home() reads HOME (POSIX) or USERPROFILE (Windows). No real Sentry
        # events from extraction error paths.
        for patch in (
            mock.patch.dict(os.environ, {"HOME": str(self.scanner_home),
                                         "USERPROFILE": str(self.scanner_home),
                                         "AI_DISCOVERY_SENTRY_DSN": ""}),
            mock.patch.object(utils_mod, "_SENTRY_DSN", ""),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.assertEqual(Path.home(), self.scanner_home)

    def assert_each_user_keeps_own_skills(self, processed: dict, expected: dict) -> None:
        for user, home in self.homes.items():
            kept = _skill_names(self.detector.filter_tool_projects_by_user(processed, home))
            self.assertEqual(kept, expected[user], f"{user} lost or gained skills on a root scan")
        scanner_keys = [p["path"] for p in processed.get("projects", [])
                        if Path(p["path"]) == self.scanner_home]
        self.assertEqual(scanner_keys, [], "user data was keyed under the scanner's home")

    def assert_each_user_reports_only_own_plugins(self, processed: dict, expected: dict) -> None:
        for user, home in self.homes.items():
            filtered = self.detector.filter_tool_projects_by_user(processed, home)
            self.assertEqual(_plugin_names(filtered), expected[user], f"{user} reports another user's plugins")
            self.assertNotIn("_plugins_by_owner", filtered)
            if not expected[user]:
                self.assertNotIn("plugins", filtered)
            report = self.detector.generate_single_tool_report(filtered, "dev", user)["tools"][0]
            self.assertEqual([k for k in report if k.startswith("_")], [])


class TestCoworkSkillsRootScan(_RootScanCase):
    def setUp(self):
        super().setUp()
        self.sessions = {u: h / "Library" / "Application Support" / "Claude" / "local-agent-mode-sessions"
                         for u, h in self.homes.items()}
        for user, sessions in self.sessions.items():
            _write_skill(sessions / "skills-plugin" / "org" / "acct" / "skills" / f"{user}-skill", f"{user}-skill")
        self.detector = AIToolsDetector(os_name="Darwin")
        self.detector._cowork_skills_extractor = MacOSClaudeCoworkSkillsExtractor()
        # The connector merge reads the real machine; not under test here.
        self.detector._merge_desktop_surface_claudeai_connectors = lambda projects_dict: None
        for patch in (
            mock.patch.object(cowork_se, "is_running_as_root", return_value=True),
            mock.patch.object(cowork_se, "scan_user_directories",
                              side_effect=lambda fn: [fn(h) for h in self.homes.values()]),
        ):
            patch.start()
            self.addCleanup(patch.stop)

    def _process(self) -> dict:
        return self.detector.process_single_tool({"name": "Claude Cowork", "install_path": str(self.tmp)})

    def test_each_user_keeps_own_cowork_skills(self):
        self.assert_each_user_keeps_own_skills(self._process(), {u: {f"{u}-skill"} for u in USERS})

    def test_skill_file_symlink_into_another_home_is_not_read(self):
        # Bob links a SKILL.md in his own tree to a file in Alice's home.
        secret = self.homes["alice"] / "notes" / "SKILL.md"
        _write_skill(secret.parent, "alice-private")
        _symlink(self, self.sessions["bob"] / "skills-plugin" / "org" / "acct" / "skills" / "planted" / "SKILL.md",
                 secret)
        self.assert_each_user_keeps_own_skills(self._process(), {"alice": {"alice-skill"}, "bob": {"bob-skill"}})

    def test_skill_file_symlink_to_own_private_file_is_not_read(self):
        # A SKILL.md linked to the owner's own key stays inside the owner's home
        # but must still not be read as root.
        key = self.homes["bob"] / ".ssh" / "id_test"
        key.parent.mkdir(parents=True)
        key.write_text("---\nname: bob-private-key\n---\nPRIVATE\n", encoding="utf-8")
        _symlink(self, self.sessions["bob"] / "skills-plugin" / "org" / "acct" / "skills" / "planted" / "SKILL.md", key)
        self.assert_each_user_keeps_own_skills(self._process(), {"alice": {"alice-skill"}, "bob": {"bob-skill"}})

    def test_sessions_dir_symlink_into_another_home_is_not_read(self):
        # Bob replaces his whole sessions tree with a link to Alice's.
        shutil.rmtree(self.sessions["bob"])
        _symlink(self, self.sessions["bob"], self.sessions["alice"])
        self.assert_each_user_keeps_own_skills(self._process(), {"alice": {"alice-skill"}, "bob": set()})


class _PluginRootScanCase(_RootScanCase):
    """Runs the Linux multi-user plugin-dir discovery against the temp homes."""

    def setUp(self):
        super().setUp()
        self.detector = AIToolsDetector(os_name="Linux")
        for patch in (
            mock.patch.object(atd, "get_all_users_linux", return_value=list(USERS)),
            mock.patch.object(atd, "linux_home_for_user", side_effect=lambda u: self.homes[u]),
        ):
            patch.start()
            self.addCleanup(patch.stop)


class TestClaudeCodePluginSkillsRootScan(_PluginRootScanCase):
    def setUp(self):
        super().setUp()
        for user, home in self.homes.items():
            plugins_dir = home / ".claude" / "plugins"
            install = plugins_dir / "cache" / "mkt" / f"{user}-plugin" / "1.0.0"
            _write_skill(install / "skills" / f"{user}-plugin-skill", f"{user}-plugin-skill")
            (plugins_dir / "installed_plugins.json").write_text(json.dumps({
                "version": 2,
                "plugins": {f"{user}-plugin@mkt": [{"scope": "user", "installPath": str(install),
                                                     "version": "1.0.0"}]},
            }), encoding="utf-8")

    def test_each_user_keeps_own_plugin_skills(self):
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {u: {f"{u}-plugin-skill"} for u in USERS})

    def test_each_user_reports_only_own_plugins(self):
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assertEqual(_plugin_names(processed), {"alice-plugin", "bob-plugin"})
        self.assert_each_user_reports_only_own_plugins(processed, {u: {f"{u}-plugin"} for u in USERS})

    def test_root_own_plugins_reach_no_user(self):
        install = self.scanner_home / ".claude" / "plugins" / "cache" / "mkt" / "root-plugin" / "1.0.0"
        install.mkdir(parents=True)
        (self.scanner_home / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {"root-plugin@mkt": [{"scope": "user", "installPath": str(install), "version": "1.0.0"}]},
        }), encoding="utf-8")
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assertIn("root-plugin", _plugin_names(processed))
        self.assert_each_user_reports_only_own_plugins(processed, {u: {f"{u}-plugin"} for u in USERS})

    def test_windows_home_case_and_slash_differences_still_match(self):
        windows = AIToolsDetector(os_name="Windows")
        tool = {"name": "Claude Code", "projects": [],
                "plugins": [{"plugin_name": "a"}, {"plugin_name": "b"}],
                "_plugins_by_owner": [("C:\\Users\\Alice\\", [{"plugin_name": "a"}]),
                                      ("c:/users/al", [{"plugin_name": "b"}])],
                "permissions": {"settings_source": "user", "settings_path": "C:\\Users\\Alice\\.claude\\settings.json"}}
        filtered = windows.filter_tool_projects_by_user(tool, Path("c:/users/alice"))
        self.assertEqual(_plugin_names(filtered), {"a"})
        self.assertIn("permissions", filtered)
        self.assertNotIn("permissions", windows.filter_tool_projects_by_user(tool, Path("c:/users/al")))
        pathless = {**tool, "permissions": {"settings_source": "user", "settings_path": None}}
        self.assertIn("permissions", windows.filter_tool_projects_by_user(pathless, Path("c:/users/alice")))

    def test_malformed_owner_data_drops_plugins_without_crashing(self):
        alice_home = str(self.homes["alice"])
        for bad in (5, [None, 5, ("x",)], [(alice_home, None)], [(5, [{"plugin_name": "a"}])]):
            tool = {"name": "Claude Code", "projects": [], "plugins": [{"plugin_name": "a"}],
                    "_plugins_by_owner": bad}
            filtered = self.detector.filter_tool_projects_by_user(tool, self.homes["alice"])
            self.assertNotIn("plugins", filtered)
            self.assertNotIn("_plugins_by_owner", filtered)

    def test_user_without_plugins_reports_none(self):
        (self.homes["bob"] / ".claude" / "plugins" / "installed_plugins.json").unlink()
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_reports_only_own_plugins(processed, {"alice": {"alice-plugin"}, "bob": set()})

    def test_single_user_scan_keeps_own_plugins(self):
        env = {"HOME": str(self.homes["alice"]), "USERPROFILE": str(self.homes["alice"])}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(atd, "get_all_users_linux", return_value=["alice"]):
            processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assertEqual(_plugin_names(processed), {"alice-plugin"})
        report = self.detector.generate_single_tool_report(processed, "dev", "alice")["tools"][0]
        self.assertEqual(_plugin_names(report), {"alice-plugin"})
        self.assertEqual([k for k in report if k.startswith("_")], [])
        filtered = self.detector.filter_tool_projects_by_user(processed, self.homes["alice"])
        self.assertEqual(_plugin_names(filtered), {"alice-plugin"})

    def test_install_path_into_another_users_home_is_not_attributed(self):
        # installed_plugins.json is user-writable. Bob points his plugin at
        # Alice's install; a root scan must not file Alice's skill under Bob.
        alice_install = self.homes["alice"] / ".claude" / "plugins" / "cache" / "mkt" / "alice-plugin" / "1.0.0"
        (self.homes["bob"] / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {"stolen@mkt": [{"scope": "user", "installPath": str(alice_install), "version": "1.0.0"}]},
        }), encoding="utf-8")
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {"alice": {"alice-plugin-skill"}, "bob": set()})

    def test_plugin_skill_file_symlink_to_own_private_file_is_not_read(self):
        key = self.homes["bob"] / ".ssh" / "id_test"
        key.parent.mkdir(parents=True)
        key.write_text("PRIVATE\n", encoding="utf-8")
        install = self.homes["bob"] / ".claude" / "plugins" / "cache" / "mkt" / "bob-plugin" / "1.0.0"
        _symlink(self, install / "skills" / "planted" / "SKILL.md", key)
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {u: {f"{u}-plugin-skill"} for u in USERS})

    def test_local_plugin_installed_elsewhere_in_own_home_is_kept(self):
        # Directory/local plugins can record an installPath outside
        # ~/.claude/plugins; inside the owner's own home they still count.
        local = self.homes["bob"] / "dev" / "my-plugin"
        _write_skill(local / "skills" / "bob-local-skill", "bob-local-skill")
        (self.homes["bob"] / ".claude" / "plugins" / "installed_plugins.json").write_text(json.dumps({
            "version": 2,
            "plugins": {"my-plugin@local": [{"scope": "user", "installPath": str(local), "version": "0.1.0"}]},
        }), encoding="utf-8")
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(
            processed, {"alice": {"alice-plugin-skill"}, "bob": {"bob-local-skill"}})

    def test_config_dir_symlink_into_another_home_is_not_attributed(self):
        # Bob replaces his ~/.claude with a link to Alice's.
        shutil.rmtree(self.homes["bob"] / ".claude")
        _symlink(self, self.homes["bob"] / ".claude", self.homes["alice"] / ".claude")
        processed = self.detector.process_single_tool({"name": "Claude Code", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {"alice": {"alice-plugin-skill"}, "bob": set()})
        self.assert_each_user_reports_only_own_plugins(processed, {"alice": {"alice-plugin"}, "bob": set()})


class TestCursorPluginSkillsRootScan(_PluginRootScanCase):
    def setUp(self):
        super().setUp()
        for user, home in self.homes.items():
            version_dir = home / ".cursor" / "plugins" / "cache" / "mkt" / f"{user}-plugin" / "abc123"
            (version_dir / ".cursor-plugin").mkdir(parents=True)
            (version_dir / ".cursor-plugin" / "plugin.json").write_text(
                json.dumps({"name": f"{user}-plugin", "version": "1.0.0"}), encoding="utf-8")
            _write_skill(version_dir / "skills" / f"{user}-plugin-skill", f"{user}-plugin-skill")

    def test_each_user_keeps_own_plugin_skills(self):
        processed = self.detector.process_single_tool({"name": "Cursor", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {u: {f"{u}-plugin-skill"} for u in USERS})

    def test_each_user_reports_only_own_plugins(self):
        processed = self.detector.process_single_tool({"name": "Cursor", "install_path": str(self.tmp)})
        self.assertEqual(_plugin_names(processed), {"alice-plugin", "bob-plugin"})
        self.assert_each_user_reports_only_own_plugins(processed, {u: {f"{u}-plugin"} for u in USERS})

    def test_single_user_scan_keeps_own_plugins(self):
        env = {"HOME": str(self.homes["alice"]), "USERPROFILE": str(self.homes["alice"])}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(atd, "get_all_users_linux", return_value=["alice"]):
            processed = self.detector.process_single_tool({"name": "Cursor", "install_path": str(self.tmp)})
        filtered = self.detector.filter_tool_projects_by_user(processed, self.homes["alice"])
        self.assertEqual(_plugin_names(filtered), {"alice-plugin"})

    def test_plugins_dir_symlink_into_another_home_is_not_attributed(self):
        # Bob replaces his ~/.cursor/plugins with a link to Alice's.
        bob_plugins = self.homes["bob"] / ".cursor" / "plugins"
        shutil.rmtree(bob_plugins)
        _symlink(self, bob_plugins, self.homes["alice"] / ".cursor" / "plugins")
        processed = self.detector.process_single_tool({"name": "Cursor", "install_path": str(self.tmp)})
        self.assert_each_user_keeps_own_skills(processed, {"alice": {"alice-plugin-skill"}, "bob": set()})
        self.assert_each_user_reports_only_own_plugins(processed, {"alice": {"alice-plugin"}, "bob": set()})


@unittest.skipUnless(path_containment._CAN_WALK, "component-wise O_NOFOLLOW open needs POSIX dir_fd")
class TestOpenContainedRace(unittest.TestCase):
    """A symlink swapped in after the real-path check must not redirect the read.

    The check is made to see the pre-swap state (realpath returns the clean
    path), while the filesystem already holds the swapped-in symlink, which is
    the window a racing attacker aims for. open_contained must refuse.
    """

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="race-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.boundary = self.tmp / "bob" / "plugins"
        self.outside = self.tmp / "alice" / "private"
        _write_skill(self.outside / "x", "alice-private")
        stale_check = mock.patch.object(
            path_containment, "_real", side_effect=lambda p: os.path.normcase(os.path.abspath(str(p))))
        stale_check.start()
        self.addCleanup(stale_check.stop)

    def test_swapped_ancestor_dir_is_refused(self):
        _symlink(self, self.boundary / "x", self.outside / "x")
        with self.assertRaises(OSError):
            os.close(path_containment.open_contained(self.boundary / "x" / "SKILL.md", str(self.boundary)))

    def test_swapped_leaf_file_is_refused(self):
        (self.boundary / "x").mkdir(parents=True)
        _symlink(self, self.boundary / "x" / "SKILL.md", self.outside / "x" / "SKILL.md")
        with self.assertRaises(OSError):
            os.close(path_containment.open_contained(self.boundary / "x" / "SKILL.md", str(self.boundary)))

    def test_clean_file_inside_boundary_opens(self):
        _write_skill(self.boundary / "x", "bob-skill")
        fd = path_containment.open_contained(self.boundary / "x" / "SKILL.md", str(self.boundary))
        try:
            self.assertIn(b"bob-skill", os.read(fd, 4096))
        finally:
            os.close(fd)


class TestOpenContainedHandleCheck(unittest.TestCase):
    """The no-dir_fd branch (Windows) checks the opened handle's path and fails closed.

    Run on any OS by turning the component walk off, so this branch is covered
    even where the Windows CI legs cannot create symlinks.
    """

    def setUp(self):
        self.tmp = Path(os.path.realpath(tempfile.mkdtemp(prefix="handle-")))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.boundary = self.tmp / "bob"
        _write_skill(self.boundary / "x", "bob-skill")
        no_walk = mock.patch.object(path_containment, "_CAN_WALK", False)
        no_walk.start()
        self.addCleanup(no_walk.stop)

    def _open(self, path):
        return path_containment.open_contained(path, os.path.normcase(str(self.boundary)))

    def test_clean_file_opens(self):
        fd = self._open(self.boundary / "x" / "SKILL.md")
        try:
            self.assertIn(b"bob-skill", os.read(fd, 4096))
        finally:
            os.close(fd)

    def test_linked_file_is_refused(self):
        key = self.boundary / ".ssh" / "id_test"
        key.parent.mkdir()
        key.write_text("PRIVATE\n", encoding="utf-8")
        _symlink(self, self.boundary / "y" / "SKILL.md", key)
        with self.assertRaises(path_containment.NotContainedError):
            self._open(self.boundary / "y" / "SKILL.md")

    def test_file_owned_by_another_uid_is_refused(self):
        # chown needs root in tests, so make the boundary's owner differ instead.
        uid = os.stat(str(self.boundary)).st_uid
        with mock.patch.object(path_containment, "_owner_uid", return_value=uid + 1):
            with self.assertRaises(path_containment.NotContainedError):
                self._open(self.boundary / "x" / "SKILL.md")

    def test_unknown_handle_path_fails_closed(self):
        with mock.patch.object(path_containment, "_fd_real_path", return_value=None):
            with self.assertRaises(path_containment.NotContainedError):
                self._open(self.boundary / "x" / "SKILL.md")

    def test_handle_outside_boundary_is_refused(self):
        # The handle reports a path outside the boundary (a swapped-in junction).
        with mock.patch.object(path_containment, "_fd_real_path",
                               return_value=str(self.tmp / "alice" / "x" / "SKILL.md")):
            with self.assertRaises(path_containment.NotContainedError):
                self._open(self.boundary / "x" / "SKILL.md")


class TestNoScannerHomeGrouping(unittest.TestCase):
    """Static guard: the merge layer must not bind Path.home() to a variable.

    In ai_tools_discovery.py, a variable holding ``Path.home()`` (or its str) is
    how every one of these bugs keyed user data by the scanner's home. Derive the
    owner from the data instead (a skill's ``project_path``, the scanned
    ``<home>/.<tool>`` dir). ``x or str(Path.home())`` fallbacks are not flagged.

    This is a tripwire, not a proof. It does not see ``.add()``/``setdefault()``
    calls, dict-literal keys, ``os.path.expanduser("~")`` or an aliased ``Path``.
    The behaviour tests above are the real check; copy them for a new tool.
    """

    # Functions where binding Path.home() is intended. Add one only with a reason.
    ALLOWED_FUNCTIONS: set = set()

    @staticmethod
    def _is_home_call(node) -> bool:
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" \
                and len(node.args) == 1:
            node = node.args[0]
        return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "home" and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "Path")

    def test_merge_layer_never_binds_path_home(self):
        tree = ast.parse(Path(atd.__file__).read_text(encoding="utf-8"))
        offenders = []
        for func in ast.walk(tree):
            if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                    or func.name in self.ALLOWED_FUNCTIONS:
                continue
            for node in ast.walk(func):
                if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None \
                        and self._is_home_call(node.value):
                    offenders.append(f"{func.name}:{node.lineno}")
                elif isinstance(node, ast.Subscript) and self._is_home_call(node.slice):
                    offenders.append(f"{func.name}:{node.lineno}")
        self.assertEqual(offenders, [], "Path.home() used as a grouping key in ai_tools_discovery.py "
                                        "(breaks root/MDM scans): " + ", ".join(offenders))


if __name__ == "__main__":
    unittest.main()
