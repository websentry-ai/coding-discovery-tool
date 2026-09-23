"""Symlink/junction safety of the shared GitHub Copilot .github rules walk.

Under a root/MDM scan the walk must not follow a redirect out of the project.
Each OS extractor's walk is exercised only on its native OS; Windows uses an NTFS
directory junction, which ``is_symlink()`` does not detect.

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
    find_github_copilot_project_root as _mac_find_root,
)
from coding_discovery_tools.linux.github_copilot.copilot_rules_extractor import (  # noqa: E402
    LinuxGitHubCopilotRulesExtractor,
    find_github_copilot_project_root as _linux_find_root,
)
from coding_discovery_tools.windows.github_copilot.copilot_rules_extractor import (  # noqa: E402
    WindowsGitHubCopilotRulesExtractor,
    find_github_copilot_project_root as _win_find_root,
)
from coding_discovery_tools.rule_read_helpers import read_rule_file_contained  # noqa: E402

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

    FIND_ROOT = None  # each concrete class sets its OS's project-root resolver

    def test_user_scope_dotfile_symlink_within_home_is_captured(self):
        # A user-global rule symlinked within the user's own home (dotfile manager)
        # is still read.
        home = Path(tempfile.mkdtemp(prefix="gh-home-"))
        try:
            rules = home / ".claude" / "rules"
            rules.mkdir(parents=True)
            (home / ".dotfiles").mkdir()
            (home / ".dotfiles" / "g.md").write_text("MY GLOBAL RULE", encoding="utf-8")
            os.symlink(home / ".dotfiles" / "g.md", rules / "g.md")
            info = self.EXTRACTOR()._extract_rule_with_scope(
                rules / "g.md", self.FIND_ROOT, scope="user", user_home=home)
            self.assertIsNotNone(info, "a user-global dotfile symlink inside the home must be captured")
            self.assertEqual(info["content"], "MY GLOBAL RULE")
        finally:
            shutil.rmtree(home, ignore_errors=True)

    def test_user_scope_symlink_outside_home_is_refused(self):
        # A user-global symlink pointing outside the home is still refused.
        home = Path(tempfile.mkdtemp(prefix="gh-home-"))
        outside = Path(tempfile.mkdtemp(prefix="gh-evil-"))
        try:
            rules = home / ".claude" / "rules"
            rules.mkdir(parents=True)
            (outside / "secret").write_text(_SECRET, encoding="utf-8")
            os.symlink(outside / "secret", rules / "y.md")
            info = self.EXTRACTOR()._extract_rule_with_scope(
                rules / "y.md", self.FIND_ROOT, scope="user", user_home=home)
            self.assertIsNone(info, "a user-global symlink pointing outside the home must be refused")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)

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
    FIND_ROOT = staticmethod(_mac_find_root)


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux extractor runs on Linux")
class TestLinuxRulesWalkSafety(_PosixRulesWalkSafety, unittest.TestCase):
    EXTRACTOR = LinuxGitHubCopilotRulesExtractor
    FIND_ROOT = staticmethod(_linux_find_root)


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

    def test_user_scope_junction_outside_home_is_refused(self):
        # Windows uid is 0, so realpath-containment-to-home is the only user-scope
        # guard: a user-global rule reached through a junction out of the home
        # (junctions need no privilege, unlike file symlinks) must be refused.
        home = Path(tempfile.mkdtemp(prefix="gh-home-"))
        outside = Path(tempfile.mkdtemp(prefix="gh-evil-"))  # sibling of home, not under it
        try:
            (outside / "evil.md").write_text(_SECRET, encoding="utf-8")
            rules = home / ".claude" / "rules"
            rules.mkdir(parents=True)
            link = rules / "ext"
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                check=True, capture_output=True,
            )
            info = self.EXTRACTOR()._extract_rule_with_scope(
                link / "evil.md", _win_find_root, scope="user", user_home=home)
            self.assertIsNone(
                info, "a user-global junction pointing outside the home must be refused")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


@unittest.skipUnless(os.name == "posix", "per-component openat containment is POSIX; Windows runs on the VM")
class TestReadRuleFileContainedContainment(unittest.TestCase):
    """Containment is decided on the OPENED file, never a re-walked name. Project scope
    (strict) resolves per component with O_NOFOLLOW; user scope follows the dotfile
    symlink then contains the resolved descriptor to the home."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="rc-root-"))
        self.outside = Path(tempfile.mkdtemp(prefix="rc-evil-"))

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.outside, ignore_errors=True)

    # project scope (allow_symlink=False) — strict per-component O_NOFOLLOW

    def test_project_symlinked_intermediate_component_refused(self):
        # root/link -> outside{secret}; a symlinked INTERMEDIATE component must be
        # refused, not followed. (Revert _open_beneath_strict to os.open(full) and this
        # reads OUT-OF-TREE-SECRET — the deterministic prove-fail.)
        (self.outside / "rule.md").write_text("OUT-OF-TREE-SECRET", encoding="utf-8")
        os.symlink(self.outside, self.root / "link")
        result = read_rule_file_contained(self.root / "link" / "rule.md", self.root, allow_symlink=False)
        self.assertIsNone(result)

    def test_project_symlinked_final_component_refused(self):
        (self.outside / "secret.md").write_text("OUT-OF-TREE-SECRET", encoding="utf-8")
        os.symlink(self.outside / "secret.md", self.root / "rule.md")
        result = read_rule_file_contained(self.root / "rule.md", self.root, allow_symlink=False)
        self.assertIsNone(result)

    def test_project_symlinked_component_back_inside_root_refused(self):
        # Strict scope refuses ANY symlinked component, even one whose target is inside
        # root — O_NOFOLLOW semantics, not mere containment.
        (self.root / "real").mkdir()
        (self.root / "real" / "rule.md").write_text("IN-ROOT", encoding="utf-8")
        os.symlink(self.root / "real", self.root / "link")
        result = read_rule_file_contained(self.root / "link" / "rule.md", self.root, allow_symlink=False)
        self.assertIsNone(result)

    def test_project_legit_nested_file_is_read(self):
        nested = self.root / "a" / "b"
        nested.mkdir(parents=True)
        (nested / "rule.md").write_text("NESTED-OK", encoding="utf-8")
        result = read_rule_file_contained(self.root / "a" / "b" / "rule.md", self.root, allow_symlink=False)
        self.assertIsNotNone(result, "a legit nested in-root file must be read")
        self.assertEqual(result[0], "NESTED-OK")

    def test_project_path_outside_root_refused(self):
        (self.outside / "rule.md").write_text("OUT-OF-TREE-SECRET", encoding="utf-8")
        result = read_rule_file_contained(self.outside / "rule.md", self.root, allow_symlink=False)
        self.assertIsNone(result, "a path not under root (.. escape) must be refused")

    # user scope (allow_symlink=True) — follow then contain the descriptor

    def test_user_dotfile_symlink_within_home_is_read(self):
        (self.root / ".dotfiles").mkdir()
        (self.root / ".dotfiles" / "g.md").write_text("MY GLOBAL RULE", encoding="utf-8")
        rules = self.root / ".claude" / "rules"
        rules.mkdir(parents=True)
        os.symlink(self.root / ".dotfiles" / "g.md", rules / "g.md")
        result = read_rule_file_contained(rules / "g.md", self.root, allow_symlink=True)
        self.assertIsNotNone(result, "a user-global dotfile symlink inside home must read")
        self.assertEqual(result[0], "MY GLOBAL RULE")

    def test_user_symlink_outside_home_refused_on_descriptor(self):
        # Binding lock: there is no name pre-check any more — the open lands on a real
        # out-of-home secret and _fd_within_root on the opened fd is the sole guard.
        (self.outside / "secret").write_text("OUT-OF-TREE-SECRET", encoding="utf-8")
        rules = self.root / ".claude" / "rules"
        rules.mkdir(parents=True)
        os.symlink(self.outside / "secret", rules / "y.md")
        result = read_rule_file_contained(rules / "y.md", self.root, allow_symlink=True)
        self.assertIsNone(result, "a user-global symlink outside home must be refused")


@unittest.skipUnless(sys.platform == "darwin", "Copilot-for-Xcode reader is macOS-only")
class TestXcodeSafeReadBytesContainment(unittest.TestCase):
    """The Xcode plist/JSON reader shares the same per-component containment."""

    def test_symlinked_component_refused_and_legit_read(self):
        from coding_discovery_tools.macos.github_copilot_xcode.settings_extractor import (
            MacOSCopilotXcodeSettingsExtractor,
        )
        ext = MacOSCopilotXcodeSettingsExtractor()
        home = Path(tempfile.mkdtemp(prefix="xc-home-"))
        outside = Path(tempfile.mkdtemp(prefix="xc-evil-"))
        try:
            cfg = home / ".config" / "github-copilot" / "xcode"
            cfg.mkdir(parents=True)
            (cfg / "mcp.json").write_text("{}", encoding="utf-8")
            self.assertEqual(ext._safe_read_bytes(cfg / "mcp.json", home), b"{}",
                             "a legit in-home config must be read")
            (outside / "secret.json").write_text("OUT-OF-TREE-SECRET", encoding="utf-8")
            os.symlink(outside / "secret.json", cfg / "evil.json")
            self.assertIsNone(ext._safe_read_bytes(cfg / "evil.json", home),
                              "a symlinked component pointing outside home must be refused")
        finally:
            shutil.rmtree(home, ignore_errors=True)
            shutil.rmtree(outside, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
