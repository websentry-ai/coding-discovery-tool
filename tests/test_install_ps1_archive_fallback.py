"""install.ps1 must download the repository without Git.

install.sh has had a curl/tar fallback since Feb 2026; install.ps1 was
git-only, so an MDM-deployed Windows device without Git failed the discovery
step on every run. These tests exercise the download functions of install.ps1
in a real Windows PowerShell with git removed from PATH, and only run on
Windows (the file is Windows PowerShell and the CI matrix includes
windows-latest).

They hit github.com for the branch archive, which is also what a customer
device does, so a network-less runner skips rather than fails.
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


def _functions_only(script_text: str) -> str:
    """install.ps1 ends by calling Main; keep the definitions, drop the call."""
    stripped = re.sub(r"(?m)^\s*Main\s*$", "", script_text)
    assert "function Get-Repository" in stripped
    return stripped


def _path_without_git(path: str) -> str:
    return os.pathsep.join(
        p for p in path.split(os.pathsep) if p and "git" not in p.lower()
    )


class TestHarnessHelpers(unittest.TestCase):
    """Cross-platform: the harness must find the functions it dot-sources."""

    def test_install_ps1_defines_the_download_functions(self):
        text = _functions_only(INSTALL_PS1.read_text(encoding="utf-8"))
        for name in ("Test-GitInstalled", "Get-RepositoryWithGit",
                     "Get-RepositoryWithArchive", "Get-Repository"):
            self.assertIn(f"function {name}", text)
        self.assertNotRegex(text, r"(?m)^\s*Main\s*$")

    def test_every_prerequisite_exit_reports_before_quitting(self):
        """A gate that exits without reporting is invisible to the backend."""
        text = INSTALL_PS1.read_text(encoding="utf-8")
        self.assertIn("function Send-InstallerFailure", text)
        for gate in ("Python 3 required but not found.", "Failed to download repository."):
            block = text[text.index(gate) - 300:text.index(gate)]
            self.assertIn("Send-InstallerFailure", block, f"{gate!r} exits unreported")

    def test_failure_report_uses_the_scan_lifecycle_contract(self):
        text = INSTALL_PS1.read_text(encoding="utf-8")
        for field in ("device_id", "run_id", "scan_event = 'failed'", "scan_error"):
            self.assertIn(field, text)

    def test_path_without_git_drops_only_git_entries(self):
        # Drive-letter-free entries so the check is valid on every OS's pathsep.
        path = os.pathsep.join(["Windows", "Program Files/Git/cmd", "Python312"])
        self.assertEqual(
            _path_without_git(path),
            os.pathsep.join(["Windows", "Python312"]),
        )


@unittest.skipUnless(platform.system() == "Windows", "install.ps1 is Windows PowerShell")
class TestInstallPs1ArchiveFallback(unittest.TestCase):
    def setUp(self):
        self.workdir = Path(tempfile.mkdtemp(prefix="install-ps1-test-"))
        self.harness = self.workdir / "harness.ps1"

    def tearDown(self):
        shutil.rmtree(self.workdir, ignore_errors=True)

    def _run(self, body: str, *, without_git: bool):
        script = _functions_only(INSTALL_PS1.read_text(encoding="utf-8"))
        self.harness.write_text(script + "\n\n" + body + "\n", encoding="utf-8")
        env = dict(os.environ)
        if without_git:
            env["PATH"] = _path_without_git(env.get("PATH", ""))
            env["Path"] = env["PATH"]
        return subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(self.harness)],
            capture_output=True, text=True, env=env, timeout=300,
        )

    def test_git_is_not_found_when_removed_from_path(self):
        result = self._run(
            "if (Test-GitInstalled) { Write-Output 'GIT=yes' } else { Write-Output 'GIT=no' }",
            without_git=True,
        )
        self.assertIn("GIT=no", result.stdout, result.stdout + result.stderr)

    def test_repository_downloads_as_an_archive_without_git(self):
        body = (
            "$ok = Get-Repository\n"
            "$hasScripts = Test-Path (Join-Path $TEMP_DIR 'scripts')\n"
            "$hasEntry = Test-Path (Join-Path $TEMP_DIR 'scripts\\coding_discovery_tools\\ai_tools_discovery.py')\n"
            "Write-Output \"RESULT ok=$ok scripts=$hasScripts entry=$hasEntry\"\n"
            "Remove-Item -Path $TEMP_DIR -Recurse -Force -ErrorAction SilentlyContinue\n"
        )
        result = self._run(body, without_git=True)
        output = result.stdout + result.stderr
        if "Could not download the repository" in output and "RESULT ok=False" in output:
            self.skipTest("no network access to github.com from this runner")
        self.assertIn("Git not found, downloading archive instead", output)
        self.assertIn("Repository downloaded (via archive)", output)
        self.assertIn("RESULT ok=True scripts=True entry=True", output)
        self.assertNotIn("Git is not installed", output)
