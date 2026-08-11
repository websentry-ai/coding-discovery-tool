"""
Shared JetBrains config-folder naming rules.

Subfolders of the JetBrains config directory are named `<ProductCode><Version>`,
e.g. `IntelliJIdea2025.2`. These rules were duplicated across the macos/, linux/
and windows/ detectors and drifted; they live here so all three agree.
"""

import re
from types import MappingProxyType
from typing import FrozenSet, Iterable, Mapping, Tuple

# Prefix-matched, because several are version-suffixed on disk, e.g. "JetBrainsClient241.18034.62".
JETBRAINS_SKIP_FOLDERS: FrozenSet[str] = frozenset({
    "consent", "DeviceId", "JetBrainsClient",
    "consentOptions", "PrivacyPolicy", "Toolbox",
})

# Read-only: one object aliased into all three detectors, so in-place edits would leak.
JETBRAINS_IDE_NAME_MAPPING: Mapping[str, str] = MappingProxyType({
    "IntelliJIdea": "IntelliJ IDEA",
    "IdeaIC": "IntelliJ IDEA Community",
    "IdeaIE": "IntelliJ IDEA Educational",
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


def should_skip_folder(folder: str, skip_folders: Iterable[str]) -> bool:
    """Whether a config subfolder is internal/system. Entries match as name prefixes."""
    prefixes = (skip_folders,) if isinstance(skip_folders, str) else tuple(skip_folders)
    return folder.startswith(prefixes)


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
