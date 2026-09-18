"""
GitHub Copilot for Xcode detection for macOS.

GitHub ships Copilot for Xcode as a dedicated app, so unlike Xcode itself the
bundle IS evidence of an AI tool. The bundle is machine-wide though, so on a
shared Mac a per-user marker decides which profiles actually ran it rather than
attributing one install to everyone.

The bundle name is matched exactly: a third-party project of the same name ships
as ``Copilot for Xcode.app`` under ``com.intii.CopilotForXcode``.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import MACHINE_APPS_DIR, macos_app_candidates, path_in_scope, read_bundle_version
from ...utils import dir_state, record_copilot_xcode_probe

logger = logging.getLogger(__name__)


APP_BUNDLE = MACHINE_APPS_DIR / "GitHub Copilot for Xcode.app"

# Per-user state written once the app has run. The log directory is the path
# GitHub documents (TROUBLESHOOTING.md); the rest are the app's sandboxed
# containers, taken from the Homebrew cask's uninstall paths.
USER_MARKERS = (
    Path("Library") / "Logs" / "GitHubCopilot",
    Path("Library") / "Application Support" / "com.github.CopilotForXcode",
    Path("Library") / "Group Containers" / "VEKTX9H2N7.group.com.github.CopilotForXcode",
    Path("Library") / "Containers" / "com.github.CopilotForXcode.EditorExtension",
    Path("Library") / "Preferences" / "com.github.CopilotForXcode.plist",
)


def _marker_state(path: Path) -> str:
    """``present``, ``absent`` or ``unreadable`` for a marker that may be a file.

    ``dir_state`` reports a regular file as absent, and one of the markers is a
    preferences plist.
    """
    try:
        os.stat(path)
        return "present"
    except (FileNotFoundError, NotADirectoryError):
        return "absent"
    except OSError:
        return "unreadable"


class MacOSGitHubCopilotXcodeDetector(BaseToolDetector):
    """GitHub Copilot for Xcode detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "GitHub Copilot (Xcode)"

    def _scan_home(self) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(getattr(self, "user_home", None) or Path.home())

    def _find_bundle(self, home: Path) -> Optional[Path]:
        """The installed bundle, or None. Raises when a candidate was unreadable:
        a clean absence lets the backend prune a live install."""
        outcome = "absent"
        for candidate in macos_app_candidates(APP_BUNDLE, home):
            if not path_in_scope(candidate, home):
                continue
            state = dir_state(candidate)
            if state == "present":
                record_copilot_xcode_probe("bundle", "present")
                return candidate
            if state == "unreadable":
                outcome = "unreadable"
        record_copilot_xcode_probe("bundle", outcome)
        if outcome == "unreadable":
            raise PermissionError(f"Copilot for Xcode bundle {outcome}")
        return None

    def _find_user_marker(self, home: Path) -> Optional[Path]:
        """The first per-user path proving this profile ran the app, or None."""
        outcome = "absent"
        for relative in USER_MARKERS:
            marker = home / relative
            if not path_in_scope(marker, home):
                continue
            state = _marker_state(marker)
            if state == "present":
                record_copilot_xcode_probe("user_state", "present")
                return marker
            if state == "unreadable":
                outcome = "unreadable"
        record_copilot_xcode_probe("user_state", outcome)
        if outcome == "unreadable":
            raise PermissionError(f"Copilot for Xcode user state {outcome}")
        return None

    def detect(self) -> Optional[Dict]:
        home = self._scan_home()

        bundle = self._find_bundle(home)
        if bundle is None:
            return None
        if self._find_user_marker(home) is None:
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version(bundle),
            "install_path": str(bundle),
        }

    def get_version(self, bundle: Optional[Path] = None) -> Optional[str]:
        """CFBundleShortVersionString, or None — version must never block detection."""
        try:
            app_bundle = bundle or self._find_bundle(self._scan_home())
            if app_bundle is None:
                return None
            return read_bundle_version(app_bundle)
        except Exception as e:
            logger.debug(f"Could not extract Copilot for Xcode version: {e}")
            return None
