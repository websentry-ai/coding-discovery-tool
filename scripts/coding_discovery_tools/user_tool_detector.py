"""
User-specific tool detection module.

This module handles detection of tools that are installed per-user, checking
user-specific paths like ~/.nvm, ~/.bun, and user configuration directories.
"""

import logging
import os
import platform
from pathlib import Path
from typing import Dict, Optional

from .claude_cowork_skills_helpers import COWORK_SESSIONS_DIR
from .coding_tool_base import BaseToolDetector

logger = logging.getLogger(__name__)


def detect_tool_for_user(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """
    Detect a specific tool for a user by checking their paths directly.

    This function handles user-specific tool installations that may not be
    in the system PATH, such as:
    - npm/nvm installations in ~/.nvm/versions
    - Bun global binaries in ~/.bun/bin
    - User configuration directories like ~/.claude, ~/.gemini

    Args:
        detector: Tool detector instance
        user_home: Path to the user's home directory

    Returns:
        Tool info dict with keys: name, version, install_path
        Returns None if tool is not found
    """
    detector.user_home = user_home

    tool_name = detector.tool_name.lower()
    
    # System-wide tools (same for all users) - detect normally
    if tool_name in ["cursor", "windsurf", "antigravity", "replit"]:
        return detector.detect()
    
    # User-specific tools - check user's home directory paths
    # Priority: npm (via nvm) installs, then Bun fallback
    
    # Claude Code detection
    if tool_name == "claude code":
        return _detect_claude_code(detector, user_home)
    
    # Extension-based tools (Roo Code, Cline, Kilo Code)
    elif tool_name in ["roo code", "cline", "kilocode", "kilo code"]:
        return _detect_extension_tool(detector, user_home, tool_name)
    
    # Codex detection
    elif tool_name == "codex":
        return _detect_codex(detector, user_home)
    
    # OpenCode detection
    elif tool_name == "opencode":
        return _detect_opencode(detector, user_home)
    
    # Gemini CLI detection
    elif tool_name == "gemini cli":
        return _detect_gemini_cli(detector, user_home)

    # Cursor CLI detection
    elif tool_name == "cursor cli":
        return _detect_cursor_cli(detector, user_home)

    # Claude Cowork detection
    elif tool_name == "claude cowork":
        return _detect_claude_cowork(detector, user_home)

    # Default: Use detector's standard detection
    return detector.detect()


def _detect_claude_code(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Claude Code installation for a user."""
    # Check user's .claude directory first
    claude_dir = user_home / ".claude"
    if claude_dir.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(claude_dir)
        }
    
    # Check user's .nvm versions for claude binary (npm installs - most common)
    nvm_versions = user_home / ".nvm" / "versions"
    if nvm_versions.exists():
        try:
            for version_dir in nvm_versions.iterdir():
                if version_dir.is_dir():
                    claude_bin = version_dir / "bin" / "claude"
                    if claude_bin.exists():
                        return {
                            "name": detector.tool_name,
                            "version": detector.get_version(),
                            "install_path": str(claude_bin)
                        }
        except (PermissionError, OSError):
            pass
    
    # Fallback: Check Bun global binaries
    bun_bin = user_home / ".bun" / "bin" / "claude"
    if bun_bin.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(bun_bin)
        }
    
    return None


def _detect_extension_tool(
    detector: BaseToolDetector,
    user_home: Path,
    tool_name: str
) -> Optional[Dict]:
    """
    Detect extension-based tools (Roo Code, Cline, Kilo Code).

    Delegates to the detector's detect() method to get properly formatted results
    like "Roo Code (Cursor)", "Roo Code (VS Code)", etc.
    """
    return detector.detect()


def _detect_codex(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Codex installation for a user."""
    # Check user's .nvm versions for codex (npm installs - most common)
    nvm_versions = user_home / ".nvm" / "versions"
    if nvm_versions.exists():
        try:
            for version_dir in nvm_versions.iterdir():
                if version_dir.is_dir():
                    codex_bin = version_dir / "bin" / "codex"
                    if codex_bin.exists():
                        return {
                            "name": detector.tool_name,
                            "version": detector.get_version(),
                            "install_path": str(codex_bin)
                        }
        except (PermissionError, OSError):
            pass
    
    # Fallback: Check Bun global binaries
    bun_bin = user_home / ".bun" / "bin" / "codex"
    if bun_bin.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(bun_bin)
        }
    
    return None


def _detect_opencode(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect OpenCode installation for a user."""
    # Check user's .nvm versions for opencode
    nvm_versions = user_home / ".nvm" / "versions"
    if nvm_versions.exists():
        try:
            for version_dir in nvm_versions.iterdir():
                if version_dir.is_dir():
                    opencode_bin = version_dir / "bin" / "opencode"
                    if opencode_bin.exists():
                        return {
                            "name": detector.tool_name,
                            "version": detector.get_version(),
                            "install_path": str(opencode_bin)
                        }
        except (PermissionError, OSError):
            pass
    
    # Fallback: Check Bun global binaries
    bun_bin = user_home / ".bun" / "bin" / "opencode"
    if bun_bin.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(bun_bin)
        }
    
    return None


def _detect_gemini_cli(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Gemini CLI installation for a user."""
    # Check user's .gemini directory first (global installation/config)
    gemini_dir = user_home / ".gemini"
    if gemini_dir.exists() and gemini_dir.is_dir():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(gemini_dir)
        }
    
    # Check user's .nvm versions for gemini (npm installs - most common)
    # npm creates symlinks with hash suffixes like .gemini-lUK4BXcM
    nvm_versions = user_home / ".nvm" / "versions" / "node"
    if nvm_versions.exists():
        try:
            for version_dir in nvm_versions.iterdir():
                if not version_dir.is_dir():
                    continue
                    
                bin_dir = version_dir / "bin"
                if not bin_dir.exists():
                    continue
                
                # Look for gemini binary (could be 'gemini' or '.gemini-*' symlink)
                for bin_file in bin_dir.iterdir():
                    if not (bin_file.name.startswith("gemini") or bin_file.name.startswith(".gemini")):
                        continue
                    
                    # Verify it points to gemini-cli package
                    is_gemini = False
                    if bin_file.is_symlink():
                        target = bin_file.readlink()
                        if "gemini-cli" in str(target).lower():
                            is_gemini = True
                    elif "gemini" in bin_file.name.lower():
                        is_gemini = True
                    
                    if is_gemini:
                        version = detector.get_version()
                        return {
                            "name": detector.tool_name,
                            "version": version or "Unknown",
                            "install_path": str(bin_file)
                        }
        except (PermissionError, OSError):
            pass
    
    # Fallback: Check Bun global binaries
    bun_bin = user_home / ".bun" / "bin" / "gemini"
    if bun_bin.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(bun_bin)
        }
    
    # Final fallback: Use detector's default detection (checks PATH via 'which gemini')
    return detector.detect()


def _detect_cursor_cli(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Cursor CLI installation for a user."""
    cursor_dir = user_home / ".cursor"
    cli_config = cursor_dir / "cli-config.json"
    if cli_config.exists():
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(cursor_dir)
        }

    return detector.detect()


def _detect_claude_cowork(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Claude Cowork installation for a user."""
    # Claude Desktop app must be present (system-wide on macOS)
    if platform.system() == "Darwin":
        app_path = Path("/Applications/Claude.app")
        try:
            if not app_path.exists():
                return None
        except OSError:
            return None
        sessions_dir = user_home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
    else:
        # Windows
        sessions_dir = user_home / "AppData" / "Roaming" / "Claude" / COWORK_SESSIONS_DIR

    try:
        if sessions_dir.exists() and sessions_dir.is_dir():
            return {
                "name": detector.tool_name,
                "version": detector.get_version(),
                "install_path": str(sessions_dir)
            }
    except (PermissionError, OSError):
        pass

    return None


def find_claude_binary_for_user(user_home: Path) -> Optional[str]:
    """
    Find the absolute path to the claude binary for a specific user.

    On macOS: Homebrew (Apple Silicon and Intel), .local/bin, Bun, nvm.
    On Windows: .local/bin, AppData npm, AppData Local Programs.

    Args:
        user_home: Path to the user's home directory

    Returns:
        Absolute path to claude binary as string, or None if not found
    """
    if platform.system() == "Windows":
        candidates = [
            user_home / ".local" / "bin" / "claude.exe",
            user_home / "AppData" / "Roaming" / "npm" / "claude.cmd",
            user_home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
        ]
    else:
        candidates = [
            Path("/opt/homebrew/bin/claude"),          # Apple Silicon Homebrew
            Path("/usr/local/bin/claude"),             # Intel Mac / manual install
            user_home / ".local" / "bin" / "claude",   # Official installer
            user_home / ".bun" / "bin" / "claude",     # Bun global install
        ]

    for candidate in candidates:
        try:
            if candidate.exists():
                if os.access(str(candidate), os.X_OK):
                    return str(candidate)
                logger.debug(
                    f"Claude binary exists but not executable: {candidate}"
                )
        except (PermissionError, OSError):
            continue

    # Walk nvm versions directory for node-installed claude binaries
    nvm_node_dir = user_home / ".nvm" / "versions" / "node"
    try:
        if nvm_node_dir.exists():
            for version_dir in nvm_node_dir.iterdir():
                if not version_dir.is_dir():
                    continue
                nvm_candidate = version_dir / "bin" / "claude"
                try:
                    if nvm_candidate.exists():
                        if os.access(str(nvm_candidate), os.X_OK):
                            return str(nvm_candidate)
                        logger.debug(
                            f"Claude binary exists but not executable: {nvm_candidate}"
                        )
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError):
        pass

    return None
