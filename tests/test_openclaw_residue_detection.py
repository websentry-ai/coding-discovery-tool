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
from unittest.mock import patch

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
        """Detect with all non-user-path gates neutralised and HOME redirected."""
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
             patch.object(self.detector, "_check_system_paths", return_value=None), \
             patch.object(self.mod.Path, "home", return_value=self.home):
            sh.which.return_value = None
            sp.run.side_effect = _empty_proc
            return self.detector.detect_openclaw()

    def test_bare_openclaw_dir_only_not_detected(self):
        """``~/.openclaw`` present but no ``bin/openclaw`` and no .app -> None."""
        oc = self.home / ".openclaw"
        oc.mkdir()
        (oc / "config.json").write_text("{}")  # residue config
        self.assertIsNone(self._run())

    def test_user_binary_detected(self):
        """``~/.openclaw/bin/openclaw`` -> detected."""
        binp = self.home / ".openclaw" / "bin" / "openclaw"
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_text("")
        result = self._run()
        self.assertIsNotNone(result)
        self.assertEqual(result["name"], "OpenClaw")
        self.assertEqual(result["install_path"], str(binp))

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
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
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

    def test_user_binary_detected(self):
        binp = self.home / ".openclaw" / "bin" / "openclaw"
        binp.parent.mkdir(parents=True, exist_ok=True)
        binp.write_text("")
        result = self._run()
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(binp))

    def test_which_backstop_detected(self):
        with patch.object(self.mod, "shutil") as sh, \
             patch.object(self.mod, "subprocess") as sp, \
             patch.object(self.mod, "is_running_as_root", return_value=False), \
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


if __name__ == "__main__":
    unittest.main()
