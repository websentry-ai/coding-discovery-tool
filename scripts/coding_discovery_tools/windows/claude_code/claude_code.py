"""
Claude Code detection for Windows
"""

import json
import logging
from pathlib import Path
from typing import Optional, Dict, List

from ...coding_tool_base import BaseToolDetector
from ...constants import COMMAND_TIMEOUT, VERSION_TIMEOUT
from ...utils import run_command, extract_version_number, resolve_windows_shortcut
from .claude_rules_extractor import WindowsClaudeRulesExtractor

logger = logging.getLogger(__name__)


class WindowsClaudeDetector(BaseToolDetector):
    """Claude Code detector for Windows systems."""

    @property
    def tool_name(self) -> str:
        """Return the name of the tool being detected."""
        return "Claude Code"

    def detect(self) -> Optional[Dict]:
        """
        Detect Claude Code installation on Windows.
        
        Returns:
            Dict with tool info or None if not found
        """
        # Check PATH
        claude_info = self._check_in_path()
        if claude_info:
            return claude_info

        # Check common directories
        claude_info = self._check_directories()
        if claude_info:
            return claude_info

        # Check Start Menu shortcuts
        claude_info = self._find_from_shortcuts()
        if claude_info:
            return claude_info

        return None

    def get_version(self, binary: Optional[str] = None) -> Optional[str]:
        """Extract Claude Code version.

        Probes ``binary`` when detection already resolved one, so the reported
        version belongs to the install being reported.

        A ``.cmd``/``.bat`` shim is read, never run: CreateProcess cannot execute
        one, and routing it through ``cmd`` would let a profile directory holding
        a metacharacter (``C:\\Users\\a&b\\...``) execute part of its own path.
        """
        if binary is not None:
            path = Path(binary)
            if path.suffix.lower() in (".cmd", ".bat"):
                return self._version_from_npm_metadata(path)
            output = run_command([str(path), "--version"], VERSION_TIMEOUT)
            return extract_version_number(output) if output else None

        # Try cmd.exe
        output = run_command(["cmd", "/c", "claude", "--version"], VERSION_TIMEOUT)
        if output:
            return extract_version_number(output)

        # Try npx
        output = run_command(["npx", "@anthropic-ai/claude-code", "--version"], COMMAND_TIMEOUT)
        if output:
            return extract_version_number(output)

        return None

    def _version_from_npm_metadata(self, shim: Path) -> Optional[str]:
        """Version of the npm package an npm shim belongs to, read from its
        ``package.json`` — the same layout ``_looks_like_install`` gates on."""
        package_json = shim.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "package.json"
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            logger.debug(f"Could not read Claude Code version from {package_json}: {e}")
            return None
        version = data.get("version") if isinstance(data, dict) else None
        return version if isinstance(version, str) else None

    def _check_in_path(self) -> Optional[Dict]:
        """Check if claude is in PATH."""
        for ext in ["", ".exe", ".cmd", ".ps1"]:
            output = run_command(["where", f"claude{ext}"], VERSION_TIMEOUT)
            if output:
                claude_paths = output.split('\n')
                for claude_path in claude_paths:
                    claude_path = claude_path.strip()
                    if claude_path and Path(claude_path).exists():
                        install_path = self._resolve_install_path(Path(claude_path))
                        return {
                            "name": self.tool_name,
                            "version": self.get_version(),
                            "install_path": str(install_path)
                        }
        return None

    def _resolve_install_path(self, executable_path: Path) -> Path:
        """Resolve the actual Claude Code installation path from executable."""
        bin_dir = executable_path.parent

        # Check if in npm global directory
        if "npm" in str(bin_dir).lower():
            node_modules_path = bin_dir / "node_modules" / "@anthropic-ai" / "claude-code"
            if node_modules_path.exists():
                return node_modules_path
            return bin_dir

        return bin_dir

    def _check_directories(self) -> Optional[Dict]:
        """Check common Windows directories for Claude Code."""
        claude_paths = self._get_search_paths()

        for claude_path in claude_paths:
            if not claude_path.exists():
                continue

            # Check if it's an executable file
            if claude_path.is_file() and claude_path.suffix.lower() in ['.exe', '.cmd', '.ps1']:
                return {
                    "name": self.tool_name,
                    "version": self.get_version(),
                    "install_path": str(claude_path.parent)
                }

            # Check if it's a directory with executables
            if claude_path.is_dir():
                if self._check_for_executable(claude_path) or self._looks_like_install(claude_path):
                    return {
                        "name": self.tool_name,
                        "version": self.get_version(),
                        "install_path": str(claude_path)
                    }

        return None

    def _get_search_paths(self) -> List[Path]:
        """Get list of paths to search for Claude Code installation."""
        user_home = Path.home()
        return [
            user_home / "AppData" / "Roaming" / "npm",
            user_home / "AppData" / "Local" / "Programs" / "claude",
            user_home / "AppData" / "Local" / "Programs" / "Claude",
            user_home / "AppData" / "Roaming" / "claude",
            user_home / "AppData" / "Roaming" / "Claude",
            user_home / ".claude",
            Path("C:\\Program Files") / "claude",
            Path("C:\\Program Files") / "Claude",
            Path("C:\\Program Files (x86)") / "claude",
            Path("C:\\Program Files (x86)") / "Claude",
        ]

    def _check_for_executable(self, directory: Path) -> bool:
        """Check if directory contains Claude executable."""
        exe_names = ["claude.exe", "Claude.exe", "claude.cmd", "claude.ps1", "claude"]
        return any((directory / exe_name).exists() for exe_name in exe_names)

    def _looks_like_install(self, directory: Path) -> bool:
        """Check if directory appears to be a Claude Code installation."""
        npm_path = directory / "node_modules" / "@anthropic-ai" / "claude-code"
        return npm_path.exists()

    def _find_from_shortcuts(self) -> Optional[Dict]:
        """Find Claude Code by checking Windows Start Menu shortcuts."""
        try:
            start_menu_paths = [
                Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs",
                Path("C:\\ProgramData") / "Microsoft" / "Windows" / "Start Menu" / "Programs",
            ]

            for start_menu in start_menu_paths:
                if not start_menu.exists():
                    continue

                for shortcut in start_menu.rglob("*.lnk"):
                    shortcut_name = shortcut.name.lower()
                    if "claude" in shortcut_name and "uninstall" not in shortcut_name:
                        install_dir = self._resolve_shortcut(shortcut)
                        if install_dir:
                            return {
                                "name": self.tool_name,
                                "version": self.get_version(),
                                "install_path": str(install_dir)
                            }
        except Exception as e:
            logger.debug(f"Could not find Claude Code from shortcuts: {e}")

        return None

    def _resolve_shortcut(self, shortcut_path: Path) -> Optional[Path]:
        """Resolve Windows shortcut to Claude installation."""
        target_path = resolve_windows_shortcut(shortcut_path)
        if target_path and target_path.name.lower() in ["claude.exe", "claude code.exe"]:
            return target_path.parent
        return None

    def extract_all_claude_rules(self) -> List[Dict]:
        """
        Extract all Claude Code rules from all projects on the machine.
        
        Returns:
            List of project dicts, each containing project_root and rules array
        """
        extractor = WindowsClaudeRulesExtractor()
        return extractor.extract_all_claude_rules()

