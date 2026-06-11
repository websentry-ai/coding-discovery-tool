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
from .constants import VERSION_TIMEOUT
from .macos_extraction_helpers import is_running_as_root
from .utils import (
    machine_global_binary_owned_by_user,
    resolve_npm_global_tool_bin,
    run_command,
)

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
    """Detect Claude Code installation for a user.

    Gates on the claude binary, not the ~/.claude config directory. The config
    directory survives uninstall (residue), so detecting on it produces false
    positives. ~/.claude remains available to the rules/MCP extractor, which only
    runs once the tool is detected here.
    """
    claude_bin = find_claude_binary_for_user(user_home)
    if claude_bin:
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": claude_bin
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

    return detector.detect()


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

    return detector.detect()


def _detect_gemini_cli(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Gemini CLI installation for a user.

    Gates on the gemini binary, not the ~/.gemini config directory. The config
    directory survives uninstall (residue), so detecting on it produces false
    positives. ~/.gemini remains available to the rules/MCP extractor, which only
    runs once the tool is detected here.
    """
    # Check user's .nvm versions for gemini (npm installs - most common)
    # npm creates symlinks with hash suffixes like .gemini-lUK4BXcM
    nvm_versions = user_home / ".nvm" / "versions" / "node"
    try:
        nvm_present = nvm_versions.exists()
    except OSError:
        nvm_present = False
    if nvm_present:
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

    if platform.system() == "Windows":
        # Windows npm installs drop shims into %APPDATA%\npm (no POSIX X_OK
        # semantics, so gate on existence like the Claude Windows branch and
        # the Bun fallback below). Mirrors how Claude has a Windows branch.
        npm_dir = user_home / "AppData" / "Roaming" / "npm"
        windows_candidates = [
            npm_dir / "gemini.cmd",
            npm_dir / "gemini.ps1",
            npm_dir / "gemini",
        ]
        for candidate in windows_candidates:
            try:
                if candidate.exists():
                    return {
                        "name": detector.tool_name,
                        "version": detector.get_version() or "Unknown",
                        "install_path": str(candidate)
                    }
            except OSError:
                continue
    else:
        # Check common user binary locations the nvm/bun scans miss.
        # ~/.local/bin and the npm-global prefix are user_home-relative (always
        # safe). Homebrew and /usr/local are MACHINE-GLOBAL — they are ALWAYS
        # probed, but under a root/MDM multi-user scan each is owner-attributed
        # so one user's Homebrew install isn't fanned out to every user (the
        # 93b5fc2 cross-user FP); a root-owned system-wide binary attributes to
        # each scanned user. User_home-relative candidates are path-scoped and
        # always pass (no owner check).
        machine_global = [
            Path("/opt/homebrew/bin/gemini"),
            Path("/usr/local/bin/gemini"),
        ]
        user_relative = [
            user_home / ".local" / "bin" / "gemini",
            user_home / ".npm-global" / "bin" / "gemini",
        ]
        is_root = is_running_as_root()
        for candidate in machine_global + user_relative:
            try:
                if candidate.exists() and os.access(str(candidate), os.X_OK):
                    if is_root and candidate in machine_global \
                            and not machine_global_binary_owned_by_user(candidate, user_home):
                        continue
                    return {
                        "name": detector.tool_name,
                        "version": detector.get_version() or "Unknown",
                        "install_path": str(candidate)
                    }
            except OSError:
                continue

        # Resolve the npm global prefix (Homebrew node / nvm / pnpm vary) and
        # probe ``<prefix>/bin/gemini`` plus pnpm/nvm fallbacks. The dynamic
        # ``npm prefix -g`` probe is root-guarded inside the helper (it resolves
        # the SCANNER's prefix, not the user's — the 93b5fc2 cross-user FP class).
        npm_resolved = resolve_npm_global_tool_bin("gemini", user_home, is_running_as_root())
        if npm_resolved:
            return {
                "name": detector.tool_name,
                "version": detector.get_version() or "Unknown",
                "install_path": npm_resolved
            }

    # Fallback: Check Bun global binaries
    bun_bin = user_home / ".bun" / "bin" / "gemini"
    try:
        bun_present = bun_bin.exists()
    except OSError:
        bun_present = False
    if bun_present:
        return {
            "name": detector.tool_name,
            "version": detector.get_version(),
            "install_path": str(bun_bin)
        }
    
    # Final fallback: detector.detect() resolves `which gemini` against the
    # SCANNER's PATH, not user_home. Under a root/MDM multi-user scan that would
    # mis-attribute the scanner's gemini to a user who has only residue, so skip
    # it when root — the explicit user_home candidates above already cover real
    # installs (mirrors the Claude find_claude_binary_for_user root guard).
    if is_running_as_root():
        return None
    return detector.detect()


def _detect_cursor_cli(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Cursor CLI (``cursor-agent``) installation for a user.

    Gates on the ``cursor-agent`` binary, not the ``~/.cursor/cli-config.json``
    residue. ``~/.cursor`` (and its ``cli-config.json``) is also written by the
    Cursor IDE and survives a CLI uninstall, so detecting on it produced false
    positives. ``cli-config.json`` remains available to the settings extractor,
    which only runs once the tool is detected here.
    """
    cursor_agent_bin = find_cursor_agent_binary_for_user(user_home)
    if cursor_agent_bin:
        return {
            "name": detector.tool_name,
            # Probe the resolved binary directly: the detector's get_version()
            # otherwise runs a bare ``cursor-agent --version`` against the
            # SCANNER's PATH, which under a root/MDM all-users scan lacks the
            # user's ~/.local/bin/cursor-agent -> "Unknown" for every user.
            "version": detector.get_version(cursor_agent_bin) or "Unknown",
            "install_path": cursor_agent_bin
        }

    return None


def _detect_claude_cowork(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Claude Cowork installation for a user."""
    system = platform.system()
    if system == "Darwin":
        app_path = Path("/Applications/Claude.app")
        try:
            if not app_path.exists():
                return None
        except OSError:
            return None
        sessions_dir = user_home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
    elif system == "Linux":
        sessions_dir = user_home / ".config" / "Claude" / COWORK_SESSIONS_DIR
    else:
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

    On macOS/Linux: Homebrew (Apple Silicon and Intel), .local/bin, Bun,
    npm-global, yarn-global, nvm, and a ``which claude`` PATH backstop.
    On Windows: .local/bin, AppData npm (.cmd and bare), AppData Local Programs,
    and Bun.

    Args:
        user_home: Path to the user's home directory

    Returns:
        Absolute path to claude binary as string, or None if not found
    """
    machine_global: list = []
    if platform.system() == "Windows":
        candidates = [
            user_home / ".local" / "bin" / "claude.exe",
            user_home / "AppData" / "Roaming" / "npm" / "claude.cmd",
            user_home / "AppData" / "Roaming" / "npm" / "claude.exe",
            user_home / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
            # WinGet is a documented primary installer; it drops a shim into the
            # per-user Links dir (NOT under AppData\Local\Programs\claude).
            user_home / "AppData" / "Local" / "Microsoft" / "WinGet" / "Links" / "claude.exe",
            user_home / ".bun" / "bin" / "claude.exe",
        ]
    else:
        user_relative = [
            user_home / ".local" / "bin" / "claude",   # Official installer
            user_home / ".bun" / "bin" / "claude",     # Bun global install
            user_home / ".npm-global" / "bin" / "claude",  # npm global prefix
            user_home / ".config" / "yarn" / "global"  # yarn global install
            / "node_modules" / ".bin" / "claude",
        ]
        # Homebrew, /usr/local and /usr/bin are MACHINE-GLOBAL — they are
        # ALWAYS probed, but under a root/MDM multi-user scan each is
        # owner-attributed (see the loop below) so one user's Homebrew install
        # isn't fanned out to every user (the 93b5fc2 cross-user FP); a
        # root-owned system-wide binary attributes to each scanned user.
        machine_global = [
            Path("/opt/homebrew/bin/claude"),      # Apple Silicon Homebrew
            Path("/usr/local/bin/claude"),         # Intel Mac / manual install
            Path("/usr/bin/claude"),               # apt/dnf system package
        ]
        candidates = machine_global + user_relative

    is_root = is_running_as_root()
    for candidate in candidates:
        try:
            if candidate.exists():
                # Machine-global binaries under root are owner-attributed; a
                # candidate owned by a different user is skipped so it isn't
                # fanned out to every scanned user.
                if is_root and candidate in machine_global \
                        and not machine_global_binary_owned_by_user(candidate, user_home):
                    continue
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

    # PATH backstop: catch custom install prefixes the explicit list misses.
    # Only meaningful in the single-user / non-root case — the resolved PATH
    # is the SCANNER's, not ``user_home``'s. Under a root/MDM multi-user scan
    # it would resolve root's claude for a user who has none, mis-attributing
    # the install. The explicit candidate list above is comprehensive and
    # already user_home-relative, so we skip ``which`` when root, and on
    # Windows (where ``which`` is not a command — the .exe/.cmd candidates
    # above already cover it).
    if not is_running_as_root() and platform.system() != "Windows":
        which_path = run_command(["which", "claude"], VERSION_TIMEOUT)
        if which_path:
            try:
                resolved = Path(which_path)
                if resolved.exists() and os.access(str(resolved), os.X_OK):
                    return str(resolved)
            except (PermissionError, OSError):
                pass

    return None


def _cursor_agent_version_key(version_dir: Path):
    """Numeric (major, minor, patch) parsed from a "X.Y.Z" version-dir name so the
    newest sorts last (``reverse=True`` puts it first). A plain string sort would
    order "1.10.0" before "1.9.0" and report a stale version; malformed names yield
    () and sort earliest. Shared by the POSIX and Windows versioned-dir scans.
    """
    return tuple(int(p) for p in version_dir.name.split(".") if p.isdigit())


def find_cursor_agent_binary_for_user(user_home: Path) -> Optional[str]:
    """Find the absolute path to the ``cursor-agent`` binary for a user.

    Mirrors ``find_claude_binary_for_user``: gating on the binary (not the
    ``~/.cursor`` config dir, which the Cursor IDE also writes and which survives
    a CLI uninstall) is what kills the residue false positive.

    On POSIX the official installer drops ``cursor-agent`` (older builds: a bare
    ``agent`` shim) into ``~/.local/bin`` and keeps the real binary under
    ``~/.local/share/cursor-agent/versions/<ver>/cursor-agent`` (newest version
    preferred so a stale older build isn't picked). The npm-global prefix is
    probed via the shared resolver, and a root-guarded ``which cursor-agent``
    backstops custom prefixes (skipped under root — it resolves the SCANNER's
    PATH, not ``user_home``'s, which would mis-attribute the install). On Windows
    the native installer dir ``%LOCALAPPDATA%\\cursor-agent`` is checked
    (``cursor-agent``/``agent`` as ``.exe``/``.cmd`` at the root plus the newest
    ``versions\\<v>\\cursor-agent.exe``), along with the ``~/.local/bin`` Git-Bash
    variant (``which`` is not a command there, so the explicit candidates cover it).

    Args:
        user_home: Path to the user's home directory.

    Returns:
        Absolute path to the ``cursor-agent`` binary as a string, or None.
    """
    if platform.system() == "Windows":
        # Existence-gated, NOT os.access(X_OK): on Windows X_OK is True for any
        # file, so it can't distinguish a binary (mirrors the Gemini/Claude
        # Windows branches). All candidates are user_home-relative, so they are
        # correctly scoped to this user even under an admin/MDM scan.
        install_dir = user_home / "AppData" / "Local" / "cursor-agent"
        candidates = [
            # Native Windows installer (irm 'https://cursor.com/install?win32=true'
            # | iex) drops cursor-agent.{exe,cmd,ps1} and agent.{exe,cmd,ps1} at
            # the root of %LOCALAPPDATA%\cursor-agent.
            install_dir / "cursor-agent.exe",
            install_dir / "cursor-agent.cmd",
            install_dir / "agent.exe",
            install_dir / "agent.cmd",
            # Git-Bash variant: the Unix installer run under MINGW64 drops an
            # extensionless symlink into ~/.local/bin.
            user_home / ".local" / "bin" / "cursor-agent",
            user_home / ".local" / "bin" / "cursor-agent.exe",
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except (PermissionError, OSError):
                continue

        # The native installer keeps the real binary under a versioned subdir
        # (%LOCALAPPDATA%\cursor-agent\versions\<v>\cursor-agent.exe); pick the
        # newest by numeric version (same key as the POSIX branch) so a stale
        # older build isn't reported.
        versions_dir = install_dir / "versions"
        try:
            if versions_dir.exists():
                version_dirs = sorted(
                    (d for d in versions_dir.iterdir() if d.is_dir()),
                    key=_cursor_agent_version_key,
                    reverse=True,
                )
                for version_dir in version_dirs:
                    versioned = version_dir / "cursor-agent.exe"
                    try:
                        if versioned.exists():
                            return str(versioned)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not enumerate Windows cursor-agent versions: {e}")
        return None

    # POSIX (macOS / Linux). All candidates below are user_home-relative, so they
    # are correctly scoped to this user even under a root/MDM multi-user scan.
    candidates = [
        user_home / ".local" / "bin" / "cursor-agent",
        user_home / ".local" / "bin" / "agent",
    ]
    for candidate in candidates:
        try:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                return str(candidate)
        except (PermissionError, OSError):
            continue

    # Versioned install dir: pick the newest version's cursor-agent.
    versions_dir = user_home / ".local" / "share" / "cursor-agent" / "versions"

    try:
        if versions_dir.exists():
            version_dirs = sorted(
                (d for d in versions_dir.iterdir() if d.is_dir()),
                key=_cursor_agent_version_key,
                reverse=True,
            )
            for version_dir in version_dirs:
                versioned = version_dir / "cursor-agent"
                try:
                    if versioned.exists() and os.access(str(versioned), os.X_OK):
                        return str(versioned)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError) as e:
        logger.debug(f"Could not enumerate cursor-agent versions: {e}")

    # npm-global prefix backstop (Homebrew node / nvm / pnpm vary). The dynamic
    # ``npm prefix -g`` probe is root-guarded inside the helper (it resolves the
    # SCANNER's prefix, not the user's — the 93b5fc2 cross-user FP class).
    npm_resolved = resolve_npm_global_tool_bin(
        "cursor-agent", user_home, is_running_as_root()
    )
    if npm_resolved:
        return npm_resolved

    # PATH backstop: only meaningful single-user / non-root (the resolved PATH is
    # the SCANNER's, not user_home's; under root it would mis-attribute the
    # scanner's cursor-agent to a user who has only residue).
    if not is_running_as_root():
        which_path = run_command(["which", "cursor-agent"], VERSION_TIMEOUT)
        if which_path:
            try:
                resolved = Path(which_path)
                if resolved.exists() and os.access(str(resolved), os.X_OK):
                    return str(resolved)
            except (PermissionError, OSError):
                pass

    return None
