"""install.ps1 must route -McpScan to the single-server scan entry point.

install.sh has had the `mcp-scan` sub-command since the agent hooks started
requesting on-demand scans; install.ps1 was discovery-only, so the Windows hook
shelled out to bash and died with "[WinError 2] The system cannot find the file
specified" on a stock Windows box.

The static checks run everywhere and pin the argument routing, including the
one property that is a security requirement rather than a style choice: the api
key must not reach the mcp-scan argv (Win32_Process.CommandLine / Event Log
4688 capture it). The execution checks run install.ps1 for real, with the
repository download and the Python invocation stubbed out, and only on Windows
(the file is Windows PowerShell and the CI matrix includes windows-latest).
"""

import os
import platform
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALL_PS1 = REPO_ROOT / "install.ps1"

SCAN_MODULE = "scripts.coding_discovery_tools.scan_single_mcp_server"
DISCOVERY_MODULE = "scripts.coding_discovery_tools.ai_tools_discovery"

API_KEY = "unbound-test-key-must-not-be-in-argv"
DOMAIN = "https://install-ps1-mcp-scan.invalid"


def _functions_only(script_text: str) -> str:
    """install.ps1 ends by calling Main; keep the definitions, drop the call."""
    stripped = re.sub(r"(?m)^\s*Main\s*$", "", script_text)
    assert "function Main" in stripped
    return stripped


def _python_args_assignments(script_text: str):
    return re.findall(r"(?m)^\s*\$pythonArgs = @\(.*$", script_text)


def _assignment_for(script_text: str, module: str) -> str:
    matches = [line for line in _python_args_assignments(script_text) if module in line]
    assert len(matches) == 1, f"expected one $pythonArgs line for {module}, got {matches}"
    return matches[0]


class TestArgumentRoutingIsDeclared(unittest.TestCase):
    """Cross-platform: parse the arg assembly out of install.ps1."""

    def setUp(self):
        self.text = INSTALL_PS1.read_text(encoding="utf-8")

    def test_param_block_declares_the_mcp_scan_switches(self):
        match = re.match(r"(?s)param\((.*?)\n\)\n", self.text)
        self.assertIsNotNone(match, "install.ps1 must open with a param() block")
        param_block = match.group(1)
        self.assertIn("[switch]$McpScan", param_block)
        self.assertIn("[string]$McpServerName", param_block)

    def test_mcp_scan_builds_the_scan_module_args(self):
        line = _assignment_for(self.text, SCAN_MODULE)
        self.assertIn('"--name", $McpServerName', line)
        self.assertIn('"--domain", $Domain', line)

    def test_mcp_scan_args_never_carry_the_api_key(self):
        line = _assignment_for(self.text, SCAN_MODULE)
        self.assertNotIn("--api-key", line)
        self.assertNotIn("$ApiKey", line)

    def test_mcp_scan_exports_the_api_key_to_the_child_environment(self):
        self.assertIn("$env:UNBOUND_API_KEY = $ApiKey", self.text)

    def test_discovery_args_are_unchanged(self):
        line = _assignment_for(self.text, DISCOVERY_MODULE)
        self.assertIn('"--api-key", $ApiKey', line)
        self.assertIn('"--domain", $Domain', line)

    def test_mcp_scan_without_a_server_name_is_rejected(self):
        self.assertIn(
            "if ($McpScan -and [string]::IsNullOrWhiteSpace($McpServerName))", self.text
        )

    def test_api_key_env_is_restored_after_the_scan(self):
        self.assertIn("$previousApiKey = $env:UNBOUND_API_KEY", self.text)
        after_save = self.text.split("$previousApiKey = $env:UNBOUND_API_KEY", 1)[1]
        finally_block = after_save.split("finally {", 1)[1].split("}", 1)[0]
        self.assertIn("$env:UNBOUND_API_KEY = $previousApiKey", finally_block)

    def test_python_exit_code_is_propagated_after_cleanup(self):
        self.assertIn("$pythonExitCode = $LASTEXITCODE", self.text)
        self.assertIn("if ($pythonExitCode -ne 0) { exit $pythonExitCode }", self.text)


@unittest.skipUnless(platform.system() == "Windows", "install.ps1 is Windows PowerShell")
class TestInstallPs1McpScanRouting(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="install-ps1-mcp-scan-"))
        self.harness = self.workdir / "harness.ps1"
        # Stub the download and the interpreter so the routing runs without a
        # network fetch or a real scan; `& $pythonCmd` resolves the function.
        stubs = (
            'function Get-PythonCommand { return "python3" }\n'
            "function Get-Repository { New-Item -ItemType Directory -Path $TEMP_DIR -Force | Out-Null; return $true }\n"
            'function python3 {\n'
            '  Write-Output ("ARGV " + ($args -join " "))\n'
            '  Write-Output ("ENVKEY " + $env:UNBOUND_API_KEY)\n'
            '  if ($env:TEST_PYTHON_EXIT_CODE) { $global:LASTEXITCODE = [int]$env:TEST_PYTHON_EXIT_CODE } else { $global:LASTEXITCODE = 0 }\n'
            '}\n'
            "Main\n"
            'Write-Output ("AFTERKEY " + $env:UNBOUND_API_KEY)\n'
        )
        script = _functions_only(INSTALL_PS1.read_text(encoding="utf-8"))
        self.harness.write_text(script + "\n\n" + stubs, encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _run(self, *args, api_key_in_env=True, python_exit_code=None):
        env = dict(os.environ)
        if api_key_in_env:
            env["UNBOUND_API_KEY"] = API_KEY
        else:
            env.pop("UNBOUND_API_KEY", None)
        if python_exit_code is not None:
            env["TEST_PYTHON_EXIT_CODE"] = str(python_exit_code)
        return subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-File", str(self.harness), "-Domain", DOMAIN, *args],
            capture_output=True, text=True, env=env, timeout=300,
        )

    def _argv_line(self, result):
        output = result.stdout + result.stderr
        lines = [l for l in output.splitlines() if l.startswith("ARGV ")]
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def test_mcp_scan_invokes_the_single_server_scan(self):
        result = self._run("-McpScan", "-McpServerName", "forter")
        line = self._argv_line(result)
        self.assertIn(f"-m {SCAN_MODULE}", line)
        self.assertIn("--name forter", line)
        self.assertIn(f"--domain {DOMAIN}", line)
        self.assertNotIn(DISCOVERY_MODULE, line)

    def test_mcp_scan_keeps_the_api_key_out_of_the_command_line(self):
        result = self._run("-McpScan", "-McpServerName", "forter")
        line = self._argv_line(result)
        self.assertNotIn("--api-key", line)
        self.assertNotIn(API_KEY, line)

    def test_mcp_scan_exports_parameter_api_key_to_the_scanner(self):
        result = self._run(
            "-ApiKey", API_KEY, "-McpScan", "-McpServerName", "forter",
            api_key_in_env=False,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"ENVKEY {API_KEY}", output)

    def _after_key(self, result):
        output = result.stdout + result.stderr
        lines = [l for l in output.splitlines() if l.startswith("AFTERKEY")]
        self.assertEqual(len(lines), 1, output)
        return lines[0][len("AFTERKEY"):].strip()

    def test_parameter_api_key_does_not_outlive_the_scan(self):
        result = self._run(
            "-ApiKey", API_KEY, "-McpScan", "-McpServerName", "forter",
            api_key_in_env=False,
        )
        self.assertEqual(self._after_key(result), "")

    def test_callers_api_key_is_left_as_it_was(self):
        result = self._run(
            "-ApiKey", "parameter-key", "-McpScan", "-McpServerName", "forter",
        )
        self.assertEqual(self._after_key(result), API_KEY)

    def test_mcp_scan_propagates_the_scanner_exit_code(self):
        result = self._run(
            "-McpScan", "-McpServerName", "forter", python_exit_code=23,
        )
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 23, output)

    def test_plain_invocation_still_runs_discovery(self):
        result = self._run()
        line = self._argv_line(result)
        self.assertIn(f"-m {DISCOVERY_MODULE}", line)
        self.assertIn(f"--api-key {API_KEY}", line)
        self.assertIn(f"--domain {DOMAIN}", line)
        self.assertNotIn(SCAN_MODULE, line)

    def test_mcp_scan_without_a_server_name_fails_without_scanning(self):
        result = self._run("-McpScan")
        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 1, output)
        self.assertIn("-McpServerName is required with -McpScan", output)
        self.assertNotIn("ARGV ", output)
