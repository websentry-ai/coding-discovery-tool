"""
GitHub Copilot CLI detection for Linux.

Subclasses the macOS detector, overriding only the all-users branch: Linux
uses ``get_linux_user_homes()`` (``/root`` + ``/home/*``) instead of the macOS
``/Users`` scan. Everything else — the marker gate, per-user detection,
``detect``/``detect_all_tools``, and ``get_version`` — is inherited unchanged.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...constants import VERSION_TIMEOUT
from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector, _parse_cli_version
from ...utils import run_command

logger = logging.getLogger(__name__)

# Paths where `gh copilot` typically installs the Copilot CLI binary on Linux.
# The gh extension manager drops the binary under ~/.local/share/gh/copilot/;
# npm global installs land under ~/.local/bin/ or ~/.npm-global/bin/.
_USER_RELATIVE_BINARY_PATHS = [
    Path(".local/share/gh/copilot/copilot"),
    Path(".local/bin/copilot"),
    Path(".npm-global/bin/copilot"),
    Path("bin/copilot"),
]


class LinuxCopilotCliDetector(MacOSCopilotCliDetector):
    """
    Detector for GitHub Copilot CLI installations on Linux systems.

    Inherits the full macOS detection surface and overrides:
    - ``_detect_all_users``: uses ``get_linux_user_homes()`` (/root + /home/*)
    - ``get_version``: probes common Linux install paths before falling back
      to PATH, since ``gh copilot`` installs to ``~/.local/share/gh/copilot/``
      by default (not on PATH when running as root scanning another user).
    """

    def get_version(self) -> Optional[str]:
        """
        Extract Copilot CLI version on Linux.

        Tries ``copilot --version`` on PATH first, then walks common per-user
        install dirs (``~/.local/share/gh/copilot/``, ``~/.local/bin/``, etc.)
        across all user homes until a working binary is found.
        """
        try:
            output = run_command(["copilot", "--version"], VERSION_TIMEOUT)
            version = _parse_cli_version(output)
            if version:
                return version
        except Exception:
            pass

        for user_home in get_linux_user_homes():
            for rel in _USER_RELATIVE_BINARY_PATHS:
                binary = user_home / rel
                try:
                    if not binary.is_file():
                        continue
                    output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                    version = _parse_cli_version(output)
                    if version:
                        return version
                except Exception:
                    continue
        return None

    def _detect_all_users(self) -> List[Dict]:
        """
        Detect the Copilot CLI for the relevant set of users on Linux.

        - If ``self.user_home`` is set, check only that user (live per-user
          discovery path).
        - Otherwise scan every home returned by ``get_linux_user_homes()``
          (includes ``/root`` and all ``/home/*`` dirs).
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
        return results
