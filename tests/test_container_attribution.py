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
    """Persisted-UUID device_id fallback when machine-id is empty/absent."""

    def setUp(self):
        from scripts.coding_discovery_tools.linux.device_id import LinuxDeviceIdExtractor
        self.extractor = LinuxDeviceIdExtractor()

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
        """First call with empty machine-id -> generate uuid4 and write it."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        fixed = "11111111-2222-3333-4444-555555555555"
        written = {}
        state_dir = Path("/fake/home/.unbound")

        device_id_path = MockPath(state_dir / "device-id", exists=False,
                                   write_sink=written)

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR",
                          MockDir(state_dir, device_id_path)), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()

        self.assertEqual(result, fixed)
        self.assertEqual(written.get("content"), fixed)

    def test_fallback_reads_existing_uuid(self):
        """Second/restarted run -> read the persisted uuid, do NOT regenerate."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        persisted = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        state_dir = Path("/fake/home/.unbound")
        written = {}

        device_id_path = MockPath(state_dir / "device-id", exists=True,
                                  read_value=f"  {persisted}\n", write_sink=written)

        # uuid4 would raise if called — proving we read instead of generate.
        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR",
                          MockDir(state_dir, device_id_path)), \
             patch.object(did_mod.uuid, "uuid4",
                          side_effect=AssertionError("uuid4 should not be called")):
            result = self.extractor.extract_device_id()

        self.assertEqual(result, persisted)
        self.assertNotIn("content", written)  # nothing rewritten

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
        """A write error must not crash; the freshly-minted uuid is returned."""
        from scripts.coding_discovery_tools.linux import device_id as did_mod

        fixed = "12121212-3434-5656-7878-909090909090"
        state_dir = Path("/fake/home/.unbound")
        device_id_path = MockPath(state_dir / "device-id", exists=False,
                                  write_error=OSError("read-only fs"))

        with patch.object(did_mod, "_MACHINE_ID_PATHS", []), \
             patch.object(did_mod.cache, "_ensure_state_dir", return_value=True), \
             patch.object(did_mod.cache, "UNBOUND_DIR",
                          MockDir(state_dir, device_id_path, mkdir_error=None)), \
             patch.object(did_mod.uuid, "uuid4", return_value=uuid.UUID(fixed)):
            result = self.extractor.extract_device_id()
        self.assertEqual(result, fixed)


class MockPath:
    """Stand-in for the resolved ``device-id`` file path."""

    def __init__(self, path, exists=False, read_value="", write_sink=None,
                 write_error=None):
        self._path = path
        self._exists = exists
        self._read_value = read_value
        self._write_sink = write_sink if write_sink is not None else {}
        self._write_error = write_error

    def exists(self):
        return self._exists

    def is_file(self):
        return self._exists

    def read_text(self, encoding="utf-8"):
        return self._read_value

    def write_text(self, content, encoding="utf-8"):
        if self._write_error is not None:
            raise self._write_error
        self._write_sink["content"] = content

    def __str__(self):
        return str(self._path)


class MockDir:
    """Stand-in for ``cache.UNBOUND_DIR`` supporting ``/`` and ``mkdir``."""

    def __init__(self, path, child, mkdir_error=None):
        self._path = path
        self._child = child
        self._mkdir_error = mkdir_error

    def __truediv__(self, name):
        return self._child

    def mkdir(self, parents=False, exist_ok=False):
        if self._mkdir_error is not None:
            raise self._mkdir_error

    def __str__(self):
        return str(self._path)


if __name__ == "__main__":
    unittest.main()
