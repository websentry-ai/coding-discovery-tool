"""Symlink/junction safety of the shared GitHub Copilot .github rules walk (WEB-5935).

Under a root/MDM scan the walk must not follow a redirect out of the project: a
user who controls their own repo could point ``.github/copilot-instructions.md``
(or the ``.github`` directory) at another user's file. Each OS extractor's walk is
exercised only on its native OS (its home/system-path logic assumes that host); the
CI matrix covers all three. Windows is tested with an NTFS directory *junction*,
which ``is_symlink()`` does not detect.

unittest, not pytest: CI runs ``python -m unittest discover -s tests -t .``.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from coding_discovery_tools.macos.github_copilot.copilot_rules_extractor import (  # noqa: E402
    MacOSGitHubCopilotRulesExtractor,
)
from coding_discovery_tools.linux.github_copilot.copilot_rules_extractor import (  # noqa: E402
    LinuxGitHubCopilotRulesExtractor,
)
from coding_discovery_tools.windows.github_copilot.copilot_rules_extractor import (  # noqa: E402
    WindowsGitHubCopilotRulesExtractor,
)

_SECRET = "SECRET-OUTSIDE-THE-REPO"


class _RulesWalkSafetyBase:
    """Fixture: a repo with a real .github/copilot-instructions.md, plus an
    out-of-repo directory used as the redirect target in the attack cases."""

    EXTRACTOR = None

    def setUp(self):
        # Under the real home so the extractor's system-path skip does not drop it.
        self.root = Path(tempfile.mkdtemp(prefix="gh-sym-", dir=str(Path.home())))
        self.repo = self.root / "myrepo"
        (self.repo / ".github").mkdir(parents=True)
        (self.repo / ".github" / "copilot-instructions.md").write_text(
            "# legit in-repo rule", encoding="utf-8"
        )
        self.outside = Path(tempfile.mkdtemp(prefix="gh-sym-evil-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    def _walk_blob(self):
        projects_by_root = {}
        self.EXTRACTOR()._walk_for_github_directories(self.root, self.root, projects_by_root, 0)
        return json.dumps(projects_by_root)

    def test_legit_regular_instruction_is_captured(self):
        self.assertIn("copilot-instructions.md", self._walk_blob())


class _PosixRulesWalkSafety(_RulesWalkSafetyBase):
    """POSIX (macOS/Linux) symlink attack cases."""

    def test_symlinked_instruction_file_outside_repo_is_refused(self):
        secret = self.outside / "id_rsa"
        secret.write_text(_SECRET, encoding="utf-8")
        ci = self.repo / ".github" / "copilot-instructions.md"
        ci.unlink()
        os.symlink(secret, ci)
        self.assertNotIn(_SECRET, self._walk_blob(),
                         "a symlinked rule file pointing outside the repo must not be read")

    def test_symlinked_github_dir_outside_repo_is_refused(self):
        (self.outside / "copilot-instructions.md").write_text(_SECRET, encoding="utf-8")
        shutil.rmtree(self.repo / ".github")
        os.symlink(self.outside, self.repo / ".github")
        self.assertNotIn(_SECRET, self._walk_blob(),
                         "a symlinked .github directory must not be entered")


@unittest.skipUnless(sys.platform == "darwin", "macOS extractor runs on macOS")
class TestMacOSRulesWalkSafety(_PosixRulesWalkSafety, unittest.TestCase):
    EXTRACTOR = MacOSGitHubCopilotRulesExtractor


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux extractor runs on Linux")
class TestLinuxRulesWalkSafety(_PosixRulesWalkSafety, unittest.TestCase):
    EXTRACTOR = LinuxGitHubCopilotRulesExtractor


@unittest.skipUnless(sys.platform == "win32", "Windows extractor runs on Windows")
class TestWindowsRulesWalkSafety(_RulesWalkSafetyBase, unittest.TestCase):
    EXTRACTOR = WindowsGitHubCopilotRulesExtractor

    def test_junctioned_github_dir_outside_repo_is_refused(self):
        # An NTFS directory junction (no admin required) — is_symlink() misses it,
        # so the walk must reject it via is_symlink_or_junction.
        (self.outside / "copilot-instructions.md").write_text(_SECRET, encoding="utf-8")
        shutil.rmtree(self.repo / ".github")
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(self.repo / ".github"), str(self.outside)],
            check=True, capture_output=True,
        )
        self.assertNotIn(_SECRET, self._walk_blob(),
                         "a junctioned .github directory must not be entered")


if __name__ == "__main__":
    unittest.main()
