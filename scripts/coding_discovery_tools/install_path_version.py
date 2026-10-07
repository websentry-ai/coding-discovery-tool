"""
Recover a CLI's version from where it is installed, without running it.

Discovery runs as root under MDM, so ``<tool> --version`` on a user-writable
binary is refused (``_is_safe_exec_path``) and PATH lookups resolve the
scanner's PATH, not the user's. Most installs still say their version on disk:
the npm package, the Homebrew keg, the native installer's ``versions/``
directory, or the editor extension folder. Only link targets, directory names
and user-owned regular files are read; nothing is executed.
"""

import json
import logging
import os
import platform
import re
import stat
from pathlib import Path
from typing import Optional

from .constants import MAX_CONFIG_FILE_SIZE
from .utils import _is_safe_exec_path, _is_symlink_or_reparse, _read_own_regular_file, extract_version_number

logger = logging.getLogger(__name__)

_MAX_LINK_HOPS = 16
_MAX_CACHE_ENTRIES = 256
_UNKNOWN_VERSIONS = {"", "unknown"}

# Matched against the resolved path with forward slashes, only when the path is not
# inside an npm package (a keg there is node's, e.g. Cellar/node/22.11.0/lib/node_modules).
# A runtime's keg (Cellar/node/22.11.0/bin/gemini) carries the runtime's version.
_RUNTIME_KEGS = {"node", "python", "ruby", "deno", "bun", "go", "openjdk"}
_KEG = re.compile(r"/(?:Caskroom|Cellar)/([^/]+)/(\d+(?:\.\d+)+[^/]*)/")
_PATH_VERSION_PATTERNS = (
    # Native installers keep one entry per version: ~/.local/share/claude/versions/2.0.14,
    # AppData/Local/cursor-agent/versions/2026.05.28-7ae6800/cursor-agent.exe. Scoped to
    # those roots so pyenv-style ~/.pyenv/versions/3.12.1 never matches.
    re.compile(r"/(?:\.local/share|AppData/Local)/[^/]+/versions/(\d+(?:\.\d+)+[^/]*)(?:/|$)",
               re.IGNORECASE),
    # Editor extension folder: .../extensions/anthropic.claude-code-2.0.5-darwin-arm64[/...]
    re.compile(r"/extensions/[a-z0-9-]+\.[a-z0-9-]+-(\d+\.\d+\.\d+[^/]*)(?:/|$)", re.IGNORECASE),
)

# Self-updating CLIs run the newest runtime they downloaded rather than the npm
# package's version: Copilot CLI keeps one directory per runtime under
# <root>/<platform>/<version>/.
_SELF_UPDATE_ROOTS = {
    "@github/copilot": (
        Path(".copilot/pkg"),
        Path("Library/Caches/copilot/pkg"),
        Path(".cache/copilot/pkg"),
        Path("AppData/Local/copilot/pkg"),
    ),
}
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)")  # a prefix: 1.0.70-rc.1 compares as 1.0.70
# What a version may look like once reported: these names come from user-writable
# folders and files, and the value is rendered in the dashboard.
_CLEAN_VERSION = re.compile(r"\d+(?:\.\d+)+(?:[-+][0-9A-Za-z.]+)?")
_MAX_VERSION_LENGTH = 64

# An npm .cmd / .ps1 shim names the package it runs: "%dp0%\node_modules\@openai\codex\bin\codex.js".
_SHIM_PACKAGE = re.compile(r"node_modules[\\/]((?:@[^\\/\"']+[\\/])?[^\\/\"']+)[\\/]")
# A node_modules/.bin shim names its package relative to .bin: "%dp0%\..\@scope\pkg\cli.js".
_BIN_SHIM_PACKAGE = re.compile(r"%dp0%[\\/]\.\.[\\/]((?:@[^\\/\"']+[\\/])?[^\\/\"']+)[\\/]")


def is_unknown_version(version) -> bool:
    return not isinstance(version, str) or version.strip().lower() in _UNKNOWN_VERSIONS


def version_from_install_path(install_path, user_home: Path) -> Optional[str]:
    """The version recorded by the install at ``install_path``, or None."""
    try:
        path = Path(install_path)
        resolved = _resolve_links(path)
        package_dir = _npm_package_dir(resolved) or _shim_package_dir(path, user_home)
        name = version = None
        if package_dir is not None:
            name, version = _package_json(package_dir, user_home)
            name = name or _package_name(package_dir)
        version = _clean(version) or _version_from_path(resolved)
        if name:
            version = _newest_self_update(name, user_home, version)
        return version
    except (OSError, ValueError) as e:
        logger.debug(f"Could not read a version from {install_path}: {e}")
    return None


def _version_from_path(resolved: Path) -> Optional[str]:
    posix = str(resolved).replace("\\", "/")
    keg = _KEG.search(posix)
    if keg:
        if keg.group(1).split("@")[0].lower() in _RUNTIME_KEGS:
            return None
        return _clean(extract_version_number(keg.group(2)))
    for pattern in _PATH_VERSION_PATTERNS:
        match = pattern.search(posix)
        if match:
            return _clean(extract_version_number(match.group(1)))
    return None


def _clean(version) -> Optional[str]:
    """A version safe to report, or None: plain x.y[.z][-pre], at most 64 chars."""
    if not isinstance(version, str) or len(version) > _MAX_VERSION_LENGTH:
        return None
    version = version.strip()
    return version if _CLEAN_VERSION.fullmatch(version) else None


def _resolve_links(path: Path) -> Path:
    """Follow the symlink chain by reading link targets only (never opening them)."""
    current = Path(os.path.abspath(str(path)))
    for _ in range(_MAX_LINK_HOPS):
        try:
            target = os.readlink(str(current))
        except OSError:
            return current
        current = Path(os.path.normpath(os.path.join(str(current.parent), target)))
    return current


def _npm_package_dir(resolved: Path) -> Optional[Path]:
    """The npm package directory containing ``resolved``, if it sits in node_modules."""
    for ancestor in resolved.parents:
        parent = ancestor.parent
        if parent.name == "node_modules" and not ancestor.name.startswith(("@", ".")):
            return ancestor
        if parent.parent.name == "node_modules" and parent.name.startswith("@"):
            return ancestor
    return None


def _package_name(package_dir: Path) -> str:
    """The npm name implied by the path: node_modules/@scope/pkg or node_modules/pkg."""
    parent = package_dir.parent
    return f"{parent.name}/{package_dir.name}" if parent.name.startswith("@") else package_dir.name


def _shim_package_dir(shim: Path, user_home: Path) -> Optional[Path]:
    """The package an npm Windows shim (.cmd / .ps1) runs, from the shim's text."""
    if shim.suffix.lower() not in (".cmd", ".ps1"):
        return None
    text = _read_own_regular_file(shim, user_home, MAX_CONFIG_FILE_SIZE) or ""
    if shim.parent.name == ".bin" and shim.parent.parent.name == "node_modules":
        match, base = _BIN_SHIM_PACKAGE.search(text), shim.parent.parent
    else:
        match, base = _SHIM_PACKAGE.search(text), shim.parent / "node_modules"
    if not match:
        return None
    return base.joinpath(*re.split(r"[\\/]", match.group(1)))


def _read_package_file(path: Path, user_home: Path) -> Optional[str]:
    """The user's own file, or a root-owned one no other account can have planted
    (every folder above it root-owned and not group/world-writable): a shared
    Homebrew or system npm prefix."""
    text = _read_own_regular_file(path, user_home, MAX_CONFIG_FILE_SIZE)
    if text is not None or os.name == "nt" or not _is_safe_exec_path(str(path)):
        return text
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(str(path), flags)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_CONFIG_FILE_SIZE:
            return None
        return os.read(fd, MAX_CONFIG_FILE_SIZE).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _package_json(package_dir: Path, user_home: Path):
    """(name, version) from the package's package.json, either may be None."""
    raw = _read_package_file(package_dir / "package.json", user_home)
    try:
        data = json.loads(raw) if raw else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return None, None
    name, version = data.get("name"), data.get("version")
    return (name if isinstance(name, str) else None,
            version if isinstance(version, str) and version.strip() else None)


def _platform_dir() -> str:
    """The <os>-<arch> folder name a self-updating CLI uses for this machine."""
    system = {"darwin": "darwin", "linux": "linux", "windows": "win32"}.get(platform.system().lower(), "")
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64" if machine in ("x86_64", "amd64") else machine
    return f"{system}-{arch}"


def _any_redirect(base: Path, path: Path) -> bool:
    """True if any component of ``path`` below ``base`` is missing or a redirect."""
    node = base
    for part in path.relative_to(base).parts:
        node = node / part
        if _is_symlink_or_reparse(node):
            return True
    return False


def _newest_self_update(package_name, user_home: Path, installed: Optional[str]) -> Optional[str]:
    """The newest runtime a self-updating CLI downloaded for this machine's platform,
    when newer than the package. Every component is refused if it is a redirect, and
    the listing is capped, since the scan reads a user-controlled tree as root."""
    best = _SEMVER.match(installed or "")
    best_key = tuple(int(n) for n in best.groups()) if best else None
    result = installed
    for root in _SELF_UPDATE_ROOTS.get(package_name, ()):
        runtime_dir = user_home / root / _platform_dir()
        if _any_redirect(user_home, runtime_dir):
            continue
        try:
            with os.scandir(runtime_dir) as entries:
                for count, entry in enumerate(entries):
                    if count >= _MAX_CACHE_ENTRIES:
                        break
                    match = _SEMVER.match(entry.name)
                    if not match or not _clean(entry.name) or not entry.is_dir(follow_symlinks=False):
                        continue
                    key = tuple(int(n) for n in match.groups())
                    if best_key is None or key > best_key:
                        best_key, result = key, entry.name
        except OSError:
            continue
    return result
