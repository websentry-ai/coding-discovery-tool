"""
User-specific tool detection module.

This module handles detection of tools that are installed per-user, checking
user-specific paths like ~/.nvm, ~/.bun, and user configuration directories.
"""

import json
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

    # Junie detection
    elif tool_name == "junie":
        return _detect_junie(detector, user_home)

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

    Gates on the binary, not ``~/.cursor/cli-config.json`` — the Cursor IDE also
    writes ``~/.cursor`` and it survives a CLI uninstall, so gating on it produced
    false positives.
    """
    cursor_agent_bin = find_cursor_agent_binary_for_user(user_home)
    if cursor_agent_bin:
        return {
            "name": detector.tool_name,
            # Probe the resolved binary directly: get_version() with no arg runs
            # against the scanner's PATH, which under a root/MDM scan lacks the
            # user's cursor-agent -> "Unknown" for every user.
            "version": detector.get_version(cursor_agent_bin) or "Unknown",
            "install_path": cursor_agent_bin
        }

    return None


def _detect_claude_cowork(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Claude Cowork installation for a user.

    Requires BOTH the on-disk Cowork sessions tree AND a present Claude Desktop
    install. The per-user Claude config tree (which holds the sessions dir)
    survives uninstall (anthropics/claude-code#25013), so on Linux/Windows
    gating on the sessions dir alone produced false positives. macOS already
    AND-requires ``/Applications/Claude.app``; Linux/Windows now AND-require an
    install dir resolved by the OS detector's ``_find_install_dir`` (keeping the
    install-dir candidate lists in the OS modules — one source of truth).
    """
    system = platform.system()
    if system == "Darwin":
        app_path = Path("/Applications/Claude.app")
        try:
            if not app_path.exists():
                return None
        except OSError:
            return None
        sessions_dir = user_home / "Library" / "Application Support" / "Claude" / COWORK_SESSIONS_DIR
        require_install_dir = False
    elif system == "Linux":
        sessions_dir = user_home / ".config" / "Claude" / COWORK_SESSIONS_DIR
        require_install_dir = True
    else:
        sessions_dir = user_home / "AppData" / "Roaming" / "Claude" / COWORK_SESSIONS_DIR
        require_install_dir = True

    try:
        if not (sessions_dir.exists() and sessions_dir.is_dir()):
            return None
    except (PermissionError, OSError):
        return None

    if require_install_dir:
        find_install_dir = getattr(detector, "_find_install_dir", None)
        if not callable(find_install_dir):
            return None
        try:
            if find_install_dir() is None:
                return None
        except (PermissionError, OSError):
            return None

    return {
        "name": detector.tool_name,
        "version": detector.get_version(),
        "install_path": str(sessions_dir)
    }


def _junie_version_from_config(user_home: Path) -> Optional[str]:
    """Read the Junie version from ``~/.junie/config.json`` (or settings.json).

    ``~/.junie`` is residue and must NOT gate detection, but once Junie is
    confirmed via the binary/plugin it is still the authoritative version source.
    Best-effort: any read/parse error yields None ("Unknown" downstream).
    """
    junie_dir = user_home / ".junie"
    for name in ("config.json", "settings.json"):
        config_file = junie_dir / name
        try:
            if not config_file.exists():
                continue
            with open(config_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and isinstance(data.get("version"), str):
                return data["version"]
        except (json.JSONDecodeError, OSError, PermissionError) as e:
            logger.debug(f"Could not read Junie config file {config_file}: {e}")
            continue
    return None


def _detect_junie(detector: BaseToolDetector, user_home: Path) -> Optional[Dict]:
    """Detect Junie installation for a user.

    Gates on a real install signal — the Junie CLI **binary** OR the **Junie
    plugin** present in a JetBrains IDE — not on the ``~/.junie`` directory.
    ``~/.junie`` is a user-authored guidelines dir (AGENTS.md / config.json):
    it survives uninstall AND is created by usage rather than install, so
    gating on it produced false positives. ``~/.junie`` remains the version
    source and the rules/MCP extraction source.

    The JetBrains plugin check is delegated to the OS detector's
    ``_has_junie_jetbrains_plugin`` (keeps the OS-specific JetBrains detector
    choice in the OS module, mirroring the ``_find_install_dir`` delegation used
    for Claude Cowork).
    """
    junie_bin = find_junie_binary_for_user(user_home)
    if junie_bin:
        return {
            "name": detector.tool_name,
            "version": _junie_version_from_config(user_home) or "Unknown",
            "install_path": junie_bin,
        }

    plugin_check = getattr(detector, "_has_junie_jetbrains_plugin", None)
    if callable(plugin_check):
        try:
            plugin_path = plugin_check(user_home)
        except (PermissionError, OSError) as e:
            logger.debug(f"Junie JetBrains plugin check failed for {user_home}: {e}")
            plugin_path = None
        if plugin_path:
            return {
                "name": detector.tool_name,
                "version": _junie_version_from_config(user_home) or "Unknown",
                "install_path": plugin_path,
            }

    return None


def find_junie_binary_for_user(user_home: Path) -> Optional[str]:
    """Find the absolute path to the ``junie`` CLI binary for a specific user.

    Mirrors ``find_claude_binary_for_user``. Junie's CLI ships a shim at
    ``~/.local/bin/junie`` plus versioned builds under
    ``~/.local/share/junie/versions/<v>/junie``, and is also installable via
    Homebrew, Bun and npm-global. Machine-global candidates (Homebrew,
    /usr/local) are owner-attributed under a root/MDM scan so one user's install
    is not fanned out to every user (the 93b5fc2 cross-user FP class). Never
    raises.

    Args:
        user_home: Path to the user's home directory.

    Returns:
        Absolute path to the junie binary as a string, or None if not found.
    """
    if platform.system() == "Windows":
        # Existence-gated, not os.access(X_OK): on Windows X_OK is True for any
        # file, so it cannot distinguish a binary.
        npm_dir = user_home / "AppData" / "Roaming" / "npm"
        candidates = [
            user_home / ".local" / "bin" / "junie.exe",
            user_home / ".local" / "bin" / "junie",
            npm_dir / "junie.cmd",
            npm_dir / "junie.exe",
            user_home / ".bun" / "bin" / "junie.exe",
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    return str(candidate)
            except (PermissionError, OSError):
                continue

        # Native installer keeps the real binary under a versioned subdir; pick
        # the newest version so a stale older build isn't reported.
        versions_dir = user_home / ".local" / "share" / "junie" / "versions"
        try:
            if versions_dir.exists():
                version_dirs = sorted(
                    (d for d in versions_dir.iterdir() if d.is_dir()),
                    key=_cursor_agent_version_key,
                    reverse=True,
                )
                for version_dir in version_dirs:
                    versioned = version_dir / "junie.exe"
                    try:
                        if versioned.exists():
                            return str(versioned)
                    except (PermissionError, OSError):
                        continue
        except (PermissionError, OSError) as e:
            logger.debug(f"Could not enumerate Windows junie versions: {e}")
        return None

    # POSIX (macOS / Linux).
    user_relative = [
        user_home / ".local" / "bin" / "junie",        # Official installer shim
        user_home / ".bun" / "bin" / "junie",          # Bun global install
        user_home / ".npm-global" / "bin" / "junie",   # npm global prefix
    ]
    # Homebrew and /usr/local are MACHINE-GLOBAL — always probed, but under a
    # root/MDM multi-user scan each is owner-attributed (the loop below) so one
    # user's install isn't fanned out to every user.
    machine_global = [
        Path("/opt/homebrew/bin/junie"),   # Apple Silicon Homebrew
        Path("/usr/local/bin/junie"),      # Intel Mac / manual install
    ]

    is_root = is_running_as_root()
    for candidate in machine_global + user_relative:
        try:
            if candidate.exists() and os.access(str(candidate), os.X_OK):
                if is_root and candidate in machine_global \
                        and not machine_global_binary_owned_by_user(candidate, user_home):
                    continue
                return str(candidate)
        except (PermissionError, OSError):
            continue

    # Versioned install dir: pick the newest version's junie.
    versions_dir = user_home / ".local" / "share" / "junie" / "versions"
    try:
        if versions_dir.exists():
            version_dirs = sorted(
                (d for d in versions_dir.iterdir() if d.is_dir()),
                key=_cursor_agent_version_key,
                reverse=True,
            )
            for version_dir in version_dirs:
                versioned = version_dir / "junie"
                try:
                    if versioned.exists() and os.access(str(versioned), os.X_OK):
                        return str(versioned)
                except (PermissionError, OSError):
                    continue
    except (PermissionError, OSError) as e:
        logger.debug(f"Could not enumerate junie versions: {e}")

    # npm-global prefix backstop. Root-guarded inside the helper: ``npm prefix
    # -g`` resolves the scanner's prefix, not the user's.
    npm_resolved = resolve_npm_global_tool_bin("junie", user_home, is_running_as_root())
    if npm_resolved:
        return npm_resolved

    # PATH backstop: non-root only — under root ``which`` resolves the scanner's
    # PATH, mis-attributing its junie to a user who has only residue.
    if not is_running_as_root():
        which_path = run_command(["which", "junie"], VERSION_TIMEOUT)
        if which_path:
            try:
                resolved = Path(which_path)
                if resolved.exists() and os.access(str(resolved), os.X_OK):
                    return str(resolved)
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
    """Numeric (major, minor, patch) key for a "X.Y.Z" version-dir name.

    A string sort would order "1.10.0" before "1.9.0" and report a stale version;
    malformed names yield () and sort earliest.
    """
    return tuple(int(p) for p in version_dir.name.split(".") if p.isdigit())


def find_cursor_agent_binary_for_user(user_home: Path) -> Optional[str]:
    """Find the absolute path to the ``cursor-agent`` binary for a user.

    Mirrors ``find_claude_binary_for_user``. Checks the per-user installer
    locations and versioned dirs, then npm-global and a PATH backstop — both
    root-guarded, since under a root/MDM scan they resolve the scanner's PATH,
    not ``user_home``'s, and would mis-attribute the install.

    Args:
        user_home: Path to the user's home directory.

    Returns:
        Absolute path to the ``cursor-agent`` binary as a string, or None.
    """
    if platform.system() == "Windows":
        # Existence-gated, not os.access(X_OK): on Windows X_OK is True for any
        # file, so it can't distinguish a binary.
        install_dir = user_home / "AppData" / "Local" / "cursor-agent"
        candidates = [
            # Native Windows installer drops these at the root of the install dir.
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

        # Native installer keeps the real binary under a versioned subdir; pick the
        # newest version so a stale older build isn't reported.
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

    # POSIX (macOS / Linux). Older builds installed a bare ``agent`` shim.
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

    # npm-global prefix backstop. Root-guarded inside the helper: ``npm prefix -g``
    # resolves the scanner's prefix, not the user's.
    npm_resolved = resolve_npm_global_tool_bin(
        "cursor-agent", user_home, is_running_as_root()
    )
    if npm_resolved:
        return npm_resolved

    # PATH backstop: non-root only — under root ``which`` resolves the scanner's
    # PATH, mis-attributing its cursor-agent to a user who has only residue.
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
