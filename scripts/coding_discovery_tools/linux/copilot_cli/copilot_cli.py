"""
GitHub Copilot CLI detection for Linux.

Subclasses the macOS detector, overriding:
- ``_detect_all_users``: uses ``get_linux_user_homes()`` (/root + /home/*)
- ``get_version``:  probes the detected user's home first, then falls back
  to PATH, since ``gh copilot`` installs to ``~/.local/share/gh/copilot/``
  by default and is typically not on PATH during a root scan.
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional

from ...constants import VERSION_TIMEOUT
from ...linux_extraction_helpers import get_linux_user_homes
from ...macos.copilot_cli.copilot_cli import (
    MacOSCopilotCliDetector,
    _parse_cli_version,
    _resolve_copilot_binary,
)
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

    def _candidate_binaries(self, user_home: Path) -> List[Path]:
        """Per-user ``copilot`` binaries to probe, in priority order: the shared
        base locations (``.local/bin``, ``.bun/bin``, newest-nvm — resolved by
        ``_resolve_copilot_binary``, the same set the macOS detector probes) plus
        the Linux-specific install dirs (gh extension, ``.npm-global``). Probing
        the shared resolver keeps Linux and macOS covering the same core paths.
        """
        candidates: List[Path] = []
        base = _resolve_copilot_binary(user_home)
        if base is not None:
            candidates.append(base)
        candidates.extend(user_home / rel for rel in _USER_RELATIVE_BINARY_PATHS)
        return candidates

    def get_version(self) -> Optional[str]:
        """
        Extract Copilot CLI version on Linux.

        Probes the detected user's home (``self.user_home``) first, then tries
        ``copilot --version`` on PATH, then walks remaining user homes. This
        ensures each per-user row gets its own user's binary version rather than
        whichever home is iterated first — and avoids N full cross-home probes
        on a multi-user scan.
        """
        # 1. Try the detected user's own home first.
        homes_to_probe: List[Path] = []
        if self.user_home is not None:
            homes_to_probe.append(self.user_home)

        for user_home in homes_to_probe:
            for binary in self._candidate_binaries(user_home):
                try:
                    if not binary.is_file():
                        continue
                    output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                    version = _parse_cli_version(output)
                    if version:
                        return version
                except Exception as exc:
                    logger.debug(f"Copilot CLI version probe failed for {binary}: {exc}")
                    continue

        # 2. Fall back to PATH.
        try:
            output = run_command(["copilot", "--version"], VERSION_TIMEOUT)
            version = _parse_cli_version(output)
            if version:
                return version
        except Exception as exc:
            logger.debug(f"Copilot CLI version probe via PATH failed: {exc}")

        # 3. Last resort: scan all user homes.
        # Only reached when user_home is unset (no specific user scoped).
        # When user_home IS set, return None rather than attributing another
        # user's binary version to this user's detection row.
        if self.user_home is not None:
            return None

        for user_home in get_linux_user_homes():
            for binary in self._candidate_binaries(user_home):
                try:
                    if not binary.is_file():
                        continue
                    output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
                    version = _parse_cli_version(output)
                    if version:
                        return version
                except Exception as exc:
                    logger.debug(f"Copilot CLI version probe failed for {binary}: {exc}")
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
            # Scope ``self.user_home`` to the user being detected so the
            # ``get_version()`` call inside ``_detect_for_user`` probes *this*
            # user's binary (step 1) and returns None rather than another user's
            # version (step 3 guard) if absent. Without this, every row would
            # inherit whichever home is iterated first.
            try:
                self.user_home = user_home
                result = self._detect_for_user(user_home)
                if result:
                    results.append(result)
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping user directory {user_home}: {exc}")
            finally:
                self.user_home = None
        return results
