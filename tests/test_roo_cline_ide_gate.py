"""IDE-install gating tests for Roo Code & Cline on macOS.

Two fixes are under test:

1. The globalStorage main loop changed from ``ide_installed OR extension_path``
   to ``ide_installed AND extension_path``. A stale
   ``.../<IDE>/User/globalStorage/<ext-id>`` dir survives an IDE uninstall, so
   the OR form surfaced a phantom row for an IDE that is no longer installed.

2. The Antigravity branch is now gated on
   ``_check_ide_installation("Antigravity")``. ``~/.antigravity/extensions``
   (which holds ``extensions.json``) survives uninstall, so the extensions.json
   entry alone is not proof of install.

Both directions are proven per fix: real (.app present) -> detected; residue
(globalStorage / extensions.json only, no .app) -> not in results.

The tests build a real tmp ``/Applications`` tree and point the detector's
``APPLICATIONS_DIR`` at it, so the actual ``_check_ide_installation`` code runs
(rather than being mocked away).
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.macos.cline.cline import MacOSClineDetector
from scripts.coding_discovery_tools.macos.roo_code.roo_code import MacOSRooDetector

ROO_EXT_ID = "rooveterinaryinc.roo-cline"
CLINE_EXT_ID = "saoudrizwan.claude-dev"


class _IdeGateMixin:
    """Shared assertions for the two detectors. Mixed with ``TestCase`` by the
    concrete subclasses (so the mixin itself is never collected/run)."""

    Detector = None
    ext_id = None
    tool_label = None  # e.g. "Roo Code"
    per_user_method = None  # e.g. "_detect_roo_for_user"

    def setUp(self):
        utils_mod._SENTRY_DSN = ""
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.home = self.root / "user"
        self.home.mkdir(parents=True)
        self.apps = self.root / "Applications"
        self.apps.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    # --- fixture builders ----------------------------------------------

    def _make_globalstorage(self, ide_folder: str) -> Path:
        gs = (self.home / "Library" / "Application Support" / ide_folder
              / "User" / "globalStorage" / self.ext_id)
        gs.mkdir(parents=True, exist_ok=True)
        return gs

    def _make_app(self, app_name: str) -> Path:
        app = self.apps / app_name
        app.mkdir(parents=True, exist_ok=True)
        return app

    def _make_antigravity_extension_json(self, version: str = "3.1.0") -> Path:
        ext_dir = self.home / ".antigravity" / "extensions"
        ext_loc = ext_dir / f"{self.ext_id}-{version}"
        ext_loc.mkdir(parents=True, exist_ok=True)
        (ext_dir / "extensions.json").write_text(json.dumps([
            {
                "identifier": {"id": self.ext_id},
                "version": version,
                "relativeLocation": f"{self.ext_id}-{version}",
            }
        ]), encoding="utf-8")
        return ext_loc

    def _detect(self):
        """Drive the per-user detection with APPLICATIONS_DIR pointed at the
        tmp /Applications tree, so the real ``_check_ide_installation`` runs."""
        with patch.object(self.Detector, "APPLICATIONS_DIR", self.apps):
            det = self.Detector()
            return getattr(det, self.per_user_method)(self.home)

    def _names(self, results):
        return [r["name"] for r in results]

    # --- globalStorage OR->AND fix -------------------------------------

    def test_globalstorage_plus_app_detected(self):
        """globalStorage present AND VS Code.app present -> detected."""
        gs = self._make_globalstorage("Code")
        self._make_app("Visual Studio Code.app")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (VS Code)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (VS Code)")
        self.assertEqual(row["install_path"], str(gs))

    def test_globalstorage_without_app_not_detected(self):
        """globalStorage present but NO .app for that IDE -> [] (the OR->AND
        fix). Residue case: AppSupport/globalStorage survived the IDE uninstall."""
        self._make_globalstorage("Code")
        results = self._detect()
        self.assertEqual(
            results, [],
            "Stale globalStorage from an uninstalled IDE must not surface a row",
        )

    # --- Antigravity branch gate ---------------------------------------

    def test_antigravity_extension_without_app_not_detected(self):
        """extensions.json lists the ext but no Antigravity.app -> not in
        results (residue ~/.antigravity/extensions survived uninstall)."""
        self._make_antigravity_extension_json()
        results = self._detect()
        self.assertNotIn(
            f"{self.tool_label} (Antigravity)", self._names(results),
            "Antigravity extensions.json alone (no .app) must not surface a row",
        )

    def test_antigravity_extension_with_app_detected(self):
        """extensions.json lists the ext AND Antigravity.app present -> detected."""
        ext_loc = self._make_antigravity_extension_json()
        self._make_app("Antigravity.app")
        results = self._detect()
        self.assertIn(f"{self.tool_label} (Antigravity)", self._names(results))
        row = next(r for r in results if r["name"] == f"{self.tool_label} (Antigravity)")
        self.assertEqual(row["install_path"], str(ext_loc))

    def test_nothing_installed_returns_empty(self):
        """No globalStorage, no .app, no extensions.json -> []."""
        self.assertEqual(self._detect(), [])


class TestRooIdeGate(_IdeGateMixin, unittest.TestCase):
    Detector = MacOSRooDetector
    ext_id = ROO_EXT_ID
    tool_label = "Roo Code"
    per_user_method = "_detect_roo_for_user"


class TestClineIdeGate(_IdeGateMixin, unittest.TestCase):
    Detector = MacOSClineDetector
    ext_id = CLINE_EXT_ID
    tool_label = "Cline"
    per_user_method = "_detect_cline_for_user"


if __name__ == "__main__":
    unittest.main()
