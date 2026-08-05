"""Tests for hardened XML parsing helpers (WEB-5305 / Aikido XXE findings)."""

import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts.coding_discovery_tools.xml_helpers import (
    UnsafeXMLError,
    safe_xml_fromstring,
    safe_xml_parse,
)


BILLION_LAUGHS = (
    '<?xml version="1.0"?>'
    "<!DOCTYPE lolz ["
    '<!ENTITY lol "lol">'
    '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
    '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
    "]>"
    "<lolz>&lol3;</lolz>"
)

XXE_FILE_READ = (
    '<?xml version="1.0"?>'
    '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
    "<foo>&xxe;</foo>"
)

EXTERNAL_DTD = '<!DOCTYPE root SYSTEM "http://attacker.example/evil.dtd"><root/>'


class TestSafeXmlFromstring(unittest.TestCase):
    def test_parses_benign_document(self):
        root = safe_xml_fromstring(
            "<application><component name='McpServers'><option value='x'/></component></application>"
        )
        self.assertEqual(root.find("component").get("name"), "McpServers")

    def test_parses_document_with_xml_declaration(self):
        root = safe_xml_fromstring(
            "<?xml version='1.0' encoding='UTF-8'?><idea-plugin><id>a.b</id></idea-plugin>"
        )
        self.assertEqual(root.find("id").text, "a.b")

    def test_rejects_billion_laughs(self):
        with self.assertRaises(UnsafeXMLError):
            safe_xml_fromstring(BILLION_LAUGHS)

    def test_rejects_external_entity(self):
        with self.assertRaises(UnsafeXMLError):
            safe_xml_fromstring(XXE_FILE_READ)

    def test_rejects_external_dtd(self):
        with self.assertRaises(UnsafeXMLError):
            safe_xml_fromstring(EXTERNAL_DTD)

    def test_unsafe_error_is_caught_by_parse_error_handlers(self):
        # Callers only catch ET.ParseError; unsafe files must be skippable the same way.
        with self.assertRaises(ET.ParseError):
            safe_xml_fromstring(XXE_FILE_READ)

    def test_malformed_raises_parse_error(self):
        with self.assertRaises(ET.ParseError):
            safe_xml_fromstring("<a><unclosed>")


class TestSafeXmlParse(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.tmp_path = Path(self._tmpdir.name)

    def test_parses_benign_file(self):
        xml_path = self.tmp_path / "mcpServers.xml"
        xml_path.write_text(
            "<?xml version='1.0'?><application><McpServerConfigurationProperties/></application>"
        )
        tree = safe_xml_parse(xml_path)
        self.assertIsNotNone(tree.getroot().find("McpServerConfigurationProperties"))
        self.assertEqual(len(tree.findall(".//McpServerConfigurationProperties")), 1)

    def test_rejects_xxe_file(self):
        xml_path = self.tmp_path / "evil.xml"
        xml_path.write_text(XXE_FILE_READ)
        with self.assertRaises(UnsafeXMLError):
            safe_xml_parse(xml_path)

    def test_malformed_file_raises_parse_error(self):
        xml_path = self.tmp_path / "broken.xml"
        xml_path.write_text("not xml at all")
        with self.assertRaises(ET.ParseError):
            safe_xml_parse(xml_path)

    def test_missing_file_raises_os_error(self):
        with self.assertRaises(OSError):
            safe_xml_parse(self.tmp_path / "does_not_exist.xml")


if __name__ == "__main__":
    unittest.main()
