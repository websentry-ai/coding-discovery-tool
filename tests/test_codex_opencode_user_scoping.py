"""Detection tests for Codex and OpenCode (``_detect_npm_global_cli``).

Both previously fell through to ``detector.detect()``, which resolves the
SCANNER's PATH and home — under an MDM scan that is the SYSTEM profile, not the
scanned user, so neither tool was findable. Resolution is now ``user_home``
relative, matching ``_detect_gemini_cli``.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.user_tool_detector import _detect_codex, _detect_opencode

_MOD = "scripts.coding_discovery_tools.user_tool_detector"
_UTILS = "scripts.coding_discovery_tools.utils"

# Probed before the user_home candidates, so a real install on the test host
# would shadow the fixture. Masked as absent unless a test opts in.
_ABS = (Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"),
        Path("/opt/homebrew/bin/opencode"), Path("/usr/local/bin/opencode"))


def _isolate_abs(present: Path = None):
    real_exists, real_access = Path.exists, os.access

    def fake_exists(self):
        if self in _ABS:
            return self == present
        return real_exists(self)

    def fake_access(path, mode):
        if present is not None and str(path) == str(present):
            return True
        if Path(path) in _ABS:
            return False
        return real_access(path, mode)

    return patch("pathlib.Path.exists", fake_exists), patch.object(os, "access", fake_access)


def _stat_for_uid(target: Path, uid: int):
    """os.stat side_effect scoped to ``target``, so Path.exists() stays real."""
    real_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(target):
            return Mock(st_uid=uid)
        return real_stat(path, *args, **kwargs)

    return fake_stat


def _detector(name):
    det = Mock()
    det.tool_name = name
    det.get_version.return_value = "1.0.0"
    det.detect.return_value = {"name": name, "version": "?", "install_path": "/scanner/path"}
    return det


class _CliCase(unittest.TestCase):
    TOOL = None
    PACKAGE = None
    DETECT = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self._isolate()

    def tearDown(self):
        self.tmp.cleanup()

    def _isolate(self, present: Path = None):
        p_exists, p_access = _isolate_abs(present)
        p_exists.start()
        p_access.start()
        self.addCleanup(p_exists.stop)
        self.addCleanup(p_access.stop)

    def _make(self, rel: str) -> Path:
        path = self.home / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        os.chmod(path, 0o755)
        return path

    def _run(self, system="Darwin", is_root=False):
        det = _detector(self.TOOL)
        with patch(f"{_MOD}.platform.system", return_value=system), \
             patch(f"{_MOD}.is_running_as_root", return_value=is_root), \
             patch(f"{_MOD}.resolve_npm_global_tool_bin", return_value=None), \
             patch(f"{_MOD}.run_command", return_value=None):
            return type(self).DETECT(det, self.home), det

    def test_windows_npm_shim_detected(self):
        shim = self._make(f"AppData/Roaming/npm/{self.TOOL}.cmd")
        result, _ = self._run(system="Windows", is_root=True)
        self.assertIsNotNone(result)
        self.assertEqual(str(shim), result["install_path"])

    def test_windows_shim_version_read_from_npm_metadata(self):
        """Version must come from the resolved binary, not the scanner's PATH."""
        shim = self._make(f"AppData/Roaming/npm/{self.TOOL}.cmd")
        pkg = shim.parent / "node_modules" / self.PACKAGE / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text('{"version": "9.9.9"}')
        result, det = self._run(system="Windows", is_root=True)
        self.assertEqual("9.9.9", result["version"])
        det.get_version.assert_not_called()

    def test_windows_shim_without_metadata_is_unknown(self):
        """Better unknown than the scanner's version against a user's path."""
        self._make(f"AppData/Roaming/npm/{self.TOOL}.cmd")
        result, det = self._run(system="Windows", is_root=True)
        self.assertEqual("Unknown", result["version"])
        det.get_version.assert_not_called()

    def test_resolved_binary_is_never_executed(self):
        """The binary sits in a user-writable dir and the scan runs as root, so
        executing it would let a user run code as the scanner."""
        binary = self._make(f".local/bin/{self.TOOL}")
        with patch(f"{_MOD}.run_command") as run:
            result, det = self._run(is_root=True)
        self.assertEqual(str(binary), result["install_path"])
        self.assertEqual("Unknown", result["version"])
        for call in run.call_args_list:
            self.assertNotIn(str(binary), str(call))

    def test_posix_npm_prefix_version_read_from_metadata(self):
        """POSIX npm puts the package under <prefix>/lib/node_modules."""
        binary = self._make(f".npm-global/bin/{self.TOOL}")
        pkg = self.home / ".npm-global" / "lib" / "node_modules" / self.PACKAGE / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text('{"version": "7.7.7"}')
        result, _ = self._run(is_root=True)
        self.assertEqual(str(binary), result["install_path"])
        self.assertEqual("7.7.7", result["version"])

    def test_fifo_metadata_is_skipped_not_read(self):
        """A FIFO in the user's tree would block a root scan indefinitely."""
        shim = self._make(f"AppData/Roaming/npm/{self.TOOL}.cmd")
        pkg = shim.parent / "node_modules" / self.PACKAGE / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        os.mkfifo(pkg)
        result, _ = self._run(system="Windows", is_root=True)
        self.assertEqual("Unknown", result["version"])

    def test_oversized_metadata_is_skipped(self):
        shim = self._make(f"AppData/Roaming/npm/{self.TOOL}.cmd")
        pkg = shim.parent / "node_modules" / self.PACKAGE / "package.json"
        pkg.parent.mkdir(parents=True, exist_ok=True)
        pkg.write_text('{"version": "1.0.0", "pad": "' + "x" * (60 * 1024) + '"}')
        result, _ = self._run(system="Windows", is_root=True)
        self.assertEqual("Unknown", result["version"])

    def test_windows_nvm_windows_shim_detected(self):
        shim = self._make(f"AppData/Roaming/nvm/v20.11.0/{self.TOOL}.cmd")
        result, _ = self._run(system="Windows", is_root=True)
        self.assertEqual(str(shim), result["install_path"])

    def test_windows_volta_shim_detected(self):
        shim = self._make(f"AppData/Local/Volta/bin/{self.TOOL}.cmd")
        result, _ = self._run(system="Windows", is_root=True)
        self.assertEqual(str(shim), result["install_path"])

    def test_windows_nothing_installed_not_detected(self):
        result, det = self._run(system="Windows", is_root=True)
        self.assertIsNone(result)
        det.detect.assert_not_called()

    def test_nvm_versions_node_layout_detected(self):
        """nvm installs to $NVM_DIR/versions/node/<version>/bin."""
        binary = self._make(f".nvm/versions/node/v20.11.0/bin/{self.TOOL}")
        result, _ = self._run()
        self.assertEqual(str(binary), result["install_path"])

    def test_local_bin_detected(self):
        binary = self._make(f".local/bin/{self.TOOL}")
        self.assertEqual(str(binary), self._run()[0]["install_path"])

    def test_npm_global_bin_detected(self):
        binary = self._make(f".npm-global/bin/{self.TOOL}")
        self.assertEqual(str(binary), self._run()[0]["install_path"])

    def test_bun_bin_detected(self):
        binary = self._make(f".bun/bin/{self.TOOL}")
        self.assertEqual(str(binary), self._run()[0]["install_path"])

    def test_scanner_path_fallback_skipped_when_root(self):
        """detector.detect() resolves the scanner's PATH, so a root scan must
        not attribute the scanner's install to a user who has none."""
        result, det = self._run(is_root=True)
        self.assertIsNone(result)
        det.detect.assert_not_called()

    def test_scanner_path_fallback_used_when_not_root(self):
        result, det = self._run(is_root=False)
        self.assertEqual("/scanner/path", result["install_path"])
        det.detect.assert_called_once()

    def test_homebrew_owned_by_this_user_detected_when_root(self):
        brew = Path(f"/opt/homebrew/bin/{self.TOOL}")
        self._isolate(brew)
        with patch.object(utils_mod.os, "stat", side_effect=_stat_for_uid(brew, 501)), \
             patch.object(utils_mod, "pwd", Mock(getpwuid=lambda uid: Mock(pw_dir=str(self.home)))):
            result, _ = self._run(is_root=True)
        self.assertEqual(str(brew), result["install_path"])

    def test_homebrew_owned_by_other_user_not_detected_when_root(self):
        brew = Path(f"/opt/homebrew/bin/{self.TOOL}")
        self._isolate(brew)
        with patch.object(utils_mod.os, "stat", side_effect=_stat_for_uid(brew, 502)), \
             patch.object(utils_mod, "pwd", Mock(getpwuid=lambda uid: Mock(pw_dir="/Users/someone-else"))):
            result, _ = self._run(is_root=True)
        self.assertIsNone(result)


class TestCodexUserScoping(_CliCase):
    TOOL = "codex"
    PACKAGE = "@openai/codex"
    DETECT = staticmethod(_detect_codex)


class TestOpenCodeUserScoping(_CliCase):
    TOOL = "opencode"
    PACKAGE = "opencode-ai"
    DETECT = staticmethod(_detect_opencode)


# The shared base defines the cases; running it directly would double-count.
del _CliCase


if __name__ == "__main__":
    unittest.main()
