"""
Tests for container-aware device/home_user handling:

  1. get_device_id() honors an injected UNBOUND_DEVICE_SERIAL ONLY when we
     detect we're inside a container (so a stray env var on a native host can't
     mask the real auto-detected serial).
  2. generate_single_tool_report() namespaces home_user with the container id
     inside a container, so multiple containers sharing one injected device_id
     keep distinct profiles instead of clobbering each other on the backend
     (the ingest replace key is (device, tool_name, home_user)).
  3. utils.in_container() / utils.get_container_id() primitives.
"""

import os
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector
from scripts.coding_discovery_tools import utils

IN_CONTAINER = "scripts.coding_discovery_tools.ai_tools_discovery.in_container"
CONTAINER_ID = "scripts.coding_discovery_tools.ai_tools_discovery.get_container_id"


class TestDeviceIdOverride(unittest.TestCase):
    def setUp(self):
        self.detector = AIToolsDetector()
        # Stub the OS extractor so we can tell the override path apart from the
        # auto-detect fallback path.
        self.detector._device_id_extractor.extract_device_id = lambda: "AUTO_DETECTED_SERIAL"

    # --- inside a container: injection is honored -------------------------
    @patch(IN_CONTAINER, return_value=True)
    def test_valid_injected_serial_in_container(self, _):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "C6YKKG659H"}):
            self.assertEqual(self.detector.get_device_id(), "C6YKKG659H")

    @patch(IN_CONTAINER, return_value=True)
    def test_injected_serial_is_stripped_in_container(self, _):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "  C6YKKG659H  "}):
            self.assertEqual(self.detector.get_device_id(), "C6YKKG659H")

    @patch(IN_CONTAINER, return_value=True)
    def test_placeholder_serial_in_container_falls_through(self, _):
        # "N/A" is in INVALID_SERIAL_VALUES — must not be accepted.
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "N/A"}):
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")

    @patch(IN_CONTAINER, return_value=True)
    def test_unset_env_in_container_falls_through(self, _):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNBOUND_DEVICE_SERIAL", None)
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")

    # --- NOT in a container: injection is ignored (the gate) --------------
    @patch(IN_CONTAINER, return_value=False)
    def test_injected_serial_ignored_outside_container(self, _):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "C6YKKG659H"}):
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")


class TestHomeUserContainerNamespacing(unittest.TestCase):
    def setUp(self):
        self.detector = AIToolsDetector()
        self.tool = {"name": "Claude Code", "install_path": "/x"}

    @patch(IN_CONTAINER, return_value=False)
    def test_native_host_home_user_unchanged(self, _):
        report = self.detector.generate_single_tool_report(self.tool, "DEV1", "sumit")
        self.assertEqual(report["home_user"], "sumit")

    @patch(CONTAINER_ID, return_value="ws-benchling")
    @patch(IN_CONTAINER, return_value=True)
    def test_container_home_user_is_namespaced(self, *_):
        report = self.detector.generate_single_tool_report(self.tool, "C6YKKG659H", "ubuntu")
        # snake_case separator: "<container_id>_<home_user>"
        self.assertEqual(report["home_user"], "ws-benchling_ubuntu")
        # system_user stays the plain username (not namespaced).
        self.assertEqual(report["system_user"], "ubuntu")
        # device_id is untouched (shared across containers on the host).
        self.assertEqual(report["device_id"], "C6YKKG659H")

    @patch(IN_CONTAINER, return_value=True)
    def test_two_containers_same_device_get_distinct_profiles(self, _):
        with patch(CONTAINER_ID, return_value="abc123"):
            r1 = self.detector.generate_single_tool_report(self.tool, "C6YKKG659H", "ubuntu")
        with patch(CONTAINER_ID, return_value="def456"):
            r2 = self.detector.generate_single_tool_report(self.tool, "C6YKKG659H", "ubuntu")
        self.assertEqual(r1["home_user"], "abc123_ubuntu")
        self.assertEqual(r2["home_user"], "def456_ubuntu")
        # Same device, same base user, different container -> no clobber.
        self.assertEqual(r1["device_id"], r2["device_id"])
        self.assertNotEqual(r1["home_user"], r2["home_user"])


class TestContainerHelpers(unittest.TestCase):
    def setUp(self):
        utils.in_container.cache_clear()
        utils.get_container_id.cache_clear()

    def tearDown(self):
        utils.in_container.cache_clear()
        utils.get_container_id.cache_clear()

    def test_container_id_prefers_injected_env(self):
        with patch.dict(os.environ, {"UNBOUND_CONTAINER_ID": "ws-1"}):
            self.assertEqual(utils.get_container_id(), "ws-1")

    @patch("scripts.coding_discovery_tools.utils.get_hostname", return_value="hostxyz")
    def test_container_id_falls_back_to_hostname(self, _):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNBOUND_CONTAINER_ID", None)
            self.assertEqual(utils.get_container_id(), "hostxyz")

    def test_in_container_true_when_dockerenv_present(self):
        with patch("os.path.exists", side_effect=lambda p: p == "/.dockerenv"):
            self.assertTrue(utils.in_container())

    def test_in_container_false_with_no_signals(self):
        # No runtime markers, and /proc reads raise (as on macOS) -> not a container.
        with patch("os.path.exists", return_value=False), \
             patch("builtins.open", side_effect=OSError):
            self.assertFalse(utils.in_container())


if __name__ == "__main__":
    unittest.main()
