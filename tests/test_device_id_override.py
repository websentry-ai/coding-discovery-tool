"""
Tests for the UNBOUND_DEVICE_SERIAL host-serial override in get_device_id().

A container is ephemeral, so its auto-detected device_id falls back to the
container hostname (a new "device" every launch). When the host serial is
injected via UNBOUND_DEVICE_SERIAL, get_device_id() must report the HOST
device instead. When the env var is unset or holds a junk/placeholder value,
get_device_id() must fall through to the normal extractor so native hosts are
unaffected.
"""

import os
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.ai_tools_discovery import AIToolsDetector


class TestDeviceIdOverride(unittest.TestCase):
    def setUp(self):
        self.detector = AIToolsDetector()
        # Stub the OS-specific extractor so we can tell the override path
        # apart from the auto-detect fallback path.
        self.detector._device_id_extractor.extract_device_id = lambda: "AUTO_DETECTED_SERIAL"

    def test_valid_injected_serial_takes_precedence(self):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "C6YKKG659H"}):
            self.assertEqual(self.detector.get_device_id(), "C6YKKG659H")

    def test_injected_serial_is_stripped(self):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "  C6YKKG659H  "}):
            self.assertEqual(self.detector.get_device_id(), "C6YKKG659H")

    def test_unset_env_falls_through_to_extractor(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UNBOUND_DEVICE_SERIAL", None)
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")

    def test_empty_env_falls_through_to_extractor(self):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": ""}):
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")

    def test_whitespace_only_env_falls_through_to_extractor(self):
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "   "}):
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")

    def test_placeholder_serial_falls_through_to_extractor(self):
        # "N/A" is in INVALID_SERIAL_VALUES — must not be accepted as a device id.
        with patch.dict(os.environ, {"UNBOUND_DEVICE_SERIAL": "N/A"}):
            self.assertEqual(self.detector.get_device_id(), "AUTO_DETECTED_SERIAL")


if __name__ == "__main__":
    unittest.main()
