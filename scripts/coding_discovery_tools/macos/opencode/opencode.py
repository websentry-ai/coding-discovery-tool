"""
OpenCode detection for macOS.

OpenCode (opencode.ai, anomalyco/opencode) is an AI coding agent. This module
detects installations by checking, in order:
- the 'opencode' command on PATH ('which opencode')
- well-known per-user binary locations (curl installer, bun, ~/.local/bin, npm)
- the desktop app bundle (/Applications/OpenCode.app or ~/Applications/OpenCode.app)

Ambiguity note: the same binary name once belonged to the archived Go project
opencode-ai/opencode (config at ~/.opencode.json), which became Charm's "Crush".
We prefer-but-don't-require ~/.config/opencode/ or ~/.local/share/opencode/ as
corroboration that the hit is the opencode.ai tool; a hit is reported either way
and no confidence field is added.
"""

import logging
import os
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command
from ...macos_extraction_helpers import macos_app_candidates, read_bundle_version

logger = logging.getLogger(__name__)

# Per-user binary locations, relative to the home directory, checked after PATH.
# Order matters: the curl installer default first, then bun, then the generic
# XDG/npm locations.
_USER_RELATIVE_BINARIES = [
    Path(".opencode/bin/opencode"),
    Path(".bun/bin/opencode"),
    Path(".local/bin/opencode"),
    Path(".npm-global/bin/opencode"),
]

_APP_BUNDLE = Path("/Applications/OpenCode.app")

# Directories that corroborate the opencode.ai tool (vs. the archived Go project).
_CORROBORATING_DIRS = [
    Path(".config/opencode"),
    Path(".local/share/opencode"),
]


class MacOSOpenCodeDetector(BaseToolDetector):
    """
    Detector for OpenCode installations on macOS systems.

    Detection involves:
    - Checking if 'opencode' command is available using 'which opencode'
    - Falling back to per-user binary locations and the desktop app bundle
    - Verifying installation by running 'opencode --version' (binary hits) or
      reading CFBundleShortVersionString (bundle hits)
    """

    def __init__(self) -> None:
        # Set by the per-user dispatcher under a root/MDM scan; None means
        # "the current user".
        self.user_home: Optional[Path] = None
        # Resolved install location so detect() and get_version() agree.
        self._resolved_path: Optional[Path] = None
        self._resolved_is_bundle: bool = False
        self._resolved_from_fallback: bool = False

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "OpenCode"

    def detect(self) -> Optional[Dict]:
        """
        Detect OpenCode installation on macOS.

        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        install_path = self._check_opencode_command()
        if not install_path:
            return None

        version = self.get_version()

        if not self._has_corroborating_dirs():
            logger.debug(
                "OpenCode binary found without ~/.config/opencode or "
                "~/.local/share/opencode; may be the archived Go 'opencode'"
            )

        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": install_path
        }

    def get_version(self) -> Optional[str]:
        """
        Extract OpenCode version.

        Bundle hits read CFBundleShortVersionString via ``read_bundle_version``
        (O_NOFOLLOW + regular-file check); binary hits run 'opencode --version'.

        Returns:
            Version string or None if version cannot be determined
        """
        try:
            if self._resolved_is_bundle and self._resolved_path is not None:
                return read_bundle_version(self._resolved_path)

            # A fallback hit is not on PATH, so probe the resolved binary itself.
            # utils.run_command refuses absolute argv[0] under a user-writable
            # prefix when scanning as root; that yields None -> "Unknown".
            argv0 = "opencode"
            if self._resolved_from_fallback and self._resolved_path is not None:
                argv0 = str(self._resolved_path)
            output = run_command(
                [argv0, "--version"],
                VERSION_TIMEOUT
            )
            if output:
                # Version output might be just a number or include text
                # Clean up the output to extract version
                version = output.strip()
                return version or None
        except Exception as e:
            logger.debug(f"Could not extract OpenCode version: {e}")
        return None

    def _home(self) -> Path:
        return getattr(self, "user_home", None) or Path.home()

    def _has_corroborating_dirs(self) -> bool:
        home = self._home()
        for rel in _CORROBORATING_DIRS:
            try:
                if (home / rel).is_dir():
                    return True
            except OSError:
                continue
        return False

    def _check_opencode_command(self) -> Optional[str]:
        """
        Locate the OpenCode install.

        PATH lookup wins (no regression for existing installs); otherwise the
        per-user binary locations, then the desktop app bundle.

        Returns:
            Path to opencode executable or app bundle if found, None otherwise
        """
        self._resolved_path = None
        self._resolved_is_bundle = False
        self._resolved_from_fallback = False

        try:
            output = run_command(
                ["which", "opencode"],
                VERSION_TIMEOUT
            )
            if output:
                path = output.strip()
                # Verify the path exists
                if Path(path).exists():
                    logger.debug(f"Found OpenCode at: {path}")
                    self._resolved_path = Path(path)
                    return path
        except Exception as e:
            logger.debug(f"Could not check for OpenCode command: {e}")

        return self._check_user_install()

    def _check_user_install(self) -> Optional[str]:
        """Per-user binaries, then the app bundle — never the scanner's PATH."""
        home = self._home()
        for rel in _USER_RELATIVE_BINARIES:
            candidate = home / rel
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    logger.debug(f"Found OpenCode at: {candidate}")
                    self._resolved_path = candidate
                    self._resolved_from_fallback = True
                    return str(candidate)
            except OSError:
                continue

        for bundle in self._bundle_candidates(home):
            try:
                if bundle.exists():
                    logger.debug(f"Found OpenCode app bundle at: {bundle}")
                    self._resolved_path = bundle
                    self._resolved_is_bundle = True
                    return str(bundle)
            except (PermissionError, OSError):
                continue

        return None

    def detect_user_install(self) -> Optional[Dict]:
        """Detect without consulting PATH — for root/MDM per-user scans, where
        ``which`` would resolve the scanner's PATH, not the scanned user's."""
        self._resolved_path = None
        self._resolved_is_bundle = False
        self._resolved_from_fallback = False
        install_path = self._check_user_install()
        if not install_path:
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version() or "Unknown",
            "install_path": install_path,
        }

    def _bundle_candidates(self, home: Path) -> List[Path]:
        """Machine-wide bundle, then the scanned user's ~/Applications sibling."""
        return macos_app_candidates(_APP_BUNDLE, home)
