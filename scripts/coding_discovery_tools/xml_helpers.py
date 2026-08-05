"""
Hardened XML parsing helpers.

JetBrains config files (mcp.json sibling XMLs, plugin.xml, options/*.xml) live in
user-writable locations, so their contents are untrusted. Parsing them with plain
xml.etree.ElementTree allows XXE and entity-expansion (billion laughs) attacks via
DTD/entity declarations.

This project ships with no external dependencies, so defusedxml is not an option.
Instead, every document is first run through a bare expat parser whose DTD and
entity handlers reject the document outright, then parsed normally with
ElementTree. Config files never legitimately contain DTDs, so rejected files are
simply skipped by callers.
"""

import xml.etree.ElementTree as ET
import xml.parsers.expat
from typing import Union


class UnsafeXMLError(ET.ParseError):
    """Raised when a document contains a DTD or entity declaration.

    Subclasses ET.ParseError so existing `except ET.ParseError` handlers
    treat unsafe files the same as malformed ones: log and skip.
    """


def _reject_unsafe_constructs(data: Union[str, bytes]) -> None:
    parser = xml.parsers.expat.ParserCreate()

    def _forbid(*_args) -> None:
        raise UnsafeXMLError(
            "XML document contains a forbidden construct (DTD / entity declaration)"
        )

    parser.StartDoctypeDeclHandler = _forbid
    parser.EntityDeclHandler = _forbid
    parser.UnparsedEntityDeclHandler = _forbid
    parser.ExternalEntityRefHandler = _forbid
    try:
        parser.Parse(data, True)
    except xml.parsers.expat.ExpatError:
        # Malformed XML: fall through so the ElementTree pass raises the
        # canonical ET.ParseError callers already handle.
        pass


def safe_xml_fromstring(text: Union[str, bytes]) -> ET.Element:
    """Drop-in replacement for ET.fromstring that rejects DTDs and entities."""
    _reject_unsafe_constructs(text)
    parser = ET.XMLParser()
    parser.feed(text)
    return parser.close()


def safe_xml_parse(source) -> ET.ElementTree:
    """Drop-in replacement for ET.parse (path input) that rejects DTDs and entities."""
    with open(source, "rb") as f:
        data = f.read()
    _reject_unsafe_constructs(data)
    parser = ET.XMLParser()
    parser.feed(data)
    return ET.ElementTree(parser.close())
