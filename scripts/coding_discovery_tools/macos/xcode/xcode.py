"""
Xcode coding intelligence detection for macOS.

Xcode on its own is a compiler, so an app bundle on disk is not a tool. A device
is considered an Xcode AI user only when the per-user CodingAssistant tree exists
at ~/Library/Developer/Xcode/CodingAssistant/ — Apple's documented location for
agent config, MCP servers and skills. If only the app is present (coding
intelligence never enabled), there is nothing to report on so we return None.
"""

import logging
import os
import plistlib
import stat
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT
from ...macos_extraction_helpers import MACHINE_APPS_DIR, path_in_scope
from ...utils import dir_state, record_xcode_probe, run_command_status

logger = logging.getLogger(__name__)


XCODE_BUNDLE_ID = "com.apple.dt.Xcode"

CODING_ASSISTANT_DIR = Path("Library") / "Developer" / "Xcode" / "CodingAssistant"

# Apple's documented agent subfolders. Anything else is counted, never named: the
# tree is user-writable and its names would otherwise reach Sentry verbatim.
KNOWN_AGENT_DIRS = frozenset({"ClaudeAgentConfig", "codex", "gemini"})

_MAX_AGENT_PROBE_LEN = 64

# O_NOFOLLOW/O_NONBLOCK are POSIX-only and O_BINARY is Windows-only. The detector is
# macOS-only but its tests run on the Windows matrix, so resolve them defensively.
_PLIST_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)


def coding_assistant_dir(user_home: Path) -> Path:
    """Where Xcode keeps this user's agent config, MCP servers and skills."""
    return user_home / CODING_ASSISTANT_DIR


def agent_summary(assistant_dir: Path) -> str:
    """Allowlisted agent dirs plus a count of the rest, or ``unreadable``.

    A denied listing must not read as ``none``: the gate has already passed, so
    an empty answer here would understate what the device is running.
    """
    try:
        names = [p.name for p in assistant_dir.iterdir() if p.is_dir()]
    except OSError as e:
        logger.debug(f"Could not list {assistant_dir}: {e}")
        return "unreadable"
    known = sorted(n for n in names if n in KNOWN_AGENT_DIRS)
    other = len(names) - len(known)
    if other:
        known.append(f"other:{other}")
    return "+".join(known)[:_MAX_AGENT_PROBE_LEN] or "none"


def _active_developer_bundle() -> Tuple[Optional[Path], bool]:
    """``(bundle, probed)`` for the Xcode ``xcode-select`` points at.

    ``probed`` is False only when the command could not run. A clean answer that
    names no bundle — Command Line Tools only, or no developer dir — is absence.
    """
    output, ran = run_command_status(["xcode-select", "-p"], COMMAND_TIMEOUT)
    if not ran:
        return None, False
    if output:
        for parent in Path(output).parents:
            if parent.suffix == ".app":
                return parent, True
    return None, True


def _spotlight_candidates(user_home: Path) -> Tuple[List[Path], bool]:
    """``(bundles, probed)`` for in-scope Xcode bundles Spotlight knows about.

    Catches version-suffixed installs (``Xcode-16.2.app``) the fixed paths miss.
    A search that ran and matched nothing is absence, not ignorance.
    """
    output, ran = run_command_status(
        ["mdfind", f"kMDItemCFBundleIdentifier == '{XCODE_BUNDLE_ID}'"], COMMAND_TIMEOUT
    )
    if not ran:
        logger.debug("Spotlight could not run for %s", XCODE_BUNDLE_ID)
        return [], False
    found = []
    for line in (output or "").splitlines():
        candidate = Path(line.strip())
        if candidate.suffix == ".app" and path_in_scope(candidate, user_home):
            found.append(candidate)
    return found, True


def _read_bundle_version(app_bundle: Path) -> Optional[str]:
    """CFBundleShortVersionString, read without following a redirected Info.plist.

    The bundle can sit under a user-writable home, so the plist is opened
    O_NOFOLLOW and must be a regular file: a symlink to a FIFO would otherwise
    block the whole scan under root.
    """
    info_plist = app_bundle / "Contents" / "Info.plist"
    try:
        if not stat.S_ISREG(os.lstat(info_plist).st_mode):
            return None
        fd = os.open(info_plist, _PLIST_OPEN_FLAGS)
    except OSError as e:
        logger.debug(f"Could not open {info_plist}: {e}")
        return None
    try:
        with os.fdopen(fd, "rb") as fh:
            plist = plistlib.load(fh)
    except Exception as e:
        logger.debug(f"Could not parse {info_plist}: {e}")
        return None
    version = plist.get("CFBundleShortVersionString") if isinstance(plist, dict) else None
    return version.strip() if isinstance(version, str) and version.strip() else None


class MacOSXcodeDetector(BaseToolDetector):
    """Xcode coding intelligence detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "Xcode Coding Intelligence"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        """The user's Xcode bundle, or None when the machine genuinely has none.

        Raises when a probe failed or a candidate was unreadable: a clean absence
        lets the backend prune a live install, so an unknown must reach the
        anomaly path instead (incident 326).
        """
        home = self._scan_home(user_home)
        outcome = "absent"

        active, active_probed = _active_developer_bundle()
        fixed = [MACHINE_APPS_DIR / "Xcode.app", home / "Applications" / "Xcode.app"]
        for candidate in ([active] if active else []) + fixed:
            if not path_in_scope(candidate, home):
                continue
            state = dir_state(candidate)
            if state == "present":
                record_xcode_probe("bundle", "present")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"

        spotlight, spotlight_probed = _spotlight_candidates(home)
        for candidate in spotlight:
            state = dir_state(candidate)
            if state == "present":
                record_xcode_probe("bundle", "spotlight")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"

        if outcome == "absent" and not (active_probed and spotlight_probed):
            outcome = "unknown"
        record_xcode_probe("bundle", outcome)
        if outcome != "absent":
            raise PermissionError(f"Xcode install dir {outcome}")
        return None

    def detect(self) -> Optional[Dict]:
        home = self._scan_home()
        assistant_dir = coding_assistant_dir(home)

        if not path_in_scope(assistant_dir, home):
            record_xcode_probe("coding_assistant", "redirected")
            return None

        state = dir_state(assistant_dir)
        record_xcode_probe("coding_assistant", state)
        if state == "unreadable":
            raise PermissionError(f"Xcode CodingAssistant dir unreadable: {assistant_dir}")
        if state != "present":
            return None

        record_xcode_probe("agents", agent_summary(assistant_dir))

        app_install = self._find_install_dir()
        if app_install is None:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(app_install),
            "install_path": str(assistant_dir),
        }

    def get_version(self, app_install: Optional[Path] = None) -> Optional[str]:
        """
        Read CFBundleShortVersionString from Xcode's Info.plist.
        Returns None on any error — version is informational and must not
        block detection.
        """
        try:
            app_bundle = app_install or self._find_install_dir()
            if app_bundle is None:
                return None
            return _read_bundle_version(app_bundle)
        except Exception as e:
            logger.debug(f"Could not extract Xcode version: {e}")
            return None
