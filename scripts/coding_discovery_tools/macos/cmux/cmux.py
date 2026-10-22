"""
cmux detection for macOS
"""

import logging
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...macos_extraction_helpers import macos_app_candidates
from ...utils import run_command

logger = logging.getLogger(__name__)


class MacOSCmuxDetector(BaseToolDetector):
    """cmux detector for macOS systems.

    cmux manages other agents rather than being one: it runs each agent in a
    pane behind a wrapper of its own, and ``cmux hooks setup`` writes cmux's
    hooks into Claude Code, Codex, OpenCode and Gemini. So a device running it
    has a second layer in front of the agents, which is worth reporting whether
    or not any agent is found alongside.

    Ships only as a macOS app, so there is no Windows or Linux detector. The
    names cmux and cmux-tui also belong to unrelated projects; this one is the
    app, identified by its bundle.
    """

    POSSIBLE_APP_PATHS = [
        Path("/Applications/cmux.app"),
        # Nightly carries its own bundle id so it can sit beside stable.
        Path("/Applications/cmux Nightly.app"),
    ]

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "cmux"

    def detect(self) -> Optional[Dict]:
        """Detect a cmux installation on macOS.

        Gated purely on the app bundle, which uninstall removes. Its other
        traces are not installation evidence and would report a tool that is
        gone: ``~/.config/cmux/cmux.json`` and
        ``~/Library/Application Support/cmux`` outlive the app, ``~/.cmuxterm``
        is shared across installs, and the per-pane wrappers under ``$TMPDIR``
        belong to whichever pane wrote them.

        Returns:
            Dict with tool info or None if not found
        """
        app_path = self._find_app_path()
        if app_path:
            return {
                "name": self.tool_name,
                "version": self.get_version(app_path),
                "install_path": str(app_path),
            }

        return None

    def _find_app_path(self) -> Optional[Path]:
        """The cmux bundle, preferring stable over nightly, else None."""
        user_home = getattr(self, 'user_home', None)
        for app_path in self.POSSIBLE_APP_PATHS:
            for candidate in macos_app_candidates(app_path, user_home):
                if candidate.exists():
                    return candidate
        return None

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """Extract the cmux version from the bundle's Info.plist.

        Args:
            app_path: Optional path to the app (if None, will try to find it)

        Returns:
            Version string or None
        """
        if app_path is None:
            app_path = self._find_app_path()

        if app_path is None:
            return None

        try:
            plist_path = app_path / "Contents" / "Info.plist"
            if not plist_path.exists():
                return None

            output = run_command(
                ["defaults", "read", str(plist_path), "CFBundleShortVersionString"],
                VERSION_TIMEOUT
            )
            return output if output else None
        except Exception as e:
            logger.warning(f"Could not extract cmux version: {e}")
        return None
