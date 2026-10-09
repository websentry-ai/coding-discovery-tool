"""Recover a CLI's version from where it is installed, since a root MDM scan can't run user binaries.
Only link targets, folder names and user-owned files are read; nothing is executed."""

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

_MAX_LINK_HOPS = 40
_MAX_CACHE_ENTRIES = 256
_UNKNOWN_VERSIONS = {"", "unknown"}

# Read only outside an npm package: a runtime's keg (Cellar/node/22.11.0/bin/gemini) holds the
# runtime's version, not the tool's.
_RUNTIME_KEGS = {"node", "python", "ruby", "deno", "bun", "go", "openjdk"}
_KEG = re.compile(r"/(?:Caskroom|Cellar)/([^/]+)/(\d+(?:\.\d+)+[^/]*)/")
_PATH_VERSION_PATTERNS = (
    # Native installers' versions/<v> (~/.local/share/claude/versions/2.0.14), scoped to those
    # roots so ~/.pyenv/versions/3.12.1 never matches.
    re.compile(r"/(?:\.local/share|AppData/Local)/[^/]+/versions/(\d+(?:\.\d+)+[^/]*)(?:/|$)",
               re.IGNORECASE),
    # Editor extension folder: .../extensions/anthropic.claude-code-2.0.5-darwin-arm64[/...]
    re.compile(r"/extensions/[a-z0-9-]+\.[a-z0-9-]+-(\d+\.\d+\.\d+[^/]*)(?:/|$)", re.IGNORECASE),
)

# Self-updating CLIs run the newest runtime they downloaded, kept under <root>/<platform>/<version>/,
# not the npm package's version.
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
_CLEAN_VERSION = re.compile(r"\d+(?:\.\d+)+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?")
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
        resolved, package_dir = _locate(install_path, user_home)
        version = _package_json(package_dir, user_home)[1] if package_dir is not None else None
        return (_clean(version) or _version_from_path(resolved)
                or _version_from_path(_follow_leaf(install_path)))
    except (OSError, ValueError) as e:
        logger.debug(f"Could not read a version from {install_path}: {e}")
    return None


def is_self_updating(install_path, user_home: Path) -> bool:
    """Whether the install is a CLI that runs runtimes it downloads per user."""
    try:
        _, package_dir = _locate(install_path, user_home)
        return package_dir is not None and (
            _package_json(package_dir, user_home)[0] or _package_name(package_dir)) in _SELF_UPDATE_ROOTS
    except (OSError, ValueError):
        return False


def runtime_version(install_path, user_home: Path) -> Optional[str]:
    """What a self-updating CLI runs for this user: the newer of its package (when this
    user can read it) and the runtimes they downloaded. None for any other CLI."""
    try:
        _, package_dir = _locate(install_path, user_home)
        if package_dir is None:
            return None
        name, version = _package_json(package_dir, user_home)
        name = name or _package_name(package_dir)
        if name not in _SELF_UPDATE_ROOTS:
            return None
        return _newest_self_update(name, user_home, _clean(version)) or _clean(version)
    except (OSError, ValueError) as e:
        logger.debug(f"Could not read a runtime for {install_path}: {e}")
    return None


def newest_version(a, b):
    """The newer of two versions; one that doesn't parse loses, and a tie keeps ``a``."""
    key_a, key_b = _version_key(a), _version_key(b)
    return b if key_b is not None and (key_a is None or key_b > key_a) else a


def _version_key(version):
    """A semver precedence key (a release above its prereleases, which compare by
    identifier, numbers numerically), or None."""
    if not isinstance(version, str):
        return None
    match = _SEMVER.match(version) or _SEMVER.match(extract_version_number(version) or "")
    if not match:
        return None
    rest = match.string[match.end():]
    pre = rest[1:].split("+")[0].split(".") if rest.startswith("-") else []
    ids = tuple((0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre)
    return tuple(int(n) for n in match.groups()) + (0 if pre else 1, ids)


def _locate(install_path, user_home: Path):
    """(the resolved binary, the npm package that provides it or None)."""
    path = Path(install_path)
    resolved = _resolve_links(path)
    return resolved, _npm_package_dir(resolved) or _shim_package_dir(path, user_home)


def _version_from_path(resolved: Path) -> Optional[str]:
    posix = str(resolved).replace("\\", "/")
    keg = _KEG.search(posix)
    if keg:
        if keg.group(1).split("@")[0].lower() in _RUNTIME_KEGS:
            return None
        folder = re.sub(r"(_\d+|,[0-9A-Za-z.-]+)$", "", keg.group(2))  # a brew revision or cask build
        return _clean(folder) or _clean(extract_version_number(folder))  # keeps a prerelease
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


def _follow_leaf(path) -> Path:
    """Only the final link chain followed, keeping a native installer's logical
    .local/share/<tool>/versions/<v> even when a folder above it is a symlink."""
    current = Path(os.path.abspath(str(path)))
    for _ in range(_MAX_LINK_HOPS):
        try:
            target = os.readlink(str(current))
        except OSError:
            break
        current = Path(os.path.normpath(os.path.join(str(current.parent), target)))
    return current


def _resolve_links(path: Path) -> Path:
    """The path with every symlink in it followed, folders included (Homebrew's
    opt/<tool> -> Cellar/<tool>/<version>), by reading link targets only."""
    parts = list(Path(os.path.abspath(str(path))).parts)
    current, pending, hops = Path(parts[0]), parts[1:], 0
    while pending:
        part = pending.pop(0)
        if part == "..":
            current = current.parent  # current has no links left, so this is the real parent
            continue
        candidate = current / part
        try:
            target = Path(os.readlink(str(candidate)))
        except OSError:
            current = candidate
            continue
        hops += 1
        if hops > _MAX_LINK_HOPS:
            return candidate.joinpath(*pending)
        if target.is_absolute():
            current, pending = Path(target.parts[0]), list(target.parts[1:]) + pending
        else:
            pending = list(target.parts) + pending
    return current


def _npm_package_dir(resolved: Path) -> Optional[Path]:
    """The npm package directory containing ``resolved``, if it sits in node_modules."""
    for ancestor in ((resolved, *resolved.parents) if resolved.is_dir() else resolved.parents):
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
    parts = re.split(r"[\\/]", match.group(1)) if match else []
    if not parts or any(part in (".", "..") for part in parts):
        return None
    return base.joinpath(*parts)


def _read_package_file(path: Path, user_home: Path) -> Optional[str]:
    """The user's own file, or a world-readable root-owned one under root-owned folders nobody else can write
    (a shared Homebrew or npm prefix), never a root-private file."""
    text = _read_own_regular_file(path, user_home, MAX_CONFIG_FILE_SIZE)
    if text is not None or os.name == "nt":
        return text
    real = os.path.realpath(str(path))  # open what was checked, not a link that can move
    if not _is_safe_exec_path(real) or not _world_traversable(Path(real).parent):
        return None
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, os.geteuid())
                or not info.st_mode & stat.S_IROTH or info.st_size > MAX_CONFIG_FILE_SIZE):
            return None
        return os.read(fd, MAX_CONFIG_FILE_SIZE).decode("utf-8", errors="replace")
    finally:
        os.close(fd)


def _world_traversable(directory: Path) -> bool:
    """Whether every folder down to ``directory`` lets any user list into it."""
    try:
        return all(os.stat(d).st_mode & (stat.S_IROTH | stat.S_IXOTH) == stat.S_IROTH | stat.S_IXOTH
                   for d in (directory, *directory.parents))
    except OSError:
        return False


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
    """The newest runtime a self-updating CLI downloaded for this platform, when newer than ``installed``.
    Redirects are refused and listings capped, since root reads a user-controlled tree here."""
    best_key = _version_key(installed)
    result = None
    for root in _SELF_UPDATE_ROOTS.get(package_name, ()):
        try:
            names = _runtime_dirs(user_home, root / _platform_dir())
        except OSError:
            continue
        for name in names:
            key = _version_key(name) if _SEMVER.match(name) and _clean(name) else None
            if key is None:
                continue
            if best_key is None or key > best_key:
                best_key, result = key, name
    return result


def _runtime_dirs(user_home: Path, rel: Path) -> list:
    """Up to the cap of real subdirectory names in user_home/rel, refusing a redirect anywhere below the home.
    On POSIX each directory stays open, so one swapped for a link mid-walk is refused, not followed."""
    if os.name == "nt":
        if _any_redirect(user_home, user_home / rel):
            return []
        with os.scandir(user_home / rel) as entries:
            return [e.name for _, e in zip(range(_MAX_CACHE_ENTRIES), entries) if e.is_dir(follow_symlinks=False)]
    fd = os.open(str(user_home), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in rel.parts:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        with os.scandir(fd) as entries:
            return [e.name for _, e in zip(range(_MAX_CACHE_ENTRIES), entries) if e.is_dir(follow_symlinks=False)]
    finally:
        os.close(fd)
