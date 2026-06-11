"""
Cursor CLI detection for Windows.

Cursor CLI is the standalone agentic terminal tool ``cursor-agent`` — distinct
from the Cursor IDE's ``cursor`` launcher. Gating on ``cursor-agent`` avoids
mislabelling the IDE launcher as the CLI.
"""

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional, Dict

from ...coding_tool_base import BaseToolDetector
from ...constants import VERSION_TIMEOUT
from ...utils import run_command, extract_version_number

logger = logging.getLogger(__name__)


class WindowsCursorCliDetector(BaseToolDetector):
    """
    Detector for Cursor CLI installations on Windows systems.

    Detection involves:
    - Checking if the ``cursor-agent`` command is available via ``where cursor-agent``
    - Reading the version via ``cursor-agent --version``
    """

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Cursor CLI"

    def detect(self) -> Optional[Dict]:
        """
        Detect Cursor CLI installation on Windows.

        Returns:
            Dict containing tool info (name, version, install_path) or None if not found
        """
        install_path = self._check_cursor_command()
        if not install_path:
            return None

        version = self.get_version()

        return {
            "name": self.tool_name,
            "version": version or "Unknown",
            "install_path": install_path
        }

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """
        Extract Cursor CLI version using ``cursor-agent --version``. Best-effort.

        ``shell=True`` is required so the npm ``.cmd`` shim can run (Windows can't
        exec a ``.cmd`` from a bare argv list).

        Args:
            binary: When provided, probe THIS exact ``cursor-agent`` path (the one
                detection already resolved for the user). Under an admin/MDM
                all-users scan the user's per-user ``cursor-agent`` is NOT on the
                scanner's PATH, so the bare ``cursor-agent`` probe yields nothing
                and the version reads "Unknown" — probing the resolved binary
                directly populates it. The absolute path is passed as a single
                shell-quoted command string (``"<path>" --version``) because a
                bare argv list under ``shell=True`` would split a path containing
                spaces (e.g. ``C:\\Users\\First Last\\...``); ``subprocess.list2cmdline``
                quotes it the way ``cmd.exe`` expects. When ``None``, keep the
                legacy bare-PATH probe (back-compat).

        Returns:
            Version string or None if version cannot be determined
        """
        try:
            if binary:
                # Quote the absolute path for cmd.exe (handles spaces); a bare
                # ["<path with spaces>", "--version"] list under shell=True would
                # break, so build one properly-quoted command string.
                command = subprocess.list2cmdline([str(binary), "--version"])
            else:
                command = ["cursor-agent", "--version"]
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=VERSION_TIMEOUT,
                shell=True
            )
            if result.returncode == 0:
                output = result.stdout.strip() or result.stderr.strip()
                if output:
                    return extract_version_number(output.strip())
        except Exception as e:
            logger.debug(f"Could not extract Cursor CLI version: {e}")
        return None

    def _check_cursor_command(self) -> Optional[str]:
        """
        Check if the ``cursor-agent`` command is available via ``where cursor-agent``.

        Probing ``cursor-agent`` (NOT ``cursor``) is what avoids mislabelling the
        Cursor IDE launcher as the CLI.

        Returns:
            Path to the ``cursor-agent`` executable if found, None otherwise
        """
        try:
            output = run_command(["where", "cursor-agent"], VERSION_TIMEOUT)
            if output:
                path = output.strip().split('\n')[0].strip()
                if Path(path).exists():
                    logger.debug(f"Found Cursor CLI (cursor-agent) at: {path}")
                    return path

            cursor_path = shutil.which("cursor-agent")
            if cursor_path:
                path = Path(cursor_path)
                if path.exists():
                    logger.debug(f"Found Cursor CLI (cursor-agent) via shutil.which at: {path}")
                    return str(path)
        except Exception as e:
            logger.debug(f"Could not check for Cursor CLI command: {e}")
        return None
