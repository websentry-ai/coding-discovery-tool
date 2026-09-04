"""
GitHub Copilot CLI detection for Windows.

The GitHub Copilot CLI (``@github/copilot``) is the standalone agentic terminal
tool, distinct from the GitHub Copilot VS Code extension / JetBrains plugin. It
keeps its configuration under ``%USERPROFILE%\\.copilot`` (i.e. ``~/.copilot``),
identical to the macOS layout, with its MCP servers in
``~/.copilot/mcp-config.json``.

OS-specific overrides: the all-users scan (``is_running_as_admin`` + ``C:\\Users``),
the binary resolve (``_resolve_windows_binary``: npm ``copilot.cmd`` / WinGet
``Links\\copilot.exe`` shims / ``.local/bin`` / ``.bun/bin``, no Homebrew), and
``get_version`` (a resolved ``.cmd`` shim is read, not run: Windows can't exec it
from a bare argv list and a shell would let a profile path holding ``&``/``^``
execute part of itself). Everything else is inherited from the macOS detector (DRY).
"""

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

from ...constants import VERSION_TIMEOUT
from ...utils import run_command, windows_node_manager_shims
from ...windows_extraction_helpers import is_running_as_admin
from ...macos.copilot_cli.copilot_cli import MacOSCopilotCliDetector, _parse_cli_version

logger = logging.getLogger(__name__)


class WindowsCopilotCliDetector(MacOSCopilotCliDetector):
    """
    Detector for GitHub Copilot CLI installations on Windows systems.

    Inherits the full macOS detection surface (binary gate, per-user detection,
    ``detect``/``detect_all_tools``, and ``get_version``) and overrides only the
    all-users branch: when ``self.user_home`` is unset and the process is admin,
    every user under ``C:\\Users`` is scanned; otherwise the current user's home
    is checked. Each detected user yields a distinct row whose ``install_path``
    is that user's resolved ``copilot`` binary (with ``~/.copilot`` carried as
    the internal ``_config_path``).
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

    def _resolve_binary(self, user_home: Path) -> Optional[str]:
        """Resolve the ``copilot`` CLI binary for ``user_home`` (the detection gate).

        Overrides the macOS resolver with the Windows candidate list
        (``_resolve_windows_binary``; no Homebrew). Returns a path string or None.
        """
        binary = self._resolve_windows_binary(user_home)
        return str(binary) if binary is not None else None

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

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """
        Extract Copilot CLI version on Windows using ``copilot --version``.

        Overrides the inherited macOS probe to pass ``shell=True`` (via
        ``_probe_version``): npm installs the CLI as a ``copilot.cmd`` shim, which
        Windows cannot exec from a bare argv list, so the inherited probe would read
        "unknown". Mirrors ``WindowsCodexDetector``.

        Args:
            binary: When provided, probe this exact ``copilot`` path with no
                re-resolve and no bare ``copilot`` fallback (works when
                ``self.user_home`` is unset). When ``None``, resolve the per-user
                binary off ``self.user_home`` if set, else probe bare ``copilot``.

        Best-effort: returns None on any failure and the caller falls back to
        "unknown".
        """
        if binary is not None:
            return self._version_for_binary(binary)

        try:
            if self.user_home is not None:
                resolved = self._resolve_windows_binary(self.user_home)
                if resolved is not None:
                    parsed = self._version_for_binary(resolved)
                    if parsed:
                        return parsed
        except Exception as exc:
            logger.debug(f"Could not extract Copilot CLI version from per-user binary on Windows: {exc}")

        return self._probe_version(["copilot", "--version"])

    @staticmethod
    def _resolve_windows_binary(user_home: Path) -> Optional[Path]:
        """Return the per-user ``copilot`` CLI binary for ``user_home`` on Windows.

        Checks the per-user install locations below: the npm global shim, the WinGet
        shim (the ``GitHub.Copilot`` package's ``copilot`` command alias lands in the
        per-user Links dir), the ``.local/bin`` / ``.bun/bin`` binaries, and the
        Node managers (nvm-windows, Volta, pnpm).
        Best-effort: returns None on any error. Never raises.
        """
        try:
            for candidate in (
                user_home / "AppData" / "Roaming" / "npm" / "copilot.cmd",
                user_home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "copilot.exe",
                user_home / ".local" / "bin" / "copilot.exe",
                user_home / ".bun" / "bin" / "copilot.exe",
                *windows_node_manager_shims(user_home, "copilot"),
            ):
                try:
                    if candidate.exists():
                        return candidate
                except OSError:
                    continue
        except (PermissionError, OSError) as exc:
            logger.debug(f"Error resolving Copilot CLI binary for {user_home}: {exc}")
        return None

    def _version_for_binary(self, binary) -> Optional[str]:
        """Version of a resolved ``copilot`` binary, never via the shell.

        A resolved path carries the profile name, and ``&``/``^`` are legal in
        Win32 account names, so probing it under ``shell=True`` would let
        ``C:\\Users\\a&b\\...`` execute part of its own path as SYSTEM. A
        ``.cmd``/``.bat`` shim is read instead of run; anything else is exec'd
        directly, which needs no shell.
        """
        path = Path(binary)
        if path.suffix.lower() in (".cmd", ".bat"):
            return self._version_from_npm_metadata(path)
        return _parse_cli_version(run_command([str(path), "--version"], VERSION_TIMEOUT))

    @staticmethod
    def _version_from_npm_metadata(shim: Path) -> Optional[str]:
        """Version of the npm package an npm shim belongs to, read from its ``package.json``."""
        package_json = shim.parent / "node_modules" / "@github" / "copilot" / "package.json"
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.debug(f"Could not read Copilot CLI version from {package_json}: {e}")
            return None
        version = data.get("version") if isinstance(data, dict) else None
        return version if isinstance(version, str) else None

    @staticmethod
    def _probe_version(command: List[str]) -> Optional[str]:
        """Run ``command`` with ``shell=True`` and parse the version banner.

        ``shell=True`` is required for the npm ``.cmd`` shim. Best-effort:
        returns None on any failure (the caller falls back to "unknown").
        """
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
                shell=True,  # Required for npm .CMD shims on Windows
            )
            if result.returncode == 0:
                return _parse_cli_version(result.stdout or result.stderr)
        except Exception as exc:
            logger.debug(f"Could not extract Copilot CLI version on Windows: {exc}")
        return None
