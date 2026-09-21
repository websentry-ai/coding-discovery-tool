"""
GitHub Copilot app detection for Windows.

The GitHub Copilot app is the agent-native desktop client, distinct from the
Copilot CLI and from the VS Code extension. It ships as an NSIS installer, so it
lands in a dedicated directory machine-wide or under the user's Programs dir.

The directory alone is not the signal: an interrupted uninstall can leave it
behind, so a live install must also hold a binary. The executable name is not
documented, so the directory is listed but nothing inside it is run.
A self-updating installer keeps that binary in a versioned subdirectory, so the
search descends a few levels.
"""

import logging
import os
from pathlib import Path
from typing import Dict, Optional

from ...coding_tool_base import BaseToolDetector
from ...utils import dir_state, record_copilot_app_probe, windows_program_files_roots

logger = logging.getLogger(__name__)

INSTALL_DIR_NAME = "GitHubCopilot"
USER_INSTALL_DIR = Path("AppData") / "Local" / "Programs" / "GitHub Copilot"

_MAX_DEPTH = 3
# Run-away guard only: the largest real install tree measured holds ~12k entries.
_MAX_ENTRIES = 50000


def _find_exe(root: Path) -> str:
    """``present``, ``no_exe``, ``truncated`` or ``unreadable``. Never raises."""
    seen = 0
    stack = [(root, 0)]
    while stack:
        current, depth = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    seen += 1
                    if seen > _MAX_ENTRIES:
                        return "truncated"
                    if entry.name.lower().endswith(".exe") and entry.is_file():
                        return "present"
                    if entry.is_dir(follow_symlinks=False) and depth < _MAX_DEPTH:
                        stack.append((Path(entry.path), depth + 1))
        except (PermissionError, OSError) as exc:
            logger.debug(f"Could not list {current}: {exc}")
            return "unreadable"
    return "no_exe"


def _install_state(install_dir: Path) -> str:
    """``present`` only when the directory holds a binary; see ``_find_exe``."""
    state = dir_state(install_dir)
    if state == "absent":
        return "missing"
    if state != "present":
        return state
    return _find_exe(install_dir)


class WindowsGitHubCopilotAppDetector(BaseToolDetector):
    """GitHub Copilot app detector for Windows."""

    @property
    def tool_name(self) -> str:
        return "GitHub Copilot App"

    def _install_dirs(self, user_home: Path):
        for root in windows_program_files_roots():
            yield root / INSTALL_DIR_NAME
        yield user_home / USER_INSTALL_DIR

    def detect(self) -> Optional[Dict]:
        """Raises when a candidate could not be resolved — denied or out of budget:
        a clean absence lets the backend prune a live install (incident 326)."""
        user_home = Path(getattr(self, "user_home", None) or Path.home())
        unresolved = None
        for install_dir in self._install_dirs(user_home):
            state = _install_state(install_dir)
            record_copilot_app_probe(install_dir.name, state)
            if state == "present":
                return {
                    "name": self.tool_name,
                    "version": None,
                    "install_path": str(install_dir),
                }
            if state in ("unreadable", "truncated"):
                unresolved = state
        if unresolved:
            # A denial is routine on a multi-user box; exhausting the budget is not,
            # and only a non-PermissionError reaches Sentry.
            error = PermissionError if unresolved == "unreadable" else RuntimeError
            raise error(f"GitHub Copilot app install dir {unresolved}")
        return None

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """The installer records no version we can read without running the app."""
        return None
