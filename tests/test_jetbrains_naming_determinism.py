"""WEB-4679 prune-key safety: the JetBrains tool *name* (which the backend uses as the
prune key, matched exactly against the scan manifest) must exclude version and license/plan.

If a future change re-embeds version/plan into the name (e.g. reverting to
f"{display_name} {version} ({plan})"), every JetBrains install row would orphan against the
manifest on the next scan and be wrongly pruned. These tests fail CI if that regression lands.
"""
import logging
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.macos.jetbrains.jetbrains import MacOSJetBrainsDetector

logging.disable(logging.CRITICAL)


class TestJetBrainsNamingDeterminism(unittest.TestCase):
    def setUp(self):
        self.det = MacOSJetBrainsDetector()

    def test_display_name_is_version_free_and_stable_across_bumps(self):
        # A patch/minor version bump renames the folder but must NOT change display_name,
        # so the prune key stays invariant across upgrades.
        for folder in ("PyCharm2025.3", "PyCharm2025.3.1", "PyCharm2026.1"):
            name, version = self.det._parse_ide_name_and_version(folder)
            self.assertEqual(name, "PyCharm", f"{folder} must map to stable 'PyCharm'")
            self.assertNotIn(version, name, "version must not leak into the display name")
        self.assertEqual(
            self.det._parse_ide_name_and_version("IntelliJIdea2025.3")[0], "IntelliJ IDEA"
        )

    def test_mapping_values_carry_no_version_or_plan(self):
        for _prefix, name in MacOSJetBrainsDetector.IDE_NAME_MAPPING.items():
            self.assertNotRegex(name, r"\d", f"{name!r} must not embed a version digit")
            self.assertNotIn("(", name, f"{name!r} must not embed a (plan) suffix")

    def test_detected_tool_name_excludes_version_and_plan(self):
        # Lock the prune-key invariant: detect() sets name = display_name ONLY, keeping
        # version and plan in separate fields (never concatenated into the name).
        fake_ide = {
            "display_name": "PyCharm",
            "version": "2025.3.1",
            "plan": "Licensed",
            "config_path": "/nonexistent/pycharm",
            "folder_name": "PyCharm2025.3.1",
        }
        with patch.object(self.det, "_scan_for_ides", return_value=[fake_ide]), \
                patch.object(self.det, "_get_plugins", return_value=[]):
            tools = self.det.detect()

        self.assertEqual(len(tools), 1)
        tool = tools[0]
        self.assertEqual(tool["name"], "PyCharm", "prune key (name) must be the bare display_name")
        self.assertNotIn("2025", tool["name"])
        self.assertNotIn("Licensed", tool["name"])
        # version + plan are preserved, just not in the name (so they can't move the key).
        self.assertEqual(tool["version"], "2025.3.1")
        self.assertEqual(tool["plan"], "Licensed")


if __name__ == "__main__":
    unittest.main()
