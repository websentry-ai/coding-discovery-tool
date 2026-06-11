"""
GitHub Copilot CLI detection for Linux.

The GitHub Copilot CLI (``@github/copilot``) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin. It
keeps its configuration under ``~/.copilot/`` (with MCP servers in
``~/.copilot/mcp-config.json``), identical to the macOS layout.

DRY (CLAUDE.md): the binary GATE in ``_detect_for_user``, ``detect``,
``detect_all_tools``, ``get_version``, and the marker machinery are all inherited
from ``MacOSCopilotCliDetector``. Only two things are Linux-specific and
overridden here: the all-users scan (``get_linux_user_homes()`` instead of root +
``/Users``) and the binary resolve (``_resolve_binary``: npm/nvm/pnpm via the
shared resolver plus ``/usr/local/bin/copilot``; NO Homebrew on Linux).
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
from ...utils import resolve_npm_global_tool_bin

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

        Order: per-user installs (``~/.local/bin``, ``~/.bun/bin``, newest nvm
        node) via ``_resolve_copilot_binary``; the npm-global prefix (nvm / pnpm /
        system node) via the shared resolver; and the machine-global
        ``/usr/local/bin/copilot`` (root-owned system installs attribute to every
        scanned user). NO Homebrew — that is a macOS path. Best-effort: returns a
        path string or None. Never raises.
        """
        per_user = _resolve_copilot_binary(user_home)
        if per_user is not None:
            return str(per_user)

        npm_resolved = resolve_npm_global_tool_bin(
            "copilot", user_home, is_running_as_root()
        )
        if npm_resolved:
            return npm_resolved

        candidate = Path("/usr/local/bin/copilot")
        try:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)
        except (PermissionError, OSError):
            pass

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
