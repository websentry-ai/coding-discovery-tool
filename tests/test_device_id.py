"""Windows and macOS device IDs come from the hardware serial only, with no fallback."""
import unittest
from unittest.mock import patch

from scripts.coding_discovery_tools.macos.device_id import MacOSDeviceIdExtractor
from scripts.coding_discovery_tools.windows.device_id import WindowsDeviceIdExtractor

WIN_RUN = "scripts.coding_discovery_tools.windows.device_id.run_command"
MAC_RUN = "scripts.coding_discovery_tools.macos.device_id.run_command"
IOREG = '    | "IOPlatformSerialNumber" = "{}"\n'


class TestWindowsDeviceId(unittest.TestCase):
    def test_returns_bios_serial(self):
        with patch(WIN_RUN, return_value="FGV8B54"):
            self.assertEqual(WindowsDeviceIdExtractor().extract_device_id(), "FGV8B54")

    def test_raises_when_unreadable(self):
        with patch(WIN_RUN, return_value=None):
            with self.assertRaises(RuntimeError):
                WindowsDeviceIdExtractor().extract_device_id()

    def test_raises_on_placeholder_serial(self):
        with patch(WIN_RUN, return_value="To Be Filled By O.E.M."):
            with self.assertRaises(RuntimeError):
                WindowsDeviceIdExtractor().extract_device_id()


class TestMacOSDeviceId(unittest.TestCase):
    def test_returns_serial_from_ioreg(self):
        with patch(MAC_RUN, return_value=IOREG.format("C02ABC123XYZ")):
            self.assertEqual(MacOSDeviceIdExtractor().extract_device_id(), "C02ABC123XYZ")

    def test_raises_when_unreadable(self):
        with patch(MAC_RUN, return_value=None):
            with self.assertRaises(RuntimeError):
                MacOSDeviceIdExtractor().extract_device_id()

    def test_raises_on_placeholder_serial(self):
        with patch(MAC_RUN, return_value=IOREG.format("Default string")):
            with self.assertRaises(RuntimeError):
                MacOSDeviceIdExtractor().extract_device_id()


if __name__ == "__main__":
    unittest.main()
