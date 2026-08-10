"""
Shared JetBrains config-folder naming rules.

The three OS detectors (macos/, linux/, windows/) each scan a JetBrains config
directory whose subfolders are named `<ProductCode><Version>`, e.g.
`IntelliJIdea2025.2`. Both the skip list and the name/version split used to be
duplicated per OS, which let them drift; they now live here so all three
detectors resolve identical names, versions, and skips for the same folder.
"""

import re
from types import MappingProxyType
from typing import FrozenSet, Iterable, Mapping, Tuple

# Prefix-matched, because several are version-suffixed on disk, e.g. "JetBrainsClient241.18034.62".
JETBRAINS_SKIP_FOLDERS: FrozenSet[str] = frozenset({
    "consent", "DeviceId", "JetBrainsClient",
    "consentOptions", "PrivacyPolicy", "Toolbox",
})

# Read-only: this one object is aliased into all three detectors, so an in-place
# edit anywhere would leak everywhere. Rebinding a detector's attribute is fine.
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

# Splits "<Name><dotted version>", e.g. "Writerside2024.1" -> ("Writerside", "2024.1").
VERSION_SUFFIX = re.compile(r'^([A-Za-z][A-Za-z ._-]*?)((?:\d+\.)+\d+)$')


def should_skip_folder(folder: str, skip_folders: Iterable[str]) -> bool:
    """
    Report whether a JetBrains config subfolder is an internal/system folder.

    Args:
        folder: Config subfolder name, e.g. "JetBrainsClient241.18034.62"
        skip_folders: Skip entries to match as a name prefix; a bare str counts
            as one entry, not as its characters

    Returns:
        True if the folder should be excluded from IDE detection
    """
    prefixes = (skip_folders,) if isinstance(skip_folders, str) else tuple(skip_folders)
    return folder.startswith(prefixes)


def parse_ide_name_and_version(folder_name: str, mapping: Mapping[str, str]) -> Tuple[str, str]:
    """
    Derive an IDE display name and version from its config folder name.

    Known product codes resolve through `mapping` first so their branded names
    win (`IntelliJIdea2025.2` -> "IntelliJ IDEA", not "IntelliJIdea"). Unmapped
    products fall back to splitting a trailing dotted version off the name, so a
    newly released IDE reports as ("Writerside", "2024.1") rather than being
    named after its raw folder.

    Args:
        folder_name: Config subfolder name, e.g. "PyCharmCE2024.1"
        mapping: Product-code prefix -> display name

    Returns:
        Tuple of (display_name, version); version is "Unknown" if none is present
    """
    for prefix in sorted(mapping, key=len, reverse=True):
        if folder_name.startswith(prefix):
            version = folder_name[len(prefix):]
            return mapping[prefix], version if version else "Unknown"

    match = VERSION_SUFFIX.match(folder_name)
    if match:
        return match.group(1).rstrip(" ._-"), match.group(2)

    return folder_name, "Unknown"
