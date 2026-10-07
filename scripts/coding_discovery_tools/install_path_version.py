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
from pathlib import Path
from typing import Optional

from .constants import MAX_CONFIG_FILE_SIZE
from .utils import _is_symlink_or_reparse, _read_own_regular_file, extract_version_number

logger = logging.getLogger(__name__)

_MAX_LINK_HOPS = 16
_MAX_CACHE_ENTRIES = 256
_UNKNOWN_VERSIONS = {"", "unknown"}

# Matched against the resolved path with forward slashes, only when the path is not
# inside an npm package (a keg there is node's, e.g. Cellar/node/22.11.0/lib/node_modules).
_PATH_VERSION_PATTERNS = (
    # Homebrew cask / formula keg: .../Caskroom/codex/0.139.0/bin/codex
    re.compile(r"/(?:Caskroom|Cellar)/[^/]+/(\d+(?:\.\d+)+[^/]*)/"),
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
_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

# An npm .cmd / .ps1 shim names the package it runs: "%dp0%\node_modules\@openai\codex\bin\codex.js".
_SHIM_PACKAGE = re.compile(r"node_modules[\\/]((?:@[^\\/\"']+[\\/])?[^\\/\"']+)[\\/]")


def is_unknown_version(version) -> bool:
    return not isinstance(version, str) or version.strip().lower() in _UNKNOWN_VERSIONS


def version_from_install_path(install_path, user_home: Path) -> Optional[str]:
    """The version recorded by the install at ``install_path``, or None."""
    try:
        path = Path(install_path)
        resolved = _resolve_links(path)
        package_dir = _npm_package_dir(resolved) or _shim_package_dir(path, user_home)
        if package_dir is not None:
            name, version = _package_json(package_dir, user_home)
            return _newest_self_update(name or _package_name(package_dir), user_home, version)
        posix = str(resolved).replace("\\", "/")
        for pattern in _PATH_VERSION_PATTERNS:
            match = pattern.search(posix)
            if match:
                return extract_version_number(match.group(1))
    except (OSError, ValueError) as e:
        logger.debug(f"Could not read a version from {install_path}: {e}")
    return None


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
        if parent.name == "node_modules" and not ancestor.name.startswith("@"):
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
    text = _read_own_regular_file(shim, user_home, MAX_CONFIG_FILE_SIZE)
    match = _SHIM_PACKAGE.search(text or "")
    if not match:
        return None
    return shim.parent.joinpath("node_modules", *re.split(r"[\\/]", match.group(1)))


def _package_json(package_dir: Path, user_home: Path):
    """(name, version) from the package's package.json, either may be None."""
    raw = _read_own_regular_file(package_dir / "package.json", user_home, MAX_CONFIG_FILE_SIZE)
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
                    if not match or not entry.is_dir(follow_symlinks=False):
                        continue
                    key = tuple(int(n) for n in match.groups())
                    if best_key is None or key > best_key:
                        best_key, result = key, entry.name
        except OSError:
            continue
    return result
