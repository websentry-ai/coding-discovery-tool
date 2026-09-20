"""
Claude Desktop detection for macOS.

Claude Desktop is the claude.ai client. Cowork and the Claude Code it hosts are
reported as their own tools, so this row is the desktop app itself. A device
counts as having it when BOTH:

    - A Claude.app bundle is discoverable on disk, AND
    - The per-user data directory exists at
      ~/Library/Application Support/Claude/

Anthropic documents claude_desktop_config.json as living in that directory, and
it is created on first run, so a bundle with no data directory is an install
that was never opened and there is nothing to report on.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import dir_state, fail_if_anomalous
from ..claude_bundle import (
    claude_app_support_dir,
    claude_bundle_version,
    resolve_claude_bundle,
)

logger = logging.getLogger(__name__)


class MacOSClaudeDesktopDetector(BaseToolDetector):
    """Claude Desktop detector for macOS."""

    @property
    def tool_name(self) -> str:
        return "Claude Desktop"

    def _scan_home(self, user_home: Optional[Path] = None) -> Path:
        """Home of the user being scanned — not the scanner's, under a root/MDM run."""
        return Path(user_home or getattr(self, "user_home", None) or Path.home())

    def _find_install_dir(self, user_home: Optional[Path] = None) -> Optional[Path]:
        return resolve_claude_bundle(self._scan_home(user_home))

    def detect(self) -> Optional[Dict]:
        home = self._scan_home()
        data_dir = claude_app_support_dir(home)

        state = dir_state(data_dir)
        if state == "unreadable":
            # Raising marks the scan incomplete device-wide, which is right for our
            # own home but wrong for a 0700 sibling an unprivileged scan was never
            # allowed to read — that denial is the norm, not a detector failure.
            fail_if_anomalous(home, f"Claude Desktop data dir unreadable: {data_dir}")
            return None
        if state != "present":
            logger.debug("No Claude Desktop data dir at %s", data_dir)
            return None

        try:
            app_install = self._find_install_dir()
        except OSError as e:
            fail_if_anomalous(home, str(e))
            return None
        if app_install is None:
            logger.debug("Claude data dir %s has no app bundle; residue, not an install", data_dir)
            return None

        return {
            "name": self.tool_name,
            "version": claude_bundle_version(app_install),
            "install_path": str(data_dir),
        }

    def get_version(self, app_install: Optional[Path] = None) -> Optional[str]:
        """CFBundleShortVersionString from Claude.app, or None on any error."""
        app_bundle = app_install or self._find_install_dir()
        return claude_bundle_version(app_bundle) if app_bundle else None
