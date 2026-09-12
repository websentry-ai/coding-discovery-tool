"""
Claude Cowork detection for macOS.

Cowork is the agentic feature of the Claude Desktop app. We treat it as a
distinct tool from Claude Code (which is the CLI). A device is considered
to have Cowork installed if BOTH:

    - A Claude Desktop app bundle is discoverable on disk (machine-wide in
      /Applications, or the user's own ~/Applications for a non-admin
      install), AND
    - The on-disk session tree exists at
      ~/Library/Application Support/Claude/local-agent-mode-sessions/

If only the app is present (Cowork never enabled / never used), there is
nothing to report on so we return None.
"""

import logging
import plistlib
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...claude_cowork_skills_helpers import COWORK_SESSIONS_DIR
from ...constants import COMMAND_TIMEOUT, is_symlink_or_junction
from ...macos_extraction_helpers import MACHINE_APPS_DIR
from ...utils import dir_state, record_cowork_probe, run_command

logger = logging.getLogger(__name__)


CLAUDE_DESKTOP_APP_PATH = Path("/Applications/Claude.app")


CLAUDE_BUNDLE_ID = "com.anthropic.claudefordesktop"


def _candidate_install_dirs(user_home: Path) -> List[Path]:
    """Where macOS puts Claude.app: machine-wide, then the scanned user's own."""
    return [
        CLAUDE_DESKTOP_APP_PATH,
        user_home / "Applications" / "Claude.app",
    ]


def _scope_root(candidate: Path, user_home: Path) -> Optional[Path]:
    """The root that owns ``candidate``: machine-wide, or the scanned user's home."""
    if candidate.parent == MACHINE_APPS_DIR:
        return MACHINE_APPS_DIR
    return user_home if user_home in candidate.parents else None


def _in_scope(candidate: Path, user_home: Path) -> bool:
    """True when ``candidate`` is really inside a root we attribute to this user.

    Lexical containment is not enough: a link anywhere below the root redirects out
    of it, and ``dir_state`` follows links, so one user's bundle could be attributed
    to another. Every component below the root is checked, hidden ones (``.Trash``)
    rejected outright.
    """
    root = _scope_root(candidate, user_home)
    if root is None:
        return False
    current = root
    for part in candidate.relative_to(root).parts:
        if part.startswith("."):
            return False
        current = current / part
        if is_symlink_or_junction(current):
            return False
    return True


def _spotlight_candidates(user_home: Path) -> List[Path]:
    """In-scope Claude.app bundles Spotlight knows about, best-effort and possibly empty.

    Last resort only: the fixed paths miss an install anywhere else, and a Cowork
    user whose bundle we cannot find reports as having no tool at all. Returns
    candidates rather than an answer so the caller applies the same present /
    unreadable handling it gives the fixed paths.
    """
    output = run_command(["mdfind", f"kMDItemCFBundleIdentifier == '{CLAUDE_BUNDLE_ID}'"],
                         COMMAND_TIMEOUT)
    if not output:
        logger.debug("Spotlight gave no answer for %s: absent, unindexed, denied under root, "
                     "or mdfind unavailable", CLAUDE_BUNDLE_ID)
        return []
    found = []
    for line in output.splitlines():
        candidate = Path(line.strip())
        if candidate.suffix != ".app":
            continue
        if not _in_scope(candidate, user_home):
            logger.debug("Ignoring out-of-scope Spotlight hit %s for %s", candidate, user_home)
            continue
        found.append(candidate)
    return found


def _get_cowork_sessions_dir(user_home: Path) -> Path:
    """Path to Claude Desktop's on-disk Cowork sessions tree."""
    return (
        user_home
        / "Library"
        / "Application Support"
        / "Claude"
        / COWORK_SESSIONS_DIR
    )


class MacOSClaudeCoworkDetector(BaseToolDetector):
    """Claude Cowork detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "Claude Cowork"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        home = self._scan_home(user_home)
        outcome = "absent"
        for candidate in _candidate_install_dirs(home):
            state = dir_state(candidate)
            if state == "present":
                record_cowork_probe("bundle", "present")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"
        # Spotlight is consulted only once the fixed paths have missed.
        for candidate in _spotlight_candidates(home):
            state = dir_state(candidate)
            if state == "present":
                record_cowork_probe("bundle", "spotlight")
                return candidate
            logger.debug("Spotlight hit %s is %s; trying the next result", candidate, state)
            if state == "unreadable":
                outcome = "unreadable"
        record_cowork_probe("bundle", outcome)
        if outcome == "unreadable":
            raise PermissionError("Claude Desktop install dir unreadable")
        return None

    def detect(self) -> Optional[Dict]:
        sessions_dir = _get_cowork_sessions_dir(self._scan_home())
        try:
            sessions_present = sessions_dir.exists() and sessions_dir.is_dir()
        except OSError as e:
            logger.debug(f"Error checking Claude Cowork install: {e}")
            return None

        if not sessions_present:
            return None
        # Reuse the resolved bundle: get_version() with no arg resolves it again,
        # which would run the Spotlight lookup a second time.
        app_install = self._find_install_dir()
        if app_install is None:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(app_install),
            "install_path": str(sessions_dir),
        }

    def get_version(self, app_install: Optional[Path] = None) -> Optional[str]:
        """
        Read CFBundleShortVersionString from Claude Desktop's Info.plist.
        Returns None on any error — version is informational and must not
        block detection.
        """
        try:
            app_bundle = app_install or self._find_install_dir()
            if app_bundle is None:
                return None
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
            logger.debug(f"Could not extract Claude Cowork version: {e}")
            return None
