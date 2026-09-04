"""A config that puts the whole command line in `command` with `args` empty never spawns:
shutil.which() misses, so a working install is reported command_not_found.

Uses a stub stdio server, so no test depends on npx reaching the registry.
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
from scripts.coding_discovery_tools import mcp_extraction_helpers as mcp_helpers

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
        self.stub_command = self._write_stub(self.bindir / "stubmcp")

        # A real install whose path contains a space, as 19 prod configs do.
        spaced_dir = root / "My App"
        spaced_dir.mkdir()
        self.spaced_cmd = str(self._write_stub(spaced_dir / "run me"))

        old_path = os.environ.get("PATH", "")
        patcher = patch.dict(os.environ, {"PATH": f"{self.bindir}{os.pathsep}{old_path}"})
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _write_stub(base):
        """Executable stub at `base`; returns the path to invoke.

        Windows can't run a shebang file, so ship a .py plus a .cmd shim. Return it with the
        extension: which() doesn't reliably apply PATHEXT to an already-qualified path.
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
        """Whole command line in `command`, args empty -> split and scan."""
        result = scanner._scan_stdio("stubmcp --flag", [], {}, 10)
        self.assertEqual("scanned", result.get("status"))
        self.assertEqual(2, len(result.get("tools") or []))

    def test_path_with_spaces_never_reaches_the_fallback(self):
        """A real path with spaces resolves on the first attempt, so the split must not run."""
        with patch.object(scanner.shlex, "split", side_effect=AssertionError("must not split")) as spy:
            result = scanner._scan_stdio(self.spaced_cmd, ["--flag"], {}, 10)
        spy.assert_not_called()
        self.assertEqual("scanned", result.get("status"))

    def test_no_op_when_there_is_nothing_to_split(self):
        """A bare command that isn't installed keeps its pre-fix status."""
        result = scanner._scan_stdio("definitely-not-on-path", [], {}, 10)
        self.assertEqual("command_not_found", result.get("status"))

    def test_bad_first_token_does_not_mask_the_error(self):
        """Head not on PATH -> command_not_found, not a spawn error or a crash."""
        for command in ("definitely-not-on-path --flag", 'stubmcp "unbalanced'):
            with self.subTest(command=command):
                result = scanner._scan_stdio(command, [], {}, 10)
                self.assertEqual("command_not_found", result.get("status"))

    def test_populated_args_are_never_split(self):
        """A config that already has args is left alone."""
        with patch.object(scanner.shlex, "split", side_effect=AssertionError("must not split")):
            result = scanner._scan_stdio("stubmcp", ["--flag"], {}, 10)
        self.assertEqual("scanned", result.get("status"))

    def test_relative_command_resolves_from_configured_cwd(self):
        cwd = Path(self.tmp.name) / "provider"
        cwd.mkdir()
        command = f".{os.sep}{self._write_stub(cwd / 'local-mcp').name}"

        result = scanner.scan_mcp_server(
            {"command": command, "args": [], "env": {}, "cwd": str(cwd)}
        )

        self.assertEqual("scanned", result.get("status"))
        self.assertEqual(2, len(result.get("tools") or []))

    def test_explicit_relative_command_resolves_from_configured_cwd(self):
        cwd = Path(self.tmp.name) / "provider"
        cwd.mkdir()
        local_command = self._write_stub(cwd / "stubmcp")
        command = f".{os.sep}{local_command.name}"

        with patch.object(
            scanner.subprocess,
            "Popen",
            side_effect=FileNotFoundError,
        ) as popen:
            scanner._scan_stdio(command, ["--flag"], {}, 10, cwd=str(cwd))

        self.assertEqual(Path(popen.call_args.args[0][0]).resolve(), local_command.resolve())

    def test_bare_command_uses_path_instead_of_configured_cwd(self):
        cwd = Path(self.tmp.name) / "provider"
        cwd.mkdir()
        self._write_stub(cwd / "stubmcp")

        with patch.object(
            scanner.subprocess,
            "Popen",
            side_effect=FileNotFoundError,
        ) as popen:
            scanner._scan_stdio("stubmcp", ["--flag"], {}, 10, cwd=str(cwd))

        self.assertEqual(
            Path(popen.call_args.args[0][0]).resolve(),
            self.stub_command.resolve(),
        )

    def test_elevated_transform_does_not_scan_configured_cwd(self):
        config = {
            "local": {
                "command": str(self.stub_command),
                "args": ["relative-script.py"],
                "cwd": str(Path(self.tmp.name) / "provider"),
            }
        }

        with patch.object(
            mcp_helpers,
            "_running_with_elevated_privileges",
            return_value=True,
        ), patch.object(mcp_helpers, "_scan_servers_in_mapping") as scan_servers:
            servers = mcp_helpers.transform_mcp_servers_to_array(config)

        scan_servers.assert_not_called()
        error = servers[0]["scan"]["error"]
        self.assertEqual(error["code"], "privilege_boundary")
        self.assertEqual(
            error["details"]["reason"],
            "configured_cwd_under_elevated_process",
        )

    def test_embedded_command_retry_preserves_configured_cwd(self):
        cwd = Path(self.tmp.name) / "provider"
        cwd.mkdir()
        command = f".{os.sep}{self._write_stub(cwd / 'local-mcp').name}"

        with patch.object(scanner, "_scan_stdio", wraps=scanner._scan_stdio) as scan_stdio:
            result = scan_stdio(f"{command} --flag", [], {}, 10, cwd=str(cwd))

        self.assertEqual("scanned", result.get("status"))
        self.assertEqual(scan_stdio.call_args_list[1].kwargs["cwd"], str(cwd))

    def test_posix_false_preserves_windows_backslashes(self):
        """Why the split passes posix=False: POSIX mode eats backslashes. Runs everywhere,
        including platforms where the posix=False branch itself never executes."""
        command = r"C:\tools\dotnet.exe run"
        self.assertEqual([r"C:\tools\dotnet.exe", "run"], shlex.split(command, posix=False))
        self.assertEqual(["C:toolsdotnet.exe", "run"], shlex.split(command, posix=True))


if __name__ == "__main__":
    unittest.main()
