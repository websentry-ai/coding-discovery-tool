"""
Shared resolution for the Claude Desktop app bundle on macOS.

Claude Desktop and Cowork are separate tool rows resolved from the same bundle,
so the candidate paths, the Spotlight fallback and the version read live here
rather than being duplicated in either detector.
"""

import logging
import plistlib
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from ..constants import COMMAND_TIMEOUT
from ..macos_extraction_helpers import path_in_scope
from ..utils import dir_state, run_command_status

logger = logging.getLogger(__name__)


CLAUDE_DESKTOP_APP_PATH = Path("/Applications/Claude.app")

CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"


def claude_app_candidates(user_home: Path) -> List[Path]:
    """Where macOS puts Claude.app: machine-wide, then the scanned user's own."""
    return [
        CLAUDE_DESKTOP_APP_PATH,
        user_home / "Applications" / "Claude.app",
    ]


def claude_app_support_dir(user_home: Path) -> Path:
    """Claude Desktop's per-user data directory.

    Anthropic documents ``claude_desktop_config.json`` as living here, and the
    directory is created on first run, so its presence is what separates an
    installed bundle from one that has been opened.
    """
    return user_home / "Library" / "Application Support" / "Claude"


def spotlight_claude_bundles(user_home: Path) -> Tuple[List[Path], bool]:
    """``(bundles, probed)`` for in-scope Claude.app bundles Spotlight knows about.

    Last resort only: the fixed paths miss an install anywhere else, and a user
    whose bundle we cannot find reports as having no tool at all. A search that
    ran and matched nothing is absence; one that could not run is ignorance, and
    reading that as absence is what pruned live Cowork rows in incident 326.
    """
    output, ran = run_command_status(
        ["mdfind", f"kMDItemCFBundleIdentifier == '{CLAUDE_BUNDLE_ID}'"], COMMAND_TIMEOUT
    )
    if not ran:
        logger.debug("Spotlight could not run for %s", CLAUDE_BUNDLE_ID)
        return [], False
    found = []
    for line in (output or "").splitlines():
        candidate = Path(line.strip())
        if candidate.suffix != ".app":
            continue
        if not path_in_scope(candidate, user_home):
            logger.debug("Ignoring out-of-scope Spotlight hit %s for %s", candidate, user_home)
            continue
        found.append(candidate)
    return found, True


def resolve_claude_bundle(user_home: Path, on_probe: Optional[Callable[[str, str], None]] = None) -> Optional[Path]:
    """First readable Claude.app for this user: fixed paths first, Spotlight only if they miss.

    Raises PermissionError when a candidate exists but cannot be read, or when
    Spotlight never ran, so the caller reports the install as unknown rather
    than absent. Only a probe that ran and found nothing means absent.
    """
    def probe(outcome: str) -> None:
        if on_probe:
            on_probe("bundle", outcome)

    outcome = "absent"
    for candidate in claude_app_candidates(user_home):
        state = dir_state(candidate)
        if state == "present":
            probe("present")
            return candidate
        if state == "unreadable":
            outcome = "unreadable"

    candidates, probed = spotlight_claude_bundles(user_home)
    for candidate in candidates:
        state = dir_state(candidate)
        if state == "present":
            probe("spotlight")
            return candidate
        logger.debug("Spotlight hit %s is %s; trying the next result", candidate, state)
        if state == "unreadable":
            outcome = "unreadable"
    if outcome == "absent" and not probed:
        outcome = "unprobed"

    probe(outcome)
    if outcome in ("unreadable", "unprobed"):
        raise PermissionError(f"Claude Desktop install dir {outcome} under {user_home}")
    return None


def claude_bundle_version(app_bundle: Path) -> Optional[str]:
    """CFBundleShortVersionString from the bundle, or None on any error.

    Version is informational and must never block detection.
    """
    try:
        info_plist = app_bundle / "Contents" / "Info.plist"
        if not info_plist.exists():
            return None
        with info_plist.open("rb") as fh:
            plist = plistlib.load(fh)
        version = plist.get("CFBundleShortVersionString")
        if isinstance(version, str) and version.strip():
            return version.strip()
        return None
    except Exception as e:
        logger.debug(f"Could not read Claude bundle version: {e}")
        return None
