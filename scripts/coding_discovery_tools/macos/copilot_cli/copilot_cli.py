"""
GitHub Copilot CLI detection for macOS.

The GitHub Copilot CLI (``@github/copilot``) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin. It
keeps its configuration under ``~/.copilot/`` and its MCP servers in
``~/.copilot/mcp-config.json``.

Detection is per-user (each user with a qualifying ``~/.copilot/`` directory is
reported as its own row), mirroring the existing Copilot detector's
all-users/root scanning rather than Codex's global-binary model.
"""

import logging
import re
from pathlib import Path
from typing import Dict, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...macos_extraction_helpers import is_running_as_root
from ...utils import run_command

logger = logging.getLogger(__name__)

# The CLI home directory is version-dependent in its exact contents. We never
# gate on a single file, and never on bare-directory existence alone — we
# require ``~/.copilot/`` to exist AND contain at least one known artifact from
# this union set (review P0-1). Older CLI builds use ``config.json``; current
# docs use ``settings.json``.
_CLI_DIR_NAME = ".copilot"
_CLI_MARKER_FILES = frozenset({
    "settings.json",
    "config.json",
    "mcp-config.json",
    "lsp-config.json",
})
_CLI_MARKER_DIRS = frozenset({
    "session-state",
    "history-session-state",
    "instructions",
    "logs",
    "pkg",
    "installed-plugins",
})


def _copilot_dir_has_known_artifact(copilot_dir: Path) -> bool:
    """Return True if ``copilot_dir`` contains at least one known CLI artifact.

    A known artifact is any of the marker files (as a file) or any of the
    marker directories (as a directory). Bare directory existence is not
    sufficient — the layout is version-dependent, so we accept a union of
    signals but require at least one to actually be present.
    """
    try:
        for marker in _CLI_MARKER_FILES:
            candidate = copilot_dir / marker
            try:
                if candidate.is_file():
                    return True
            except OSError:
                continue
        for marker in _CLI_MARKER_DIRS:
            candidate = copilot_dir / marker
            try:
                if candidate.is_dir():
                    return True
            except OSError:
                continue
    except OSError as exc:
        logger.debug(f"Error inspecting Copilot CLI dir {copilot_dir}: {exc}")
    return False


_VERSION_RE = re.compile(r"\d+\.\d+\.\d+(?:[.\-+][0-9A-Za-z.\-]+)?")


def _parse_cli_version(raw: Optional[str]) -> Optional[str]:
    """Extract a clean version from raw ``copilot --version`` output.

    The CLI prints a multi-line banner (e.g. ``"GitHub Copilot CLI 0.0.399.\\n
    Run 'copilot update'..."``); we want just the version number so the stored
    value is clean, comparable, and well under the backend's version column
    limit (a raw multi-line banner overflows it). Falls back to the first
    non-empty line (capped) when no semver is present.
    """
    if not raw:
        return None
    match = _VERSION_RE.search(raw)
    if match:
        return match.group(0)
    first_line = next((line.strip() for line in raw.splitlines() if line.strip()), "")
    return first_line[:50] or None


class MacOSCopilotCliDetector(BaseToolDetector):
    """
    Detector for GitHub Copilot CLI installations on macOS systems.

    Detection involves:
    - Checking that a user's ``~/.copilot/`` directory exists.
    - Verifying it contains at least one known CLI artifact (a marker file or
      marker directory) so a stray empty ``~/.copilot`` does not count.

    When ``user_home`` is set on the instance (the per-user path used by the
    live discovery loop via ``detect_tool_for_user``), detection is scoped to
    that single user. Otherwise, when running as root, all users under
    ``/Users`` are scanned; for a regular user, only their own home is checked.
    Each detected user produces a distinct row whose ``install_path`` is that
    user's ``~/.copilot`` directory.
    """

    def __init__(self) -> None:
        # Set by detect_tool_for_user() in the live discovery loop to scope
        # detection to a single user's home directory.
        self.user_home: Optional[Path] = None

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "GitHub Copilot CLI"

    def detect(self) -> Optional[Dict]:
        """
        Detect GitHub Copilot CLI installation(s) on macOS.

        Respects ``self.user_home`` when set (single-user scope). Otherwise
        performs an all-users scan when running as root, or checks the current
        user's home directory.

        Returns:
            For a single user (``self.user_home`` set or a single match), a dict
            with tool info, or None if not found. When multiple users qualify
            during an all-users scan, returns a list of dicts.
        """
        results = self._detect_all_users()
        if not results:
            return None
        if len(results) == 1:
            return results[0]
        return results

    def detect_all_tools(self, user_home: Optional[str] = None) -> List[Dict]:
        """
        Entry point mirroring other multi-result detectors.

        Args:
            user_home: Optional user home directory. When provided, scopes
                detection to that single user; otherwise self-contained
                all-users scan (root) or current-user check.

        Returns:
            List of detected tool dicts (possibly empty).
        """
        if user_home is not None:
            self.user_home = Path(user_home)
        return self._detect_all_users()

    def get_version(self) -> Optional[str]:
        """
        Extract Copilot CLI version using ``copilot --version``.

        Best-effort only: the CLI binary may not be on PATH (e.g. when running
        as root or scanning another user), in which case "unknown" is returned
        by the caller.

        Returns:
            Parsed version (e.g. ``0.0.399``) or None if it can't be determined.
            The semver is parsed out of the multi-line ``copilot --version``
            banner so the stored value is clean and within the backend's version
            column limit (a raw multi-line banner overflows it).
        """
        # TODO(copilot-cli): resolve the per-user binary (e.g. ~/.local/bin,
        # .bun/bin, nvm paths) and probe that explicitly, mirroring
        # find_claude_binary_for_user, so version populates on root MDM scans
        # where root's PATH lacks the user's copilot install (review W2).
        try:
            output = run_command(["copilot", "--version"], VERSION_TIMEOUT)
            return _parse_cli_version(output)
        except Exception as exc:
            logger.debug(f"Could not extract Copilot CLI version: {exc}")
        return None

    def _detect_all_users(self) -> List[Dict]:
        """
        Detect the Copilot CLI for the relevant set of users.

        - If ``self.user_home`` is set, check only that user.
        - Else if running as root, scan every directory under ``/Users``.
        - Else check the current user's home directory.
        """
        if self.user_home is not None:
            result = self._detect_for_user(self.user_home)
            return [result] if result else []

        if is_running_as_root():
            return self._detect_for_all_system_users()

        result = self._detect_for_user(Path.home())
        return [result] if result else []

    def _detect_for_all_system_users(self) -> List[Dict]:
        """Scan every user directory under /Users when running as root.

        Fallback path: the live MDM discovery loop scopes detection per-user via
        ``detect_tool_for_user`` (which sets ``self.user_home``), so this root
        all-users branch only fires for a direct ``detect()`` call with no
        ``user_home`` set. Kept for parity with the sibling Copilot detectors and
        the standalone entry point (review W1).
        """
        results: List[Dict] = []
        users_dir = Path("/Users")
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
            logger.debug(f"Error scanning /Users for Copilot CLI: {exc}")
        return results

    def _detect_for_user(self, user_home: Path) -> Optional[Dict]:
        """
        Detect the Copilot CLI for a single user's home directory.

        Returns a tool-info dict when ``user_home/.copilot`` exists and holds at
        least one known artifact; otherwise None.
        """
        copilot_dir = user_home / _CLI_DIR_NAME
        try:
            if not copilot_dir.is_dir():
                return None
        except OSError as exc:
            logger.debug(f"Error checking Copilot CLI dir {copilot_dir}: {exc}")
            return None

        if not _copilot_dir_has_known_artifact(copilot_dir):
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "unknown",
            "publisher": "GitHub",
            "install_path": str(copilot_dir),
        }
