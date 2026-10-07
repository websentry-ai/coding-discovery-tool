"""
Recover a CLI's version from where it is installed, without running it.

Discovery runs as root under MDM, so ``<tool> --version`` on a user-writable
binary is refused (``_is_safe_exec_path``) and PATH lookups resolve the
scanner's PATH, not the user's. Most installs still say their version on disk:
the Homebrew keg, the native installer's ``versions/`` directory, the editor
extension folder, or the npm package's ``package.json``. Only link targets and
user-owned regular files are read; nothing is executed.
"""

import json
import logging
import os
import re
from pathlib import Path
from typing import Optional

from .constants import MAX_CONFIG_FILE_SIZE
from .utils import _read_own_regular_file

logger = logging.getLogger(__name__)

_MAX_LINK_HOPS = 16
_UNKNOWN_VERSIONS = {"", "unknown"}

# Matched against the resolved path with forward slashes.
_PATH_VERSION_PATTERNS = (
    # Homebrew cask / formula keg: .../Caskroom/codex/0.139.0/bin/codex
    re.compile(r"/(?:Caskroom|Cellar)/[^/]+/(\d+(?:\.\d+)+[^/]*)/"),
    # Native installers keep one dir or file per version under ~/.local/share/<tool>/versions:
    # claude -> versions/2.0.14, cursor-agent -> versions/2025.09.18-7ae6800/cursor-agent.
    # Scoped to .local/share so pyenv-style ~/.pyenv/versions/3.12.1 never matches.
    re.compile(r"/\.local/share/[^/]+/versions/(\d+(?:\.\d+)+[^/]*)(?:/|$)"),
    # Editor extension folder: .../extensions/anthropic.claude-code-2.0.5-darwin-arm64/...
    re.compile(r"/extensions/[a-z0-9-]+\.[a-z0-9-]+-(\d+\.\d+\.\d+)(?:-[a-z0-9_-]+)?/", re.IGNORECASE),
)

# Self-updating CLIs run the newest runtime they downloaded, not the npm package's
# version: Copilot CLI keeps one directory per runtime under <cache>/copilot/pkg/<platform>/.
_SELF_UPDATE_CACHES = {
    "@github/copilot": (Path("Library/Caches/copilot/pkg"), Path(".cache/copilot/pkg")),
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
        posix = str(resolved).replace("\\", "/")
        for pattern in _PATH_VERSION_PATTERNS:
            match = pattern.search(posix)
            if match:
                return match.group(1)
        package_dir = _npm_package_dir(resolved) or _shim_package_dir(path, user_home)
        if package_dir is not None:
            name, version = _package_json(package_dir, user_home)
            return _newest_self_update(name, user_home, version)
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


def _newest_self_update(package_name, user_home: Path, installed: Optional[str]) -> Optional[str]:
    """The newest runtime a self-updating CLI downloaded, when newer than the package."""
    best = _SEMVER.match(installed or "")
    best_key = tuple(int(n) for n in best.groups()) if best else None
    result = installed
    for cache in _SELF_UPDATE_CACHES.get(package_name, ()):
        try:
            platforms = [p for p in (user_home / cache).iterdir() if p.is_dir() and not p.is_symlink()]
        except OSError:
            continue
        for platform_dir in platforms:
            try:
                names = [p.name for p in platform_dir.iterdir() if p.is_dir() and not p.is_symlink()]
            except OSError:
                continue
            for name in names:
                match = _SEMVER.match(name)
                key = tuple(int(n) for n in match.groups()) if match else None
                if key and (best_key is None or key > best_key):
                    best_key, result = key, name
    return result
