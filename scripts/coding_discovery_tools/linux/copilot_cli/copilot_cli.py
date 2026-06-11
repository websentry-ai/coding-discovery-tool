"""
GitHub Copilot CLI detection for Linux.

The GitHub Copilot CLI (``@github/copilot``) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin. It
keeps its configuration under ``~/.copilot/`` (with MCP servers in
``~/.copilot/mcp-config.json``), identical to the macOS layout.

Inherits the binary-gated detection from ``MacOSCopilotCliDetector`` (DRY) and
overrides only the all-users scan (``get_linux_user_homes()``) and the binary
resolve (``_resolve_binary``: Linuxbrew instead of macOS Homebrew prefixes).
"""

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

from ...linux_extraction_helpers import get_linux_user_homes, is_running_as_root
from ...macos.copilot_cli.copilot_cli import (
    MacOSCopilotCliDetector,
    _resolve_copilot_binary,
)
from ...utils import (
    machine_global_binary_owned_by_user,
    resolve_npm_global_tool_bin,
)

logger = logging.getLogger(__name__)


class LinuxCopilotCliDetector(MacOSCopilotCliDetector):
    """
    Detector for GitHub Copilot CLI installations on Linux systems.

    Inherits the binary-gated per-user detection from the macOS detector and
    overrides only the all-users scan (``get_linux_user_homes()``) and the binary
    resolve (no Homebrew).
    """

    def _resolve_binary(self, user_home: Path) -> Optional[str]:
        """Resolve the ``copilot`` CLI binary for ``user_home`` on Linux.

        Per-user installs, then the npm-global prefix, then machine-global
        ``/usr/local/bin`` and Linuxbrew (macOS Homebrew prefixes don't apply). The
        machine-global candidates are owner-attributed under a root/MDM scan so one
        user's install isn't fanned out to every user.
        Best-effort: returns a path string or None. Never raises.
        """
        per_user = _resolve_copilot_binary(user_home)
        if per_user is not None:
            return str(per_user)

        npm_resolved = resolve_npm_global_tool_bin(
            "copilot", user_home, is_running_as_root()
        )
        if npm_resolved:
            return npm_resolved

        # The user_home-relative Linuxbrew prefix is always probed; the
        # machine-global prefixes are owner-attributed under root.
        user_relative = [user_home / ".linuxbrew" / "bin" / "copilot"]
        machine_global = [
            Path("/usr/local/bin/copilot"),
            Path("/home/linuxbrew/.linuxbrew/bin/copilot"),
        ]
        is_root = is_running_as_root()
        for candidate in user_relative + machine_global:
            try:
                if candidate.exists() and os.access(str(candidate), os.X_OK):
                    if is_root and candidate in machine_global \
                            and not machine_global_binary_owned_by_user(candidate, user_home):
                        continue
                    return str(candidate)
            except (PermissionError, OSError):
                continue

        return None

    def _detect_all_users(self) -> List[Dict]:
        """
        Detect the Copilot CLI for the relevant set of users on Linux.

        - If ``self.user_home`` is set, check only that user (the live per-user
          discovery path).
        - Else scan every human user home via ``get_linux_user_homes()`` (which
          already includes ``/root`` when running as root).
        """
        if self.user_home is not None:
            result = self._detect_for_user(self.user_home)
            return [result] if result else []

        results: List[Dict] = []
        for user_home in get_linux_user_homes():
            try:
                result = self._detect_for_user(user_home)
                if result:
                    results.append(result)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping user directory {user_home}: {exc}")
                continue
        return results
