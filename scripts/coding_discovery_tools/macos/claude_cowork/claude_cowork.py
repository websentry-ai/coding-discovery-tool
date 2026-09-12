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
from ...constants import COMMAND_TIMEOUT
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


def _spotlight_install_dir(user_home: Path) -> Optional[Path]:
    """Claude.app wherever it is installed, asked of Spotlight. None when it cannot answer.

    Last resort only: the fixed paths above miss an install anywhere else, and a
    Cowork user whose bundle we cannot find reports as having no tool at all.
    Spotlight is off, unindexed or empty-under-root on plenty of managed Macs, so
    this returns None rather than failing the scan.
    """
    output = run_command(["mdfind", f"kMDItemCFBundleIdentifier == '{CLAUDE_BUNDLE_ID}'"],
                         COMMAND_TIMEOUT)
    for line in (output or "").splitlines():
        candidate = Path(line.strip())
        if candidate.suffix != ".app":
            continue
        # Same scope as the fixed list: machine-wide, or inside the scanned user's
        # home. Anything else is another user's install, or a copy in Trash or on a
        # mounted volume.
        if candidate.parent != MACHINE_APPS_DIR and user_home not in candidate.parents:
            continue
        if any(part.startswith(".") for part in candidate.parts):
            continue
        return candidate
    return None


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
        outcome = "absent"
        for candidate in _candidate_install_dirs(self._scan_home(user_home)):
            state = dir_state(candidate)
            if state == "present":
                record_cowork_probe("bundle", "present")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"
        found = _spotlight_install_dir(self._scan_home(user_home))
        if found is not None and dir_state(found) == "present":
            record_cowork_probe("bundle", "spotlight")
            return found
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

        if not (sessions_present and self._find_install_dir()):
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(),
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
