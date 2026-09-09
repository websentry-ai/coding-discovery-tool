"""
Shared JetBrains config-folder naming rules.

Subfolders of the JetBrains config directory are named `<ProductCode><Version>`,
e.g. `IntelliJIdea2025.2`. These rules were duplicated across the macos/, linux/
and windows/ detectors and drifted; they live here so all three agree.
"""

import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from types import MappingProxyType
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Tuple

from .xml_helpers import safe_xml_fromstring

logger = logging.getLogger(__name__)

# Prefix-matched, because several are version-suffixed on disk, e.g. "JetBrainsClient241.18034.62".
JETBRAINS_SKIP_FOLDERS: FrozenSet[str] = frozenset({
    "consent", "DeviceId", "JetBrainsClient",
    "consentOptions", "PrivacyPolicy", "Toolbox",
})

# Android Studio is an IntelliJ-platform IDE, but Google ships it under its own vendor dir.
JETBRAINS_VENDOR_DIRS: Tuple[str, ...] = ("JetBrains", "Google")


def jetbrains_config_roots(settings_dir: Path) -> List[Path]:
    """The JetBrains-family config roots under a platform's per-user settings dir."""
    return [settings_dir / vendor for vendor in JETBRAINS_VENDOR_DIRS]


# Read-only: one object aliased into all three detectors, so in-place edits would leak.
JETBRAINS_IDE_NAME_MAPPING: Mapping[str, str] = MappingProxyType({
    "IntelliJIdea": "IntelliJ IDEA",
    "IdeaIC": "IntelliJ IDEA Community",
    "IdeaIE": "IntelliJ IDEA Educational",
    "AndroidStudio": "Android Studio",
    "Aqua": "Aqua",
    "PyCharm": "PyCharm",
    "PyCharmCE": "PyCharm Community",
    "WebStorm": "WebStorm",
    "PhpStorm": "PhpStorm",
    "GoLand": "GoLand",
    "Rider": "Rider",
    "CLion": "CLion",
    "RustRover": "RustRover",
    "RubyMine": "RubyMine",
    "DataGrip": "DataGrip",
    "DataSpell": "DataSpell",
})

# Splits "<Name><version>", e.g. "Writerside2024.1-EAP" -> ("Writerside", "2024.1-EAP").
VERSION_SUFFIX = re.compile(r'^([A-Za-z][A-Za-z ._-]*?)((?:\d+\.)+\d+(?:[-.][A-Za-z0-9][A-Za-z0-9.-]*)?)$')

# Real config folders carry a version ("CLion2025.3"); uninstall leftovers don't ("Clion").
VERSIONED_FOLDER = re.compile(r'^[A-Za-z][A-Za-z ._-]*\d+(?:\.\d+)+')

# Leading digits of a version segment, so "2-EAP" still orders as 2.
SEGMENT_NUMBER = re.compile(r'^\d+')


def version_sort_key(version: str) -> Tuple[Tuple[int, ...], int]:
    """Ordering key for an IDE version, so newer sorts higher.

    Numbers first, then stability, so 2025.1 < 2025.2-EAP < 2025.2: a
    prerelease outranks the older stable it supersedes but loses to the release
    it precedes, which is what leaves a lingering EAP config dir behind.
    Unparseable versions sort lowest.
    """
    segments = version.split('.')
    parts = []
    for segment in segments:
        match = SEGMENT_NUMBER.match(segment)
        if not match:
            break
        parts.append(int(match.group()))
    stable = 1 if parts and all(s.isdigit() for s in segments) else 0
    return (tuple(parts) if parts else (0,)), stable


def should_skip_folder(folder: str, skip_folders: Iterable[str]) -> bool:
    """Whether a config subfolder is internal/system. Entries match as name prefixes."""
    prefixes = (skip_folders,) if isinstance(skip_folders, str) else tuple(skip_folders)
    return folder.startswith(prefixes)


# Editions JetBrains ships at no cost; everything else needs a licence.
FREE_EDITION_MARKERS = ("IdeaIC", "IdeaIE", "PyCharmCE")


def detect_plan(folder_name: str) -> str:
    """Whether the edition is free, derived from the product code in the folder name."""
    return "Free" if any(m in folder_name for m in FREE_EDITION_MARKERS) else "Licensed"


def looks_like_ide_folder(folder: str) -> bool:
    """Whether the folder name carries a version, which uninstall leftovers do not."""
    return VERSIONED_FOLDER.match(folder) is not None


def parse_ide_name_and_version(folder_name: str, mapping: Mapping[str, str]) -> Tuple[str, str]:
    """
    Derive (display_name, version) from a config folder name; version is "Unknown" if absent.

    The mapping runs first so branded names win: `IntelliJIdea2025.2` is "IntelliJ IDEA".
    A mapped prefix only claims the folder when what follows it is a version, or
    `PyCharmEdu2024.1` would share a display_name with PyCharm and `_filter_old_versions`
    would drop one of the two installs.
    """
    for prefix in sorted(mapping, key=len, reverse=True):
        if not folder_name.startswith(prefix):
            continue
        version = folder_name[len(prefix):]
        if not version:
            return mapping[prefix], "Unknown"
        if version[0].isdigit():
            return mapping[prefix], version
        match = VERSION_SUFFIX.match(folder_name)
        # No clean split, e.g. "IntelliJIdea2024.1-EAP" -- keep the branded name.
        return (match.group(1).rstrip(" ._-"), match.group(2)) if match else (mapping[prefix], version)

    match = VERSION_SUFFIX.match(folder_name)
    if match:
        return match.group(1).rstrip(" ._-"), match.group(2)

    return folder_name, "Unknown"


def plugin_entries(ide: Mapping) -> List[Dict[str, Optional[str]]]:
    """
    Plugin entries of a detected IDE as ``{name, version}`` dicts.

    Falls back to the names-only ``plugins`` list so a caller that supplies just names
    still matches its plugin (with an unknown version) instead of detecting nothing.
    """
    details = ide.get("_plugin_details")
    if details:
        return details
    return [{"name": name, "version": None} for name in ide.get("plugins", [])]


def parse_plugin_metadata(xml_content: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Derive (plugin_id, plugin_name, plugin_version) from a plugin.xml document.

    Parsed as-is: modular plugins declare `xmlns:xi` and use `<xi:include>`, so stripping
    the declaration leaves an unbound prefix and the whole document fails to parse.
    """
    try:
        root = safe_xml_fromstring(xml_content)
    except ET.ParseError as exc:
        logger.debug(f"Failed to parse plugin.xml: {exc}")
        return None, None, None
    except Exception as exc:
        logger.debug(f"Error parsing plugin.xml: {exc}")
        return None, None, None

    def text(tag: str) -> Optional[str]:
        element = root.find(f".//{tag}")
        return element.text.strip() if element is not None and element.text else None

    return text("id") or root.get("id"), text("name"), text("version")
