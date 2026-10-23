"""
cmux detection for macOS
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...macos_extraction_helpers import (
    macos_app_candidates,
    read_bundle_identifier,
    read_bundle_version,
)

logger = logging.getLogger(__name__)

# Every channel ships under this identifier or a suffix of it: stable is
# ``com.cmuxterm.app``, nightly ``.nightly``, next ``.nightly.cmux-next``. The
# name alone is not enough to identify the product -- unrelated projects are
# also called cmux and would install a bundle of the same name.
CMUX_BUNDLE_ID = "com.cmuxterm.app"


class MacOSCmuxDetector(BaseToolDetector):
    """cmux detector for macOS systems.

    cmux manages other agents rather than being one: it runs each agent in a
    pane behind a wrapper of its own, and ``cmux hooks setup`` writes cmux's
    hooks into Claude Code, Codex, OpenCode and Gemini. So a device running it
    has a second layer in front of the agents, which is worth reporting whether
    or not any agent is found alongside.

    macOS only, because that is the only platform cmux ships on.
    """

    # Ordered: the first channel that exists wins, so a stable install is
    # reported in preference to a pre-release sitting beside it. Exposed as a
    # class attribute so the order is visible to (and patchable by) tests.
    APP_PATHS = [
        Path("/Applications/cmux.app"),
        Path("/Applications/cmux NEXT.app"),
        Path("/Applications/cmux NIGHTLY.app"),
        # Upstream is folding release candidates into the stable identity, but
        # already-installed RC builds still carry their own bundle.
        Path("/Applications/cmux RC.app"),
    ]

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

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
            Dict with name/version/install_path, or None when no bundle exists.
        """
        app_path = self._resolve_app_path()
        if app_path is None:
            return None
        return {
            "name": self.tool_name,
            "version": self.get_version(app_path) or "Unknown",
            "install_path": str(app_path),
        }

    def _candidates(self) -> List[Path]:
        """Each channel's bundle, machine-wide then under the scanned user."""
        found = []
        for app_path in self.APP_PATHS:
            found.extend(macos_app_candidates(app_path, self.user_home))
        return found

    def _resolve_app_path(self) -> Optional[Path]:
        """The first cmux bundle that exists, or None."""
        for candidate in self._candidates():
            try:
                if not candidate.exists():
                    continue
                identifier = read_bundle_identifier(candidate) or ""
            except (OSError, ValueError) as exc:
                logger.debug(f"Could not inspect {candidate}: {exc}")
                continue
            if identifier == CMUX_BUNDLE_ID or identifier.startswith(
                    CMUX_BUNDLE_ID + "."):
                return candidate
            logger.debug(
                f"{candidate} is not cmux (bundle id {identifier!r}); ignoring")
        return None

    def get_version(self, app_path: Optional[Path] = None) -> Optional[str]:
        """Read ``CFBundleShortVersionString`` from the bundle's Info.plist.

        Uses ``read_bundle_version`` (O_NOFOLLOW + regular-file check) rather
        than ``defaults read``: the bundle can live under a user-writable home
        during a root scan, where a symlinked plist pointing at a FIFO would
        otherwise block the whole scan.
        """
        try:
            if app_path is None:
                app_path = self._resolve_app_path()
            if app_path is None:
                return None
            return read_bundle_version(app_path)
        except Exception as exc:
            logger.debug(f"Could not extract cmux version: {exc}")
        return None
