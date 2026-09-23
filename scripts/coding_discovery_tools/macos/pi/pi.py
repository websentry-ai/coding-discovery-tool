"""
pi coding agent detection for macOS.

The pi coding agent (https://pi.inc) ships a terminal agent whose per-user state
lives under ``~/.pi/agent/`` and whose launcher is a binary literally named
``pi``. That name is shared with plenty of unrelated software (Raspberry Pi
helpers, pretty-printers, personal shell scripts), so resolving a ``pi``
executable is NOT on its own evidence of an install. Every detection is gated on
a corroborating agent artefact — see ``_is_pi_coding_agent``.

Binary resolution mirrors ``macos/augment/augment.py``: the agent installs to a
per-user prefix that root's PATH does not include during an MDM all-users scan,
so the documented locations are probed explicitly and PATH is consulted only
when scanning the scanning user's own home.
"""

import logging
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import (
    extract_version_number,
    machine_global_binary_owned_by_user,
    run_command,
    _is_scanning_users_own_home,
    _which_no_cwd,
)
from ...macos_extraction_helpers import is_running_as_root

logger = logging.getLogger(__name__)

# Documented override for the agent state directory.
_PI_AGENT_DIR_ENV = "PI_CODING_AGENT_DIR"

# Substring of the npm/standalone package directory the launcher resolves into.
_PI_PACKAGE_MARKER = "pi-coding-agent"

# Per-user prefixes (relative to a user home) the launcher installs into, in
# resolution order. The agent's own ``bin`` comes first so a managed install
# always wins over a stray same-named binary elsewhere on the user's prefixes.
_USER_RELATIVE_BIN_PATHS = (
    Path(".local") / "bin" / "pi",
    Path("bin") / "pi",
    Path(".bin") / "pi",
    Path("local") / "bin" / "pi",
    Path(".bun") / "bin" / "pi",
)

# Machine-global locations (Homebrew, npm -g under /usr/local). Under a root
# scan these are attributed per ``machine_global_binary_owned_by_user`` so one
# user's Homebrew install is not fanned out to every account. Class-level list
# so tests can isolate from the CI box.
_MACHINE_GLOBAL_BIN_PATHS = (
    Path("/opt/homebrew/bin/pi"),
    Path("/usr/local/bin/pi"),
)


def _node_version_key(version_dir: Path):
    """Sort key for ``~/.nvm/versions/node/<v>`` dirs (newest first when reversed)."""
    nums = re.findall(r"\d+", version_dir.name)
    return tuple(int(n) for n in nums)


class MacOSPiDetector(BaseToolDetector):
    """
    Detector for the pi coding agent on macOS.

    When ``self.user_home`` is set (the live per-user discovery path), detection
    is scoped to that single user; otherwise the current user's home is checked.
    Root/all-users enumeration is the caller's job (``detect_tool_for_user``).
    """

    MACHINE_GLOBAL_BIN_PATHS = list(_MACHINE_GLOBAL_BIN_PATHS)

    def __init__(self) -> None:
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected.

        MUST stay ``"Pi Coding Agent"``. The bare string ``"pi"`` must never be
        used as a tool name: the frontend matches tool names with a ``\\bpi\\b``
        word-boundary regex, so a two-letter row would collide with unrelated
        copy ("pi", "Raspberry Pi", ...).
        """
        return "Pi Coding Agent"

    def detect(self) -> Optional[Dict]:
        """
        Detect the pi coding agent.

        Returns:
            Dict with name/version/install_path, or None when no corroborated
            install is found.
        """
        for user_home in self._iter_scan_homes():
            try:
                binary = self._resolve_pi_binary(user_home)
                if binary is None:
                    continue
                if not self._is_pi_coding_agent(user_home, binary):
                    logger.debug(
                        f"Binary named pi at {binary} is not the pi coding agent "
                        f"(no agent dir, managed-install marker, or package path)"
                    )
                    continue
                return {
                    "name": self.tool_name,
                    "version": self.get_version(binary) or "Unknown",
                    "install_path": str(binary),
                }
            except (PermissionError, OSError) as exc:
                logger.debug(f"Skipping pi detection for {user_home}: {exc}")
        return None

    def get_version(self, binary: Optional[Path] = None) -> Optional[str]:
        """Extract the agent version via ``pi --version`` (best-effort).

        ``run_command`` already refuses to execute an absolute argv[0] under a
        user-writable prefix when running as root, so no second gate is added
        here (see ``tests/test_version_probe_exec_gate.py``).
        """
        try:
            if binary is None:
                for user_home in self._iter_scan_homes():
                    binary = self._resolve_pi_binary(user_home)
                    if binary is not None:
                        break
            if binary is None:
                return None
            output = run_command([str(binary), "--version"], VERSION_TIMEOUT)
            if not output:
                return None
            return extract_version_number(output) or output.strip() or None
        except Exception as exc:
            logger.debug(f"Could not extract pi coding agent version: {exc}")
        return None

    # -- internals ----------------------------------------------------------

    def _iter_scan_homes(self) -> List[Path]:
        """User homes to scan: the scoped one, else the current user's home."""
        if self.user_home is not None:
            return [self.user_home]
        return [Path.home()]

    def _agent_dir(self, user_home: Path) -> Path:
        """The agent state dir for ``user_home`` (``~/.pi/agent`` by default)."""
        override = os.environ.get(_PI_AGENT_DIR_ENV)
        if override:
            return Path(override)
        return user_home / ".pi" / "agent"

    def _resolve_pi_binary(self, user_home: Path) -> Optional[Path]:
        """Return the ``pi`` launcher for ``user_home``, if any. Never raises."""
        try:
            candidates: List[Path] = [self._agent_dir(user_home) / "bin" / "pi"]
            candidates.extend(user_home / rel for rel in _USER_RELATIVE_BIN_PATHS)
            for candidate in candidates:
                try:
                    if candidate.exists() and os.access(str(candidate), os.X_OK):
                        return candidate
                except OSError:
                    continue

            nvm_node_dir = user_home / ".nvm" / "versions" / "node"
            try:
                version_dirs = sorted(
                    nvm_node_dir.glob("*"), key=_node_version_key, reverse=True
                )
            except OSError:
                version_dirs = []
            for version_dir in version_dirs:
                try:
                    candidate = version_dir / "bin" / "pi"
                    if candidate.exists() and os.access(str(candidate), os.X_OK):
                        return candidate
                except OSError:
                    continue

            # Machine-global installs (Homebrew / npm -g). Under root, only when
            # the binary is attributable to this user (its owner, or root-owned).
            is_root = is_running_as_root()
            for candidate in self.MACHINE_GLOBAL_BIN_PATHS:
                try:
                    if not (candidate.exists() and os.access(str(candidate), os.X_OK)):
                        continue
                    if is_root and not machine_global_binary_owned_by_user(candidate, user_home):
                        continue
                    return candidate
                except OSError:
                    continue

            # PATH fallback (other system installs), only for the scanning
            # user's own home so root's PATH is never attributed to another user.
            try:
                if _is_scanning_users_own_home(user_home):
                    found = _which_no_cwd("pi")
                    if found:
                        return Path(found)
            except (OSError, RuntimeError):
                pass
        except (PermissionError, OSError) as exc:
            logger.debug(f"Error resolving pi binary for {user_home}: {exc}")
        return None

    def _is_pi_coding_agent(self, user_home: Path, binary: Path) -> bool:
        """The collision guard for the shared ``pi`` binary name.

        A binary called ``pi`` is only the pi coding agent when something
        corroborates it:
          - the agent state dir ``~/.pi/agent`` exists, or
          - the managed-install marker ``~/.pi/agent/install/managed-install.json``
            exists (MDM/managed rollouts), or
          - the binary resolves into a ``pi-coding-agent`` package directory.
        Without one of these, an unrelated ``pi`` on the user's prefixes would be
        reported as an AI coding agent.
        """
        agent_dir = self._agent_dir(user_home)
        try:
            if agent_dir.is_dir():
                return True
        except OSError:
            pass
        try:
            if (agent_dir / "install" / "managed-install.json").is_file():
                return True
        except OSError:
            pass
        try:
            if _PI_PACKAGE_MARKER in str(binary.resolve()):
                return True
        except OSError:
            pass
        return False
