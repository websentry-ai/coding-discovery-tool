"""Residue-vs-real detection tests for OpenClaw (macOS, Windows, Linux).

The fix removes the bare ``~/.openclaw`` (``%USERPROFILE%\\.openclaw``) directory
entry from the per-user candidate list — it is a residue config/data dir that
survives uninstall. The kept gates are unchanged: the real binary
(``~/.openclaw/bin/openclaw``), the ``.app`` bundle (macOS), Programs dirs
(Windows), the PATH lookup (``shutil.which``), and the running-process probe.

Every test neutralises the process/service/which probes and the hardcoded
system paths, so only the per-user tree under a fake HOME is authoritative.
Both directions per OS: bare ``~/.openclaw`` only -> None; real binary/.app /
``which`` -> detected.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod


def _empty_proc(*args, **kwargs):
    """A subprocess.run stand-in whose stdout has no 'openclaw' substring."""
    return SimpleNamespace(stdout="", stderr="", returncode=1)


class TestMacOSOpenClawResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.macos.openclaw import detect_openclaw as mod
        self.mod = mod
        self.detector = mod.MacOSOpenClawDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        """Detect with all non-user-path gates neutralised and HOME redirected.
        The npm-prefix resolver is stubbed to None by default so a real
        ``openclaw`` on the dev Mac (e.g. ``/opt/homebrew/bin/openclaw``) cannot
        leak in — the npm-prefix cases opt in explicitly."""
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=None), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            return self.detector.detect_openclaw()

    def test_bare_openclaw_dir_only_not_detected(self):
        """``~/.openclaw`` present but no .app and no npm binary -> None."""
        oc = self.home / ".openclaw"
        oc.mkdir()
        (oc / "config.json").write_text("{}")  # residue config
        self.assertIsNone(self._run())

    def test_removed_openclaw_bin_candidate_not_detected(self):
        """FIX #3: the undocumented ``~/.openclaw/bin/openclaw`` candidate was
        REMOVED (npm installs to the global prefix, not there) — so even when
        that file exists on disk it must NOT produce a detection. The npm-prefix
        resolver is stubbed None so only the removed candidate could match.

        Fails against the pre-fix code, which listed ``~/.openclaw/bin/openclaw``
        and would detect it."""
        binp = self.home / ".openclaw" / "bin" / "openclaw"
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_text("")
        self.assertIsNone(self._run())

    def test_npm_prefix_binary_detected_when_not_root(self):
        """FIX #3: when NOT root, the npm-global-prefix resolution finds the
        real binary -> detected. The resolver is stubbed to a tmp path so the
        test is hermetic."""
        npm_bin = self.home / "npmprefix" / "bin" / "openclaw"
        npm_bin.parent.mkdir(parents=True, exist_ok=True)
        npm_bin.write_text("")
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=str(npm_bin)), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(npm_bin))

    def test_npm_prefix_probe_skipped_when_root(self):
        """GUARD (FIX #3): under root the ``npm prefix -g`` probe must NOT run —
        it resolves the SCANNER's prefix, not the user's (the 93b5fc2 cross-user
        FP class). We assert the helper is called with ``is_root=True`` so the
        guard inside it skips the dynamic probe; with no .app and no static
        fallback the user-dir check yields None.

        The shared helper's own root guard is unit-tested separately; here we
        confirm the detector forwards the real root state through."""
        from scripts.coding_discovery_tools import utils as utils_real
        calls = []

        def spy(tool, user_home, is_root):
            calls.append((tool, is_root))
            return None

        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=True), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", side_effect=spy), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod, "scan_user_directories",
                          side_effect=lambda cb: cb(self.home)):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNone(result)
        self.assertEqual(calls, [("openclaw", True)])

    def test_user_app_bundle_detected(self):
        """``~/Applications/OpenClaw.app`` -> detected."""
        app = self.home / "Applications" / "OpenClaw.app"
        app.mkdir(parents=True, exist_ok=True)
        result = self._run()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(app))

    def test_which_backstop_detected(self):
        """``shutil.which('openclaw')`` resolving -> detected (PATH gate kept)."""
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=None), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = "/usr/local/bin/openclaw"
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/usr/local/bin/openclaw")


class TestLinuxOpenClawResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.linux.openclaw import detect_openclaw as mod
        self.mod = mod
        self.detector = mod.LinuxOpenClawDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        """Detect with non-user-path gates neutralised; the npm-prefix resolver
        is stubbed to None so a real ``openclaw`` on the dev host can't leak."""
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=None), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            return self.detector.detect_openclaw()

    def test_bare_openclaw_dir_only_not_detected(self):
        oc = self.home / ".openclaw"
        oc.mkdir()
        (oc / "config.json").write_text("{}")
        self.assertIsNone(self._run())

    def test_removed_openclaw_bin_candidate_not_detected(self):
        """FIX #3: the undocumented ``~/.openclaw/bin/openclaw`` candidate was
        REMOVED — even when present on disk it must NOT be detected (the
        npm-prefix resolver is stubbed None). Fails against the pre-fix code."""
        binp = self.home / ".openclaw" / "bin" / "openclaw"
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_text("")
        self.assertIsNone(self._run())

    def test_npm_prefix_binary_detected_when_not_root(self):
        """FIX #3: when NOT root, the npm-global-prefix resolution finds the
        real binary -> detected (resolver stubbed to a tmp path for hermeticity)."""
        npm_bin = self.home / "npmprefix" / "bin" / "openclaw"
        npm_bin.parent.mkdir(parents=True, exist_ok=True)
        npm_bin.write_text("")
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=str(npm_bin)), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(npm_bin))

    def test_npm_prefix_probe_skipped_when_root(self):
        """GUARD (FIX #3): under root the resolver is invoked with
        ``is_root=True`` (so its internal ``npm prefix -g`` probe is skipped);
        with no static fallback the result is None."""
        calls = []

        def spy(tool, user_home, is_root):
            calls.append((tool, is_root))
            return None

        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=True), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", side_effect=spy), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod, "scan_user_directories",
                          side_effect=lambda cb: cb(self.home)):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNone(result)
        self.assertEqual(calls, [("openclaw", True)])

    def test_which_backstop_detected(self):
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.mod, "resolve_npm_global_tool_bin", return_value=None), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = "/usr/bin/openclaw"
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/usr/bin/openclaw")


class TestWindowsOpenClawResidue(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        from scripts.coding_discovery_tools.windows.openclaw import detect_openclaw as mod
        self.mod = mod
        self.detector = mod.WindowsOpenClawDetector()
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self):
        """Neutralise which / process / deep-exe-search; drive the static-path
        check off ``_get_installation_paths`` (which we control via env)."""
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.detector, "_search_for_executable", return_value=None), \
             patch.object(self.detector, "_get_installation_paths",
                          return_value=self._candidate_paths()):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            return self.detector.detect_openclaw()

    def _candidate_paths(self):
        # Mirrors the real per-user/system candidate set MINUS the bare
        # ``~/.openclaw`` entry (which the fix removed).
        return [
            self.home / "AppData" / "Local" / "Programs" / "OpenClaw",
            self.home / "AppData" / "Roaming" / "OpenClaw",
        ]

    def test_bare_openclaw_dir_only_not_detected(self):
        """``%USERPROFILE%\\.openclaw`` present -> None: it is NOT among the
        candidate paths (the fix removed it). We create it to prove that even
        when it exists on disk, detection ignores it."""
        oc = self.home / ".openclaw"
        oc.mkdir()
        (oc / "config.json").write_text("{}")
        self.assertIsNone(self._run())

    def test_programs_install_detected(self):
        """A real ``Programs\\OpenClaw`` install dir -> detected."""
        install = self.home / "AppData" / "Local" / "Programs" / "OpenClaw"
        install.mkdir(parents=True, exist_ok=True)
        (install / "openclaw.exe").write_text("")
        result = self._run()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))

    def test_which_backstop_detected(self):
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.detector, "_search_for_executable", return_value=None), \
             patch.object(self.detector, "_get_installation_paths", return_value=self._candidate_paths()):
            sh.which.return_value = r"C:\Program Files\OpenClaw\openclaw.exe"
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], r"C:\Program Files\OpenClaw\openclaw.exe")

    def test_roaming_userdata_residue_not_in_candidates(self):
        """REGRESSION (WARNING #1): a bare ``%APPDATA%\\Roaming\\OpenClaw``
        userData dir with NO exe inside must NOT be detected. This drives the
        REAL ``_get_installation_paths`` (env-driven, not patched) so it would
        fail against the old code that listed the userData dirs via bare
        ``path.exists()``. The deep exe-search and which/process gates are
        neutralised so the static-path list is authoritative.

        Also asserts the residue dirs are absent from the candidate list and a
        real ``Programs\\OpenClaw`` install IS present (gate kept)."""
        local = self.home / "AppData" / "Local"
        roaming = self.home / "AppData" / "Roaming"
        # Residue userData dirs that survive uninstall — no exe inside.
        (local / "OpenClaw").mkdir(parents=True, exist_ok=True)
        (roaming / "OpenClaw").mkdir(parents=True, exist_ok=True)

        env = {"LOCALAPPDATA": str(local), "APPDATA": str(roaming)}
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.detector, "_search_for_executable", return_value=None), \
             patch.dict(self.mod.os.environ, env, clear=True):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            # The real _get_installation_paths must not include the userData
            # residue dirs (only Programs/system dirs).
            candidates = self.detector._get_installation_paths()
            self.assertNotIn(local / "OpenClaw", candidates)
            self.assertNotIn(roaming / "OpenClaw", candidates)
            self.assertIn(local / "Programs" / "OpenClaw", candidates)
            # End-to-end: residue-only userData -> not installed.
            self.assertIsNone(self.detector.detect_openclaw())

    def test_programs_install_via_real_candidate_list(self):
        """A real ``%LOCALAPPDATA%\\Programs\\OpenClaw\\openclaw.exe`` IS
        detected through the REAL env-driven candidate list (Programs gate
        kept)."""
        local = self.home / "AppData" / "Local"
        install = local / "Programs" / "OpenClaw"
        install.mkdir(parents=True, exist_ok=True)
        (install / "openclaw.exe").write_text("")

        env = {"LOCALAPPDATA": str(local), "APPDATA": str(self.home / "AppData" / "Roaming")}
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_admin", return_value=False), \
             patch.object(self.detector, "_search_for_executable", return_value=None), \
             patch.dict(self.mod.os.environ, env, clear=True):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            result = self.detector.detect_openclaw()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(install))


class TestResolveNpmGlobalToolBin(unittest.TestCase):
    """Unit tests for the shared ``resolve_npm_global_tool_bin`` helper (used by
    OpenClaw + Gemini). GUARD: the dynamic ``npm prefix -g`` probe and the
    machine-global ``/opt/homebrew/bin`` fallback resolve the SCANNER's config,
    so they must be SKIPPED under root (the 93b5fc2 cross-user FP class). The
    ``user_home``-relative fallbacks are correctly scoped and stay active."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        import scripts.coding_discovery_tools.utils as utils
        self.utils = utils
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name) / "user"
        self.home.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _make_exec(self, path: Path) -> Path:
        import os
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def test_npm_prefix_resolved_when_not_root(self):
        """Not root: ``npm prefix -g`` is consulted and ``<prefix>/bin/<tool>``
        resolved -> returned."""
        prefix = Path(self.tmp.name) / "prefix"
        tool_bin = self._make_exec(prefix / "bin" / "openclaw")
        with patch.object(self.utils, "run_command", return_value=str(prefix)) as rc:
            result = self.utils.resolve_npm_global_tool_bin("openclaw", self.home, is_root=False)
        self.assertEqual(result, str(tool_bin))
        rc.assert_called_once()
        self.assertEqual(rc.call_args.args[0], ["npm", "prefix", "-g"])

    def test_npm_prefix_probe_skipped_when_root(self):
        """GUARD: under root, ``run_command`` (``npm prefix -g``) is NEVER
        called even though it would resolve to a real binary — mirrors
        ``test_which_fallback_skipped_when_root``. With no user_home-relative
        binary the result is None."""
        prefix = Path(self.tmp.name) / "prefix"
        self._make_exec(prefix / "bin" / "openclaw")  # a real binary it WOULD find
        run_mock = Mock(return_value=str(prefix))
        with patch.object(self.utils, "run_command", run_mock):
            result = self.utils.resolve_npm_global_tool_bin("openclaw", self.home, is_root=True)
        self.assertIsNone(result)
        run_mock.assert_not_called()

    def test_user_home_fallback_resolved_when_root(self):
        """Under root, a ``user_home``-relative install (``~/.npm-global/bin``)
        is still resolved — only the SCANNER-scoped probes are gated."""
        tool_bin = self._make_exec(self.home / ".npm-global" / "bin" / "openclaw")
        with patch.object(self.utils, "run_command", return_value=None):
            result = self.utils.resolve_npm_global_tool_bin("openclaw", self.home, is_root=True)
        self.assertEqual(result, str(tool_bin))

    def test_nvm_fallback_resolved(self):
        """A ``user_home``-relative nvm install is resolved. Uses ``is_root=True``
        so the machine-global ``/opt/homebrew/bin`` fallback (which may really
        exist on the dev Mac) is gated out and cannot shadow the tmp nvm path —
        keeping the test hermetic while still proving nvm resolution (which is
        user-relative and unaffected by the root guard)."""
        tool_bin = self._make_exec(
            self.home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin" / "openclaw")
        with patch.object(self.utils, "run_command", return_value=None):
            result = self.utils.resolve_npm_global_tool_bin("openclaw", self.home, is_root=True)
        self.assertEqual(result, str(tool_bin))


if __name__ == "__main__":
    unittest.main()
