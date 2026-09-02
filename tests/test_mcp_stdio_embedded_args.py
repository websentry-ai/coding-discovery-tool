"""_scan_stdio treats `command` as a bare executable. Configs that put the whole command line in
`command` with `args` empty never spawn — shutil.which() misses, argv[0] is a filename that does
not exist, and the scan reports command_not_found on a working install.

Driven against a stub stdio MCP server so nothing depends on npx reaching the registry. The stub
is written as a .py plus a .cmd shim on Windows (no shebang support there) so the spawn tests run
on every platform rather than being skipped on the one platform posix=False exists for.
"""

import os
import shlex
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools import mcp_tool_scanner as scanner

STUB_BODY = '''\
import json, sys
for line in sys.stdin:
    try:
        req = json.loads(line)
    except Exception:
        continue
    mid, method = req.get("id"), req.get("method")
    if method == "initialize":
        result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                  "serverInfo": {"name": "stub", "version": "9.9.9"}}
    elif method == "tools/list":
        result = {"tools": [{"name": "alpha"}, {"name": "beta"}]}
    else:
        continue
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": mid, "result": result}) + "\\n")
    sys.stdout.flush()
'''


class TestStdioEmbeddedArgs(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)

        self.bindir = root / "bin"
        self.bindir.mkdir()
        self._write_stub(self.bindir / "stubmcp")

        # A real install whose path contains a space, mirroring the prod configs
        # (".../Codex Computer Use.app/...", "/Applications/Cursor.app/...").
        spaced_dir = root / "My App"
        spaced_dir.mkdir()
        self.spaced_cmd = str(self._write_stub(spaced_dir / "run me"))

        old_path = os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": f"{self.bindir}{os.pathsep}{old_path}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _write_stub(base):
        """Write an executable stub at `base` (no extension) and return the invocable path.

        Windows cannot execute a shebang text file and shutil.which() only matches PATHEXT
        entries, so ship a .py plus a .cmd shim there. The returned path carries the extension
        on Windows: shutil.which() does not consistently apply PATHEXT to a path that already
        has a directory component across the Python versions CI runs.
        """
        if os.name == "nt":
            script = base.with_suffix(".py")
            script.write_text(STUB_BODY)
            shim = base.with_suffix(".cmd")
            shim.write_text(f'@"{sys.executable}" "{script}" %*\r\n')
            return shim
        base.write_text(f"#!{sys.executable}\n{STUB_BODY}")
        base.chmod(base.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return base

    def test_embedded_args_recovers(self):
        """The fix: whole command line in `command`, args empty -> split and scan."""
        result = scanner._scan_stdio("stubmcp --flag", [], {}, 10)
        self.assertEqual("scanned", result.get("status"))
        self.assertEqual(2, len(result.get("tools") or []))

    def test_path_with_spaces_never_reaches_the_fallback(self):
        """19 of the 21 prod configs with whitespace in `command` are real paths with correct
        args. shutil.which() resolves them on the first attempt, so the split must not run."""
        with patch.object(scanner.shlex, "split", side_effect=AssertionError("must not split")) as spy:
            result = scanner._scan_stdio(self.spaced_cmd, ["--flag"], {}, 10)
        spy.assert_not_called()
        self.assertEqual("scanned", result.get("status"))

    def test_no_op_when_there_is_nothing_to_split(self):
        """A bare command that simply is not installed keeps its pre-fix status."""
        result = scanner._scan_stdio("definitely-not-on-path", [], {}, 10)
        self.assertEqual("command_not_found", result.get("status"))

    def test_bad_first_token_does_not_mask_the_error(self):
        """Split candidate whose head is not on PATH -> still command_not_found, not a spawn
        error, and never a crash on an unbalanced quote."""
        for command in ("definitely-not-on-path --flag", 'stubmcp "unbalanced'):
            with self.subTest(command=command):
                result = scanner._scan_stdio(command, [], {}, 10)
                self.assertEqual("command_not_found", result.get("status"))

    def test_populated_args_are_never_split(self):
        """Guard on `not args`: a config that already has args is left alone."""
        with patch.object(scanner.shlex, "split", side_effect=AssertionError("must not split")):
            result = scanner._scan_stdio("stubmcp", ["--flag"], {}, 10)
        self.assertEqual("scanned", result.get("status"))

    def test_posix_false_preserves_windows_backslashes(self):
        """Why the split passes posix=(os.name != "nt"): POSIX mode treats a backslash as an
        escape and silently eats it. Pure unit test — runs on every platform, including the ones
        where the posix=False branch itself never executes."""
        command = r"C:\tools\dotnet.exe run"
        self.assertEqual([r"C:\tools\dotnet.exe", "run"], shlex.split(command, posix=False))
        self.assertEqual(["C:toolsdotnet.exe", "run"], shlex.split(command, posix=True))


if __name__ == "__main__":
    unittest.main()
