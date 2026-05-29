"""
GitHub Copilot CLI detection for Windows.

The GitHub Copilot CLI (``@github/copilot``) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin. It
keeps its configuration under ``%USERPROFILE%\\.copilot`` (i.e. ``~/.copilot``),
identical to the macOS layout, with its MCP servers in
``~/.copilot/mcp-config.json``.

Two things are OS-specific: the all-users scan (Windows uses
``is_running_as_admin`` and iterates ``C:\\Users`` instead of root + ``/Users``)
and ``get_version`` (overridden to pass ``shell=True`` for the npm ``.cmd``
shim, mirroring ``WindowsCodexDetector`` — without it the inherited probe would
always read "unknown"). Everything else — the marker gate
(``_copilot_dir_has_known_artifact``), ``_detect_for_user``, ``detect``, and
``detect_all_tools`` — is inherited from the macOS detector rather than
re-derived (CLAUDE.md DRY). Mirrors the per-user/admin idiom in
``windows/github_copilot/detect_copilot.py``.
"""

import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from ...constants import VERSION_TIMEOUT
from ...windows_extraction_helpers import is_running_as_admin
from ...macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector

logger = logging.getLogger(__name__)


class WindowsCopilotCliDetector(MacOSCopilotCliDetector):
    """
    Detector for GitHub Copilot CLI installations on Windows systems.

    Inherits the full macOS detection surface (marker gate, per-user detection,
    ``detect``/``detect_all_tools``, and ``get_version``) and overrides only the
    all-users branch: when ``self.user_home`` is unset and the process is admin,
    every user under ``C:\\Users`` is scanned; otherwise the current user's home
    is checked. Each detected user yields a distinct row whose ``install_path``
    is that user's ``~/.copilot`` directory.
    """

    def _detect_all_users(self) -> List[Dict]:
        """
        Detect the Copilot CLI for the relevant set of users on Windows.

        - If ``self.user_home`` is set, check only that user (the live per-user
          discovery path).
        - Else if running as admin, scan every directory under ``C:\\Users``.
        - Else check the current user's home directory.
        """
        if self.user_home is not None:
            result = self._detect_for_user(self.user_home)
            return [result] if result else []

        if is_running_as_admin():
            return self._detect_for_all_system_users()

        result = self._detect_for_user(Path.home())
        return [result] if result else []

    def _detect_for_all_system_users(self) -> List[Dict]:
        """Scan every user directory under ``C:\\Users`` when running as admin.

        Fallback path: the live MDM discovery loop scopes detection per-user via
        ``detect_tool_for_user`` (which sets ``self.user_home``), so this admin
        all-users branch only fires for a direct ``detect()`` call with no
        ``user_home`` set. Kept for parity with ``WindowsGitHubCopilotDetector``
        and the standalone entry point.
        """
        results: List[Dict] = []
        users_dir = Path("C:\\Users")
        try:
            if not users_dir.exists():
                return results
            for user_dir in users_dir.iterdir():
                if not user_dir.is_dir() or user_dir.name.startswith('.'):
                    continue
                try:
                    result = self._detect_for_user(user_dir)
                    if result:
                        results.append(result)
                except (PermissionError, OSError) as exc:
                    logger.debug(f"Skipping user directory {user_dir}: {exc}")
                    continue
        except (PermissionError, OSError) as exc:
            logger.debug(f"Error scanning C:\\Users for Copilot CLI: {exc}")
        return results

    def get_version(self) -> Optional[str]:
        """
        Extract Copilot CLI version on Windows using ``copilot --version``.

        Overrides the inherited macOS probe to pass ``shell=True``: npm installs
        the CLI as a ``copilot.cmd`` shim, which Windows cannot exec from a bare
        argv list, so the inherited ``run_command`` probe would raise and version
        would always read "unknown". Mirrors ``WindowsCodexDetector``.
        Best-effort: returns None on any failure and the caller falls back to
        "unknown".
        """
        try:
            result = subprocess.run(
                ["copilot", "--version"],
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
                shell=True,  # Required for npm .CMD shims on Windows
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                return output or None
        except Exception as exc:
            logger.debug(f"Could not extract Copilot CLI version on Windows: {exc}")
        return None
