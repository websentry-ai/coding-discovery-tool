"""Residue-vs-real detection tests for Gemini CLI (``_detect_gemini_cli``).

The fix stops treating the ``~/.gemini`` config/data directory as proof of
installation. ``~/.gemini`` survives an uninstall, so gating on it produced
false positives. Detection now gates on the gemini *binary* (nvm symlink ->
gemini-cli, Homebrew, ``~/.local/bin``, ``~/.npm-global/bin``, ``~/.bun/bin``,
or the ``which gemini`` PATH backstop in ``detector.detect()``).

Each binary location is proven to be *detected* (the false-NEGATIVE guard) and
a residue-only ``~/.gemini`` home is proven to be *NOT detected* (the FP fix).
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import _detect_gemini_cli

_MOD = "scripts.coding_discovery_tools.user_tool_detector"
# The owner-attribution helper (machine_global_binary_owned_by_user) lives in
# utils and reads os.stat(...).st_uid + pwd.getpwuid(uid).pw_dir. W1 tests mock
# those on the utils module so attribution never depends on the real FS owner
# (this dev Mac actually has /opt/homebrew/bin/claude owned by a real user).
_UTILS = "scripts.coding_discovery_tools.utils"


def _stat_for_uid(target: Path, uid: int):
    """os.stat side_effect: return a fake stat (chosen ``uid``) for ``target``,
    pass through to the real os.stat for every other path."""
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            return Mock(st_uid=uid)
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _stat_raises_for(target: Path, exc: OSError = None):
    """os.stat side_effect: raise for ``target`` only, pass through otherwise.
    A real stat failure is per-file, not global — scoping it here keeps unrelated
    probes (the nvm/bun ``.exists()`` walks) working so the test exercises the
    helper's never-crash path, not an unrelated unguarded ``.exists()``."""
    real_stat = os.stat
    err = exc or OSError("boom")

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            raise err
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _pwd_home(uid_to_home: dict):
    """pwd.getpwuid side_effect mapping uid -> a pw_dir; unknown uid -> KeyError
    (mirrors the real pwd behaviour the helper guards against)."""

    def fake_getpwuid(uid):
        if uid in uid_to_home:
            return Mock(pw_dir=str(uid_to_home[uid]))
        raise KeyError(uid)

    return fake_getpwuid

# Absolute-literal candidate paths baked into ``_detect_gemini_cli``. They
# cannot be redirected to a tmp file via a constant patch, so the test that
# covers them makes exactly these paths "appear" present+executable.
_HOMEBREW = Path("/opt/homebrew/bin/gemini")
_USR_LOCAL = Path("/usr/local/bin/gemini")


def _make_detector():
    """A stand-in detector exposing only the surface ``_detect_gemini_cli``
    touches: ``tool_name``, ``get_version()``, and the ``detect()`` PATH
    fallback (defaulted to None so only opt-in cases exercise it)."""
    det = Mock()
    det.tool_name = "Gemini CLI"
    det.get_version.return_value = "1.2.3"
    det.detect.return_value = None
    return det


_ABS_LITERALS = (_HOMEBREW, _USR_LOCAL)


def _isolate_abs(present: Path = None):
    """Return (exists_patch, access_patch). The two absolute gemini literals
    report absent unless equal to ``present`` (which also reports executable);
    all other paths fall through to the real os/Path. This keeps HOME-binary
    cases hermetic on a host that actually has gemini installed under
    /opt/homebrew or /usr/local, and lets the two positive tests turn exactly
    one literal "on"."""
    real_exists = Path.exists
    real_access = os.access

    def fake_exists(self):
        if self in _ABS_LITERALS:
            return self == present
        return real_exists(self)

    def fake_access(path, mode):
        if present is not None and str(path) == str(present):
            return True
        if Path(path) in _ABS_LITERALS:
            return False
        return real_access(path, mode)

    return patch("pathlib.Path.exists", fake_exists), patch.object(os, "access", fake_access)


class TestGeminiCliResidueDetection(unittest.TestCase):
    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        # Mask both absolute gemini literals as absent by default so a real
        # install on the test host can't leak into HOME-binary cases.
        self._with_abs(None)

    def tearDown(self):
        self.tmp.cleanup()

    def _with_abs(self, present: Path):
        """(Re)install absolute-literal isolation; ``present`` (if given) is the
        single literal that looks installed for this test."""
        p_exists, p_access = _isolate_abs(present)
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)

    def _make_exec(self, path: Path) -> Path:
        """Create an executable file at ``path`` (satisfies the os.X_OK gate)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    # --- residue-only: NOT detected (the FP fix) ------------------------

    def test_residue_gemini_dir_only_not_detected(self):
        """``~/.gemini`` present but no binary anywhere -> None."""
        (self.home / ".gemini").mkdir()
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)
        # The residue dir existing must not have produced a detection.
        det.get_version.assert_not_called()

    def test_residue_gemini_dir_with_config_files_not_detected(self):
        """A populated ``~/.gemini`` (settings + oauth) is still residue."""
        gdir = self.home / ".gemini"
        gdir.mkdir()
        (gdir / "settings.json").write_text("{}")
        (gdir / "oauth_creds.json").write_text("{}")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)

    # --- real binary locations: detected (false-NEGATIVE guard) ----------

    def test_homebrew_binary_detected(self):
        """Apple-Silicon Homebrew ``/opt/homebrew/bin/gemini`` -> detected."""
        self._with_abs(_HOMEBREW)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_HOMEBREW))
        self.assertEqual(result["name"], "Gemini CLI")

    def test_usr_local_binary_detected(self):
        """Intel-Mac / manual ``/usr/local/bin/gemini`` -> detected."""
        self._with_abs(_USR_LOCAL)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_USR_LOCAL))

    def test_local_bin_binary_detected(self):
        self._make_exec(self.home / ".local" / "bin" / "gemini")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(self.home / ".local" / "bin" / "gemini"))

    def test_npm_global_binary_detected(self):
        self._make_exec(self.home / ".npm-global" / "bin" / "gemini")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"],
                         str(self.home / ".npm-global" / "bin" / "gemini"))

    def test_bun_binary_detected(self):
        # ``.bun`` is checked via ``.exists()`` only (no X_OK gate in source).
        bun = self.home / ".bun" / "bin" / "gemini"
        bun.parent.mkdir(parents=True, exist_ok=True)
        bun.write_text("")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(bun))

    def test_nvm_symlink_to_gemini_cli_detected(self):
        """npm/nvm install: a symlink whose target contains ``gemini-cli``."""
        node_dir = self.home / ".nvm" / "versions" / "node" / "v20.0.0" / "bin"
        node_dir.mkdir(parents=True, exist_ok=True)
        real_pkg = self.home / "pkgs" / "gemini-cli" / "dist" / "index.js"
        real_pkg.parent.mkdir(parents=True, exist_ok=True)
        real_pkg.write_text("")
        link = node_dir / ".gemini-lUK4BXcM"
        os.symlink(real_pkg, link)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(link))

    def test_which_backstop_detected(self):
        """No HOME binary, but ``detector.detect()`` (the ``which gemini``
        fallback) finds one -> detected. Proves the PATH backstop survives."""
        det = _make_detector()
        det.detect.return_value = {
            "name": "Gemini CLI",
            "version": "1.2.3",
            "install_path": "/custom/prefix/bin/gemini",
        }
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], "/custom/prefix/bin/gemini")

    def test_which_fallback_skipped_when_root(self):
        """Under a root/MDM multi-user scan the detector.detect() (``which
        gemini``) fallback must be SKIPPED — it resolves the SCANNER's PATH, not
        the user's, so a user with only ~/.gemini residue must NOT be reported.
        Mirrors the Claude root guard; fails against the pre-guard code."""
        det = _make_detector()
        det.detect.return_value = {
            "name": "Gemini CLI",
            "version": "1.2.3",
            "install_path": "/custom/prefix/bin/gemini",
        }
        (self.home / ".gemini").mkdir()  # residue only, no real binary
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)
        det.detect.assert_not_called()

    def test_homebrew_skipped_when_root(self):
        """Under a root/MDM multi-user scan, the MACHINE-GLOBAL Homebrew /
        /usr/local candidates must be SKIPPED — probing them per-user would
        attribute one shared Homebrew install to EVERY user. With Homebrew
        "present" but no user_home-relative binary, the result is None under
        root. Fails against the pre-guard code, which probed Homebrew regardless
        of root."""
        self._with_abs(_HOMEBREW)  # /opt/homebrew/bin/gemini "present"+exec
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)

    def test_user_home_binary_still_detected_when_root(self):
        """The root guard drops only the MACHINE-GLOBAL candidates — a
        user_home-relative binary (``~/.local/bin/gemini``) is still probed and
        detected under root (it is correctly scoped to that user)."""
        self._make_exec(self.home / ".local" / "bin" / "gemini")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"],
                         str(self.home / ".local" / "bin" / "gemini"))

    def test_residue_dir_plus_real_binary_uses_binary(self):
        """When both ``~/.gemini`` residue and a real binary exist, the
        install_path is the binary (never the residue dir)."""
        (self.home / ".gemini").mkdir()
        self._make_exec(self.home / ".local" / "bin" / "gemini")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertNotEqual(result["install_path"], str(self.home / ".gemini"))
        self.assertEqual(result["install_path"], str(self.home / ".local" / "bin" / "gemini"))

    # --- FIX #3: Windows npm path + npm-prefix root guard ----------------

    def test_windows_npm_cmd_detected(self):
        """FIX #3: on Windows, the npm shim ``%APPDATA%\\npm\\gemini.cmd`` ->
        detected (mirrors how Claude has a Windows branch). Gates on existence
        (no POSIX X_OK on Windows).

        Fails against the pre-fix code, whose user-dir/Homebrew/Bun block was
        gated entirely to non-Windows (no Windows npm probe at all)."""
        cmd = self.home / "AppData" / "Roaming" / "npm" / "gemini.cmd"
        cmd.parent.mkdir(parents=True, exist_ok=True)
        cmd.write_text("")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Windows"), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(cmd))

    def test_npm_prefix_resolution_skipped_when_root(self):
        """GUARD (FIX #3): under a root/MDM multi-user scan the npm-prefix
        resolution (``resolve_npm_global_tool_bin``) must be SKIPPED for the
        SCANNER-scoped probe — a user with only ``~/.gemini`` residue must NOT
        be reported. We assert the resolver is invoked with ``is_root=True``
        (so its internal ``npm prefix -g`` probe is gated), and ``detect()``
        (the ``which`` backstop) is never consulted. Mirrors
        ``test_which_fallback_skipped_when_root``."""
        (self.home / ".gemini").mkdir()  # residue only
        calls = []

        def spy(tool, user_home, is_root):
            calls.append((tool, is_root))
            return None

        det = _make_detector()
        det.detect.return_value = {
            "name": "Gemini CLI", "version": "1.2.3",
            "install_path": "/scanner/bin/gemini",
        }
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_MOD}.resolve_npm_global_tool_bin", side_effect=spy), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)
        self.assertEqual(calls, [("gemini", True)])
        det.detect.assert_not_called()

    def test_npm_prefix_resolution_used_when_not_root(self):
        """FIX #3: when NOT root, the npm-prefix resolution finds the real
        binary -> detected (resolver stubbed to a tmp path for hermeticity)."""
        npm_bin = self.home / "npmprefix" / "bin" / "gemini"
        npm_bin.parent.mkdir(parents=True, exist_ok=True)
        npm_bin.write_text("")
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=str(npm_bin)), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(npm_bin))

    # --- W1: machine-global owner attribution under root -----------------
    # Recovers the Homebrew-only false-NEGATIVE from 93b5fc2 WITHOUT
    # re-opening the cross-user false-positive: under root, a machine-global
    # binary is attributed to its OWNER (Homebrew/usr-local) or to every
    # scanned user when root-owned (apt/dnf), instead of being dropped.

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_owned_by_this_user_detected_when_root(self):
        """W1: root scan, /opt/homebrew/bin/gemini present and owned by a uid
        whose home == the scanned user_home -> attributed (detected). Fails
        against pre-W1 code, which dropped all machine-global candidates under
        root."""
        self._with_abs(_HOMEBREW)  # present + executable
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 501)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({501: self.home})), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_HOMEBREW))

    @unittest.skipIf(os.name == "nt", "POSIX-only: machine-global owner attribution uses pwd (absent on Windows)")
    def test_homebrew_owned_by_other_user_not_detected_when_root(self):
        """W1 (the FP guard): root scan, /opt/homebrew/bin/gemini owned by a
        DIFFERENT user's home -> that candidate is skipped; with no user-local
        binary the result is None (one user's Homebrew install is not fanned
        out to every user)."""
        self._with_abs(_HOMEBREW)
        other_home = self.home.parent / "someone_else"
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 502)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({502: other_home})), \
             patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=None), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)

    def test_root_owned_machine_global_detected_when_root(self):
        """W1: root scan, /opt/homebrew/bin/gemini owned by uid 0 (system-wide,
        e.g. apt/dnf) -> attributed to whoever is being scanned (detected). No
        pwd lookup is needed for uid 0."""
        self._with_abs(_HOMEBREW)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(_HOMEBREW, 0)), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_HOMEBREW))

    def test_machine_global_detected_when_not_root_no_owner_check(self):
        """W1: when NOT root, a present machine-global binary is detected
        directly with NO owner check (single-user case is unchanged)."""
        self._with_abs(_HOMEBREW)
        det = _make_detector()
        # os.stat must NOT be consulted in the non-root path; if it were and we
        # didn't mock it, the real (missing) path would still detect, but we
        # assert the helper is bypassed by leaving stat/pwd unmocked.
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=False), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNotNone(result)
        self.assertEqual(result["install_path"], str(_HOMEBREW))

    def test_stat_failure_never_crashes_when_root(self):
        """W1: root scan, os.stat on the machine-global candidate raises OSError
        -> the helper returns False (skip), the candidate is dropped, and no
        exception escapes. With no user-local binary the result is None."""
        self._with_abs(_HOMEBREW)
        det = _make_detector()
        with patch(f"{_MOD}.platform.system", return_value="Darwin"), \
             patch(f"{_MOD}.is_running_as_root", return_value=True), \
             patch(f"{_UTILS}.os.stat", side_effect=_stat_raises_for(_HOMEBREW)), \
             patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=None), \
             patch(f"{_MOD}.run_command", return_value=None):
            result = _detect_gemini_cli(det, self.home)
        self.assertIsNone(result)


@unittest.skipIf(os.name == "nt", "POSIX-only: machine_global_binary_owned_by_user uses pwd (absent on Windows)")
class TestMachineGlobalBinaryOwnedByUser(unittest.TestCase):
    """Focused unit tests for the shared ``machine_global_binary_owned_by_user``
    helper — the four branches: uid0 -> True, owner-match -> True,
    owner-mismatch -> False, stat-raises -> False."""

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.cand = Path("/opt/homebrew/bin/sometool")

    def tearDown(self):
        self.tmp.cleanup()

    def test_uid_zero_returns_true(self):
        """A root-owned (uid 0) machine-global binary is system-wide -> True,
        regardless of the scanned user."""
        with patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(self.cand, 0)):
            self.assertTrue(
                utils_mod.machine_global_binary_owned_by_user(self.cand, self.home)
            )

    def test_owner_home_matches_returns_true(self):
        with patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(self.cand, 501)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({501: self.home})):
            self.assertTrue(
                utils_mod.machine_global_binary_owned_by_user(self.cand, self.home)
            )

    def test_owner_home_mismatch_returns_false(self):
        other = self.home.parent / "other_user"
        with patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(self.cand, 502)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({502: other})):
            self.assertFalse(
                utils_mod.machine_global_binary_owned_by_user(self.cand, self.home)
            )

    def test_stat_raises_returns_false(self):
        with patch(f"{_UTILS}.os.stat", side_effect=OSError("nope")):
            self.assertFalse(
                utils_mod.machine_global_binary_owned_by_user(self.cand, self.home)
            )

    def test_unknown_owner_uid_returns_false(self):
        """uid resolves via stat but pwd.getpwuid raises KeyError (orphaned uid)
        -> False (do not attribute)."""
        with patch(f"{_UTILS}.os.stat", side_effect=_stat_for_uid(self.cand, 999)), \
             patch(f"{_UTILS}.pwd.getpwuid", side_effect=_pwd_home({})):
            self.assertFalse(
                utils_mod.machine_global_binary_owned_by_user(self.cand, self.home)
            )


if __name__ == "__main__":
    unittest.main()
