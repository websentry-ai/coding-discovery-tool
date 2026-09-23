"""Symlink-safe, containment-checked reading of workspace rule files.

Shared by the GitHub Copilot rules extractors. The .github walk follows redirects,
so a root/MDM scan must not read a rule file whose target escapes its containment
root (another user's file). Two scopes:

  * Project/workspace reads are STRICT — resolved one component at a time from a
    handle on the project root with ``O_NOFOLLOW`` (``openat``), so no symlinked
    component, intermediate or final, is ever followed and the opened file cannot
    escape the root. Containment holds by construction, not by re-walking a name.
  * User-scope global reads (the user's own ``~/.copilot/instructions`` /
    ``~/.claude/rules`` / VS Code User prompts) MAY follow a symlink (so a dotfile
    manager works), so they open the path and then contain the OPENED descriptor's
    kernel path to the user's home; the ``st_uid`` owner check is the real backstop.

Windows has no per-component ``openat`` and cannot ``os.open`` a directory, so it
opens the file and contains the handle's real path (``GetFinalPathNameByHandle``).
The walks separately refuse to descend a symlinked/junctioned directory before
reaching this reader.
"""

import logging
import os
import platform
import stat
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from .constants import MAX_CONFIG_FILE_SIZE

logger = logging.getLogger(__name__)


def _fd_real_path(fd: int) -> Optional[str]:
    """The kernel's own path for an open fd, read from the descriptor so a later path
    swap can't change it, or None. Never re-walked with ``realpath``: Linux ``readlink``
    of the fd magic-link, macOS ``F_GETPATH``, Windows ``GetFinalPathNameByHandle``."""
    system = platform.system()
    if system == "Windows":
        from .utils import _windows_final_path, _strip_extended_prefix
        final = _windows_final_path(fd)
        return _strip_extended_prefix(final) if final else None
    if system == "Darwin":
        try:
            import fcntl
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, b"\x00" * 1024)
            return raw.split(b"\x00", 1)[0].decode("utf-8", "replace") or None
        except (OSError, ValueError):
            return None
    try:
        return os.readlink("/proc/self/fd/%d" % fd)
    except OSError:
        return None


def _dir_real_path(root) -> Optional[str]:
    """The kernel's own path for directory ``root``, or None. POSIX takes it from an
    open directory handle so an ancestor swap can't forge it; Windows can't ``os.open``
    a directory, so it resolves the name (the file side is still handle-bound)."""
    if platform.system() == "Windows":
        from .utils import _strip_extended_prefix
        try:
            return _strip_extended_prefix(os.path.realpath(str(root)))
        except OSError:
            return None
    dir_fd = None
    try:
        dir_fd = os.open(str(root), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        return _fd_real_path(dir_fd)
    except OSError:
        return None
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass


def _fd_within_root(fd: int, root) -> bool:
    """True when the file the fd actually opened resolves inside ``root``. Both sides
    are handle/kernel paths compared after ``normpath`` only, never re-walked by name,
    so the check holds on Windows too (where ``O_NOFOLLOW`` and ``st_uid`` are inert)."""
    real = _fd_real_path(fd)
    base = _dir_real_path(root)
    if real is None or base is None:
        return False
    real_n = os.path.normcase(os.path.normpath(real))
    base_n = os.path.normcase(os.path.normpath(base))
    return real_n == base_n or real_n.startswith(base_n.rstrip(os.sep) + os.sep)


def _open_beneath_strict(rule_file, root, final_flags) -> Optional[int]:
    """POSIX strict open: descend each path component of ``rule_file`` from a handle on
    ``root`` with ``O_NOFOLLOW`` (``openat``), so no symlinked component — intermediate
    or final — is ever followed and the opened file cannot escape ``root`` (the
    RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS guarantee). Returns the file fd, or None on
    any symlink, escape, or error. Component-wise ``openat`` is used rather than raw
    ``openat2`` so it needs no ctypes and behaves the same on macOS and Linux."""
    try:
        rel = os.path.relpath(os.path.abspath(str(rule_file)), os.path.abspath(str(root)))
    except (OSError, ValueError):
        return None
    parts = [p for p in rel.split(os.sep) if p and p != os.curdir]
    if not parts or os.pardir in parts:  # empty, or escapes root with ".."
        return None
    o_nofollow = getattr(os, "O_NOFOLLOW", 0)
    o_directory = getattr(os, "O_DIRECTORY", 0)
    dir_fd = None
    try:
        dir_fd = os.open(str(root), os.O_RDONLY | o_directory)  # trusted anchor
        for comp in parts[:-1]:
            next_fd = os.open(comp, os.O_RDONLY | o_directory | o_nofollow, dir_fd=dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        return os.open(parts[-1], final_flags | o_nofollow, dir_fd=dir_fd)
    except OSError:
        return None
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass


def _open_contained(rule_file, root, allow_symlink) -> Optional[int]:
    """Open ``rule_file`` under a containment guarantee, or None. POSIX project scope
    walks per component from a root handle so containment holds by construction. User
    scope must follow a dotfile-manager symlink, and Windows has no per-component
    ``openat``, so both open the path then bind containment to the opened descriptor."""
    base_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
    if os.name == "posix" and not allow_symlink:
        return _open_beneath_strict(rule_file, root, base_flags)
    flags = base_flags | (0 if allow_symlink else getattr(os, "O_NOFOLLOW", 0))
    try:
        fd = os.open(str(rule_file), flags)
    except OSError:
        return None
    # User-scope POSIX is hardening, not fully race-free: it must follow the dotfile
    # symlink, so it can't walk per-component, and the root side of this check is still
    # name-resolved. The st_uid == root-owner check below is the real backstop against
    # cross-user disclosure here; project scope closes it by construction and Windows by
    # the handle path.
    if not _fd_within_root(fd, root):
        try:
            os.close(fd)
        except OSError:
            pass
        return None
    return fd


def read_rule_file_contained(
    rule_file: Path, containment_root, *, allow_symlink: bool = False
) -> Optional[Tuple[str, bool, int, str]]:
    """Read a rule file's text through the safe boundary, or None if refused.

    Returns ``(content, truncated, size, last_modified_iso)``. Containment is enforced
    on the OPENED file: ``allow_symlink=False`` (project/workspace) resolves it per
    component from the root with ``O_NOFOLLOW`` and refuses a multiply-linked file;
    ``allow_symlink=True`` (the user's own global rules) follows the link so a dotfile
    manager works, then contains the resolved descriptor to the home. The file must be
    a regular file owned by the root's owner. Never raises.
    """
    if containment_root is None:
        logger.info(f"Refusing rule file {rule_file}: no containment root")
        return None
    fd = None
    try:
        fd = _open_contained(rule_file, containment_root, allow_symlink)
        if fd is None:
            logger.info(f"Refusing rule file {rule_file}: not contained under {containment_root}")
            return None
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return None
        # Strict mode: a hard link (even same-uid) passes containment and the owner check; only nlink > 1 refuses it.
        if not allow_symlink and st.st_nlink > 1:
            logger.info(f"Refusing rule file {rule_file}: multiply-linked (nlink={st.st_nlink})")
            return None
        try:
            owner = os.stat(str(containment_root)).st_uid
        except OSError:
            return None
        # A file owned by a different uid is not this owner's, even in-tree.
        if st.st_uid != owner:
            logger.info(f"Refusing rule file {rule_file}: owned by uid {st.st_uid}, root owner differs")
            return None
        size = st.st_size
        truncated = size > MAX_CONFIG_FILE_SIZE
        last_modified = datetime.utcfromtimestamp(st.st_mtime).isoformat() + "Z"
        with os.fdopen(fd, "rb") as handle:
            fd = None
            data = handle.read(MAX_CONFIG_FILE_SIZE) if truncated else handle.read()
        return data.decode("utf-8", errors="replace"), truncated, size, last_modified
    except OSError as e:
        logger.debug(f"Could not read rule file {rule_file}: {e}")
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
