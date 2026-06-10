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


if __name__ == "__main__":
    unittest.main()
