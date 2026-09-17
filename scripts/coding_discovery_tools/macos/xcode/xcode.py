"""
Xcode coding intelligence detection for macOS.

Xcode on its own is a compiler, so an app bundle on disk is not a tool. A device
is considered an Xcode AI user only when the per-user CodingAssistant tree exists
at ~/Library/Developer/Xcode/CodingAssistant/ — Apple's documented location for
agent config, MCP servers and skills. If only the app is present (coding
intelligence never enabled), there is nothing to report on so we return None.
"""

import logging
import plistlib
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT, is_symlink_or_junction
from ...macos_extraction_helpers import MACHINE_APPS_DIR
from ...utils import dir_state, record_xcode_probe, run_command

logger = logging.getLogger(__name__)


XCODE_BUNDLE_ID = "com.apple.dt.Xcode"

CODING_ASSISTANT_DIR = Path("Library") / "Developer" / "Xcode" / "CodingAssistant"


def coding_assistant_dir(user_home: Path) -> Path:
    """Where Xcode keeps this user's agent config, MCP servers and skills."""
    return user_home / CODING_ASSISTANT_DIR


def agent_dirs(assistant_dir: Path) -> List[str]:
    """Agent subfolders present, e.g. ClaudeAgentConfig / codex / gemini."""
    try:
        return sorted(p.name for p in assistant_dir.iterdir() if p.is_dir())
    except OSError as e:
        logger.debug(f"Could not list {assistant_dir}: {e}")
        return []


def _in_scope(candidate: Path, user_home: Path) -> bool:
    """True when the bundle is machine-wide or inside the scanned user's home."""
    if is_symlink_or_junction(candidate):
        return False
    return candidate.parent == MACHINE_APPS_DIR or user_home in candidate.parents


def _active_developer_bundle() -> Optional[Path]:
    """The Xcode ``xcode-select`` points at, or None when only the CLI tools are installed."""
    output = run_command(["xcode-select", "-p"], COMMAND_TIMEOUT)
    if not output:
        return None
    for parent in Path(output.strip()).parents:
        if parent.suffix == ".app":
            return parent
    return None


def _spotlight_candidates(user_home: Path) -> List[Path]:
    """In-scope Xcode bundles Spotlight knows about; catches version-suffixed installs."""
    output = run_command(
        ["mdfind", f"kMDItemCFBundleIdentifier == '{XCODE_BUNDLE_ID}'"], COMMAND_TIMEOUT
    )
    if not output:
        return []
    found = []
    for line in output.splitlines():
        candidate = Path(line.strip())
        if candidate.suffix == ".app" and _in_scope(candidate, user_home):
            found.append(candidate)
    return found


class MacOSXcodeDetector(BaseToolDetector):
    """Xcode coding intelligence detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "Xcode Coding Intelligence"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _candidate_install_dirs(self, user_home: Path) -> List[Path]:
        """Where macOS puts Xcode.app: the selected one, the fixed paths, then Spotlight."""
        candidates = []
        active = _active_developer_bundle()
        if active is not None:
            candidates.append(active)
        candidates.append(MACHINE_APPS_DIR / "Xcode.app")
        candidates.append(user_home / "Applications" / "Xcode.app")
        candidates.extend(_spotlight_candidates(user_home))
        return candidates

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        home = self._scan_home(user_home)
        outcome = "absent"
        for candidate in self._candidate_install_dirs(home):
            if not _in_scope(candidate, home):
                continue
            state = dir_state(candidate)
            if state == "present":
                record_xcode_probe("bundle", "present")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"
        record_xcode_probe("bundle", outcome)
        if outcome == "unreadable":
            raise PermissionError("Xcode install dir unreadable")
        return None

    def detect(self) -> Optional[Dict]:
        home = self._scan_home()
        assistant_dir = coding_assistant_dir(home)

        state = dir_state(assistant_dir)
        record_xcode_probe("coding_assistant", state)
        if state == "unreadable":
            raise PermissionError(f"Xcode CodingAssistant dir unreadable: {assistant_dir}")
        if state != "present":
            return None

        record_xcode_probe("agents", "+".join(agent_dirs(assistant_dir)) or "none")

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
            logger.debug(f"Could not extract Xcode version: {e}")
            return None
