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
import os
import re
from pathlib import Path
from typing import Dict, FrozenSet, List, Optional

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...macos_extraction_helpers import is_running_as_root
from ...utils import run_command

logger = logging.getLogger(__name__)

# The CLI home directory is version-dependent in its exact contents. We never
# gate on a single file, and never on bare-directory existence alone — we
# require the config dir to exist AND contain at least one STRONG (CLI-exclusive)
# artifact (review P0-1).
#
# Markers are split into two tiers:
#
#   STRONG (CLI-exclusive): files/dirs the standalone GitHub Copilot CLI writes
#     and that no other tool reads. Presence of any one of these is sufficient
#     to declare a CLI install. The set mirrors GitHub's documented config-dir
#     contents (Copilot CLI "config dir reference"); ``pkg`` is an
#     observed-but-undocumented binary dir kept as an extra real-world signal.
#
#   SHARED: markers under ``~/.copilot/`` that are NOT exclusive to the CLI,
#     because another GitHub Copilot surface also produces them. Two ways:
#       - ALSO read by the Copilot VS Code/JetBrains agent: ``skills/`` (and
#         ``agents/``, ``instructions/``, ``copilot-instructions.md``). Per the
#         VS Code Agent Skills docs, ``~/.copilot/skills/`` is a shared location
#         the IDE agent reads, so it is not exclusive to the CLI:
#         https://code.visualstudio.com/docs/agent-customization/agent-skills
#       - WRITTEN by the IDE extension itself: ``ide/``. The Copilot VS Code /
#         JetBrains extension drops a discovery lock file at
#         ``~/.copilot/ide/<uuid>.lock`` (containing the IDE name + pid) so the
#         CLI can connect — see microsoft/vscode-copilot-chat#3583. An IDE-only
#         user with no standalone CLI therefore has ``~/.copilot/ide/`` even
#         though the CLI was never installed.
#       - WRITTEN by Unbound's own MDM onboarding: ``hooks/``. The Copilot hook
#         installer (websentry-ai/setup ``copilot/hooks/mdm/setup.py``) runs for
#         EVERY onboarded device and does ``(~/.copilot/hooks).mkdir(parents=True)``
#         then writes ``unbound.json`` + ``unbound.py`` — creating ``~/.copilot/
#         hooks/`` from scratch on machines that never had the CLI. Treating it as
#         CLI-exclusive made every hooked device a phantom CLI install (confirmed
#         in prod: device D2FJV74J5Q / user gowshik — ``~/.copilot`` held only
#         ``hooks/unbound.json``, no binary). So ``hooks/`` is NOT CLI-exclusive.
#     Any of these can be present for an IDE-only (or hook-only) user, so none can
#     alone declare a CLI install (doing so produces phantom CLI rows); they are
#     tracked solely to log/suppress that case. A genuine CLI always also has a
#     strong marker (config.json / session-store.db / logs/), so demoting these
#     never causes a false negative.
_CLI_DIR_NAME = ".copilot"
_CLI_STRONG_MARKER_FILES = frozenset({
    "config.json",
    "mcp-config.json",
    "settings.json",
    "lsp-config.json",
    "permissions-config.json",
    "session-store.db",
})
_CLI_STRONG_MARKER_DIRS = frozenset({
    "logs",
    "session-state",
    "command-history-state",
    "installed-plugins",
    "plugin-data",
    "pkg",
})
_CLI_SHARED_MARKER_FILES = frozenset({
    "copilot-instructions.md",
})
_CLI_SHARED_MARKER_DIRS = frozenset({
    "skills",
    "agents",
    "instructions",
    "ide",
    "hooks",
})


def _resolve_copilot_dir(user_home: Path) -> Path:
    """Return the Copilot CLI config directory for ``user_home``.

    Honors the ``COPILOT_HOME`` environment variable, which — per GitHub's docs
    — *replaces* the entire ``~/.copilot`` path (its value is the complete
    config dir, not a parent). ``COPILOT_HOME`` is read from the process
    environment, so it only validly applies to the user the process runs as; we
    therefore honor it only when ``user_home`` is the running user's own home.
    During a root/all-users scan, another user's ``COPILOT_HOME`` (set in their
    shell) is not visible here, so those users fall back to the documented
    default ``<user_home>/.copilot``. (Reading a per-user ``COPILOT_HOME`` from
    each user's shell profiles during a root scan is a separate, best-effort
    follow-up.) The deprecated ``--config-dir`` flag is intentionally ignored.
    """
    try:
        if user_home == Path.home():
            override = (os.environ.get("COPILOT_HOME") or "").strip()
            if override:
                return Path(os.path.expanduser(os.path.expandvars(override)))
    except OSError as exc:
        logger.debug(f"Error resolving COPILOT_HOME: {exc}")
    return user_home / _CLI_DIR_NAME


def _dir_has_any_marker(
    copilot_dir: Path, marker_files: FrozenSet[str], marker_dirs: FrozenSet[str]
) -> bool:
    """Return True if ``copilot_dir`` holds any of ``marker_files`` (as a file)
    or ``marker_dirs`` (as a directory). Errors are swallowed (the tool must
    never crash) — a marker that can't be stat'd is treated as absent.
    """
    try:
        for marker in marker_files:
            try:
                if (copilot_dir / marker).is_file():
                    return True
            except OSError:
                continue
        for marker in marker_dirs:
            try:
                if (copilot_dir / marker).is_dir():
                    return True
            except OSError:
                continue
    except OSError as exc:
        logger.debug(f"Error inspecting Copilot CLI dir {copilot_dir}: {exc}")
    return False


def _copilot_dir_has_strong_artifact(copilot_dir: Path) -> bool:
    """Return True if ``copilot_dir`` holds at least one STRONG CLI artifact.

    A strong artifact is any of the STRONG marker files (present as a file) or
    any of the STRONG marker directories (present as a directory). These are
    written by the standalone GitHub Copilot CLI and read by no other tool, so a
    single one is sufficient to declare a CLI install. Bare directory existence
    is not sufficient — the layout is version-dependent, so we accept a union of
    signals but require at least one to actually be present.

    SHARED markers (``skills/``, ``agents/``, ``instructions/``,
    ``copilot-instructions.md``) are intentionally excluded: they are also read
    by the GitHub Copilot VS Code/JetBrains agent
    (https://code.visualstudio.com/docs/agent-customization/agent-skills), so on
    their own they cannot distinguish a CLI install from an IDE-only user and
    would produce phantom CLI rows. Use ``_copilot_dir_has_shared_artifact`` to
    detect that case for logging/suppression.
    """
    return _dir_has_any_marker(
        copilot_dir, _CLI_STRONG_MARKER_FILES, _CLI_STRONG_MARKER_DIRS
    )


def _copilot_dir_has_shared_artifact(copilot_dir: Path) -> bool:
    """Return True if ``copilot_dir`` holds at least one SHARED Copilot marker.

    A shared marker is any of the SHARED marker files (present as a file) or any
    of the SHARED marker directories (present as a directory): ``skills/``,
    ``agents/``, ``instructions/``, ``copilot-instructions.md``, ``ide/``, and
    ``hooks/``. These live under ``~/.copilot/`` but are NOT exclusive to the CLI:
    ``skills/``/``agents/``/``instructions/``/``copilot-instructions.md`` are
    ALSO read by the GitHub Copilot VS Code extension / JetBrains plugin's agent
    mode (https://code.visualstudio.com/docs/agent-customization/agent-skills);
    ``ide/`` is WRITTEN by that extension as a discovery lock
    (microsoft/vscode-copilot-chat#3583); and ``hooks/`` is WRITTEN by Unbound's
    own MDM onboarding (websentry-ai/setup copilot/hooks/mdm/setup.py). So none of
    them can, on their own, declare a standalone CLI install — an IDE-only or
    hook-only user who never installed the CLI can have them. This predicate
    exists only to recognise the "shared markers but no strong CLI artifact" case
    so it can be logged and suppressed rather than reported as a phantom CLI row.
    """
    return _dir_has_any_marker(
        copilot_dir, _CLI_SHARED_MARKER_FILES, _CLI_SHARED_MARKER_DIRS
    )


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
    - Verifying it contains at least one STRONG (CLI-exclusive) marker (a strong
      marker file or directory) so a stray empty ``~/.copilot`` does not count.
      A dir holding only SHARED markers (skills/agents/instructions/
      copilot-instructions.md, which the IDE Copilot agent reads; ide/, which the
      IDE extension writes as a discovery lock; or hooks/, which Unbound's own MDM
      onboarding creates) is the VS Code/JetBrains agent or Unbound's hook rather
      than the CLI, and is suppressed.

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

        Returns a tool-info dict when the resolved config dir (``COPILOT_HOME``
        when set for this user, else ``user_home/.copilot``) exists and holds at
        least one STRONG CLI artifact; otherwise None. A dir holding only SHARED
        markers (skills/agents/instructions/copilot-instructions.md/ide/hooks) is
        the IDE Copilot agent or Unbound's own MDM hook, not the CLI — it is
        logged and suppressed.
        """
        copilot_dir = _resolve_copilot_dir(user_home)
        try:
            if not copilot_dir.is_dir():
                return None
        except OSError as exc:
            logger.debug(f"Error checking Copilot CLI dir {copilot_dir}: {exc}")
            return None

        if not _copilot_dir_has_strong_artifact(copilot_dir):
            if _copilot_dir_has_shared_artifact(copilot_dir):
                logger.info(
                    "Skipping %s: only shared/IDE-written/hook-written Copilot markers present "
                    "(skills/agents/instructions/copilot-instructions.md/ide/hooks) — likely the "
                    "VS Code/JetBrains Copilot agent or Unbound's MDM hook, not the standalone CLI",
                    copilot_dir,
                )
            return None

        return {
            "name": self.tool_name,
            "version": self.get_version() or "unknown",
            "publisher": "GitHub",
            "install_path": str(copilot_dir),
        }
