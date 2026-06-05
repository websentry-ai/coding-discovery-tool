"""Tests for Approach B coding-discovery-tool changes.

Covers:
  - ``is_container`` top-level field in the report payload built by
    ``generate_single_tool_report``.
  - The persisted-UUID device_id fallback in ``LinuxDeviceIdExtractor`` when
    /etc/machine-id is empty/absent (read existing, else generate+write).
  - The ``in_container()`` helper salvaged into utils.py.

Per CLAUDE.md: tests mock filesystem reads/writes; ``in_container`` is
lru_cached so we clear its cache around container-state toggles.
"""

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
import scripts.coding_discovery_tools.ai_tools_discovery as ai_tools_discovery
import scripts.coding_discovery_tools.utils as utils_mod
from scripts.coding_discovery_tools.utils import in_container


class TestIsContainerInReport(unittest.TestCase):
    """``is_container`` is always present as a top-level report field."""

    def _make_report(self):
        detector = AIToolsDetector()
        tool = {"name": "dummy-tool", "version": "1.0", "_internal": "hidden"}
        return detector.generate_single_tool_report(
            tool, device_id="DEV-123", home_user="testuser"
        )

    def test_is_container_true(self):
        with patch.object(ai_tools_discovery, "in_container", return_value=True):
            report = self._make_report()
        self.assertIn("is_container", report)
        self.assertIs(report["is_container"], True)

    def test_is_container_false(self):
        with patch.object(ai_tools_discovery, "in_container", return_value=False):
            report = self._make_report()
        self.assertIn("is_container", report)
        self.assertIs(report["is_container"], False)

    def test_is_container_is_top_level_not_in_tool(self):
        """The flag lives in the report dict, not inside the tool payload."""
        with patch.object(ai_tools_discovery, "in_container", return_value=True):
            report = self._make_report()
        self.assertIn("is_container", report)
        self.assertEqual(len(report["tools"]), 1)
        self.assertNotIn("is_container", report["tools"][0])
        # Internal keys (leading underscore) are still filtered out.
        self.assertNotIn("_internal", report["tools"][0])

    def test_home_user_not_namespaced(self):
        """Approach B: plain home_user, no PR #165 container namespacing."""
        with patch.object(ai_tools_discovery, "in_container", return_value=True):
            report = self._make_report()
        self.assertEqual(report["home_user"], "testuser")


class TestInContainerHelper(unittest.TestCase):
    """The salvaged in_container() helper. lru_cached -> clear around toggles."""

    def setUp(self):
        in_container.cache_clear()
        self.addCleanup(in_container.cache_clear)

    def test_detects_dockerenv(self):
        def fake_exists(p):
            return p == "/.dockerenv"

        with patch.object(utils_mod.os.path, "exists", side_effect=fake_exists):
            self.assertTrue(in_container())

    def test_no_markers_returns_false(self):
        with patch.object(utils_mod.os.path, "exists", return_value=False), \
             patch("builtins.open", side_effect=OSError("no proc")):
            self.assertFalse(in_container())

    def test_lru_cache_clear_resets_state(self):
        # Without cache_clear() the first result would stick and lie.
        with patch.object(utils_mod.os.path, "exists", return_value=False), \
             patch("builtins.open", side_effect=OSError):
            self.assertFalse(in_container())
        in_container.cache_clear()
        with patch.object(utils_mod.os.path, "exists",
                          side_effect=lambda p: p == "/run/.containerenv"):
            self.assertTrue(in_container())


class TestLinuxDeviceIdFallback(unittest.TestCase):
    """Persisted-UUID device_id fallback when machine-id is empty/absent.

    These tests use a REAL temp dir (patched onto ``cache.UNBOUND_DIR``) so that
    the atomic ``tempfile.mkstemp`` + ``os.replace`` write path is exercised for
    real rather than mocked away.
    """

    def setUp(self):
        from scripts.coding_discovery_tools.linux.device_id import LinuxDeviceIdExtractor
        self.extractor = LinuxDeviceIdExtractor()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / ".unbound"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.device_id_path = self.state_dir / "device-id"

    def test_machine_id_takes_precedence(self):
        """Existing behavior preserved: machine-id wins, no UUID fallback."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        class FakeMachineIdPath:
            def exists(self):
                return True

            def is_file(self):
                return True

            def read_text(self, encoding="utf-8"):
                return "  abc123machineid  \n"

        with patch.object(did_mod, "_MACHINE_ID_PATHS", [FakeMachineIdPath()]):
            self.assertEqual(self.extractor.extract_device_id(), "abc123machineid")

    def test_fallback_generates_and_persists_uuid(self):
        """First call with empty machine-id -> generate uuid4 and write it.

        Reads the file back off the real fs to prove the atomic write landed.
        """
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        fixed = "11111111-2222-3333-4444-555555555555"

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR", self.state_dir), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()

        self.assertEqual(result, fixed)
        self.assertTrue(self.device_id_path.exists())
        self.assertEqual(self.device_id_path.read_text(encoding="utf-8"), fixed)
        # No leftover temp files from the atomic write.
        leftovers = [p for p in self.state_dir.iterdir() if p.name != "device-id"]
        self.assertEqual(leftovers, [])

    def test_fallback_reads_existing_uuid(self):
        """Second/restarted run -> read the persisted uuid, do NOT regenerate."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        persisted = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        self.device_id_path.write_text(f"  {persisted}\n", encoding="utf-8")

        # uuid4 would raise if called — proving we read instead of generate.
        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR", self.state_dir), \
             patch.object(did_mod.uuid, "uuid4",
                          side_effect=AssertionError("uuid4 should not be called")):
            result = self.extractor.extract_device_id()

        self.assertEqual(result, persisted)
        # File untouched (still has the original whitespace-padded content).
        self.assertEqual(self.device_id_path.read_text(encoding="utf-8"),
                         f"  {persisted}\n")

    def test_fallback_corrupt_partial_uuid_regenerates(self):
        """A truncated/partial (non-UUID) persisted value is treated as absent.

        Simulates a pre-atomic-write partial write or a manual edit. The extractor
        must reject it, mint a fresh valid UUID, and atomically overwrite the file.
        """
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        # Partial UUID left by an interrupted write.
        self.device_id_path.write_text("11111111-222", encoding="utf-8")
        fixed = "33333333-4444-5555-6666-777777777777"

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR", self.state_dir), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()

        # Regenerated, and the corrupt value is gone — replaced by a valid UUID.
        self.assertEqual(result, fixed)
        uuid.UUID(result)  # parses -> well-formed
        on_disk = self.device_id_path.read_text(encoding="utf-8")
        self.assertEqual(on_disk, fixed)
        uuid.UUID(on_disk)  # the persisted value is now valid

    def test_fallback_garbage_non_uuid_regenerates(self):
        """An arbitrary non-UUID string (manual edit / other tool) is rejected."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        self.device_id_path.write_text("not-a-uuid-at-all", encoding="utf-8")
        fixed = "44444444-5555-6666-7777-888888888888"

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR", self.state_dir), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()

        self.assertEqual(result, fixed)
        self.assertEqual(self.device_id_path.read_text(encoding="utf-8"), fixed)

    def test_fallback_unwritable_state_dir_returns_uuid_unpersisted(self):
        """No usable state dir -> return an (unpersisted) uuid, never raise."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        fixed = "99999999-8888-7777-6666-555555555555"
        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=False), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()
        self.assertEqual(result, fixed)

    def test_fallback_write_failure_still_returns_uuid(self):
        """A write error must not crash; the freshly-minted uuid is returned.

        Patches the atomic-write primitive (mkstemp) to raise.
        """
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        fixed = "12121212-3434-5656-7878-909090909090"

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR", self.state_dir), \
             patch.object(did_mod.tempfile, "mkstemp",
                          side_effect=OSError("read-only fs")), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()
        self.assertEqual(result, fixed)
        # Write failed -> nothing persisted.
        self.assertFalse(self.device_id_path.exists())


if __name__ == "__main__":
    unittest.main()
