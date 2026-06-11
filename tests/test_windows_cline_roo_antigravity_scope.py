"""User-scoping tests for the Windows Cline / Roo "(Antigravity)" gate.

Cline and Roo emit an ``(Antigravity)`` row only when Antigravity is installed.
They previously probed ``WindowsAntigravityDetector()._find_app_path()``, which
under an admin/SYSTEM scan enumerates EVERY user's ``Programs`` dir — so user B
(holding only Cline/Roo-in-Antigravity extension residue) was wrongly credited
with Antigravity because user A had it installed, a cross-user false positive.

The fix scopes the probe to the user being scanned via
``_is_antigravity_installed(user_home)`` ->
``WindowsAntigravityDetector().is_installed_for_user(user_home)``. These tests
prove both directions for each detector.

All cases rely on ``C:\\Program Files\\Antigravity`` being absent on the test
host (true on macOS; CI Windows runners have no Antigravity), so only the tmp
``user_home`` decides the outcome.
"""

import tempfile
import unittest
from pathlib import Path

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.windows.antigravity.antigravity import (
    WindowsAntigravityDetector,
)
from scripts.coding_discovery_tools.windows.cline.cline import WindowsClineDetector
from scripts.coding_discovery_tools.windows.roo_code.roo_code import WindowsRooDetector


def _install_antigravity(user_home: Path) -> None:
    """Create a real per-user Antigravity install (the ``Antigravity.exe``
    artifact, removed on uninstall) under ``user_home``'s Programs dir."""
    name = WindowsAntigravityDetector._PROGRAM_DIR_NAMES[0]
    install = user_home / "AppData" / "Local" / "Programs" / name
    install.mkdir(parents=True, exist_ok=True)
    (install / "Antigravity.exe").write_text("")


class _AntigravityGateMixin:
    """Shared cases; concrete subclasses set ``detector_cls``."""

    detector_cls = None

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.detector = self.detector_cls()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_installed_for_own_user_true(self):
        """The scanned user has their own Antigravity install -> gate True."""
        user_home = self.root / "user_b"
        user_home.mkdir(parents=True, exist_ok=True)
        _install_antigravity(user_home)
        self.assertTrue(self.detector._is_antigravity_installed(user_home))

    def test_other_users_install_not_attributed_false(self):
        """CROSS-USER FP FIX: user A has Antigravity, user B does not. The gate
        for user B must be False — user A's per-user install (reachable via the
        old ``_find_app_path`` all-users admin enumeration) must NOT be
        attributed to user B, who holds only extension residue."""
        user_a_home = self.root / "user_a"
        user_a_home.mkdir(parents=True, exist_ok=True)
        _install_antigravity(user_a_home)

        user_b_home = self.root / "user_b"
        extensions = user_b_home / ".antigravity" / "extensions"
        extensions.mkdir(parents=True, exist_ok=True)
        (extensions / "extensions.json").write_text("[]")

        self.assertFalse(self.detector._is_antigravity_installed(user_b_home))


class TestClineAntigravityGate(_AntigravityGateMixin, unittest.TestCase):
    detector_cls = WindowsClineDetector


class TestRooAntigravityGate(_AntigravityGateMixin, unittest.TestCase):
    detector_cls = WindowsRooDetector


if __name__ == "__main__":
    unittest.main()
