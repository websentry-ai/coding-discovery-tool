"""Real-path containment checks for files attributed to a user on root scans.

Under a root/MDM scan the scanner can read every user's files, and each file it
reports is attributed to an owning home. A user can plant symlinks in their own
home (a redirected ``~/.cursor/plugins`` dir, a ``SKILL.md`` link) that point
into another user's home. Without a check, root reads the other user's file and
files it under the planter. These helpers accept a file only if it really lives
inside a boundary dir that really lives inside the owner's home. Symlinks that
stay inside the owner's own home (e.g. a dotfiles setup) still pass.

Stdlib only: this module runs on customer machines.
"""

import os
import stat
from pathlib import Path
from typing import Optional, Union

from .constants import is_symlink_or_junction
from .rule_read_helpers import _fd_real_path

PathLike = Union[str, Path]


class NotContainedError(PermissionError):
    """The file is outside its boundary, or is not a single-link regular file."""


def _real(path: PathLike) -> str:
    return os.path.normcase(os.path.realpath(str(path)))


def _inside(child_real: str, parent_real: str) -> bool:
    parent = parent_real.rstrip(os.sep)
    return child_real == parent or child_real.startswith(parent + os.sep)


def owned_boundary(owner_home: PathLike, boundary: PathLike) -> Optional[str]:
    """Real path of ``boundary`` if it resolves inside ``owner_home``, else None.

    None means the boundary dir (or one of its ancestors) is redirected out of
    the owner's home, so nothing under it can be attributed to that owner.
    """
    try:
        home_real = _real(owner_home)
        boundary_real = _real(boundary)
    except (OSError, ValueError):
        return None
    return boundary_real if _inside(boundary_real, home_real) else None


def is_within(path: PathLike, boundary_real: str) -> bool:
    """True if ``path`` resolves to a location inside ``boundary_real``."""
    try:
        return _inside(_real(path), boundary_real)
    except (OSError, ValueError):
        return False


def is_owned_file(path: PathLike, boundary: PathLike, owner_home: PathLike) -> bool:
    """True if ``path`` resolves inside ``boundary`` and ``boundary`` inside ``owner_home``."""
    boundary_real = owned_boundary(owner_home, boundary)
    return boundary_real is not None and is_within(path, boundary_real)


# Walking the real path with O_NOFOLLOW needs dir_fd (POSIX). Windows has no
# dir_fd or O_NOFOLLOW, and any user can create a directory junction, so on
# Windows the opened handle's final path is checked against the boundary instead.
_CAN_WALK = (
    os.name == "posix"
    and os.open in os.supports_dir_fd
    and hasattr(os, "O_DIRECTORY")
    and hasattr(os, "O_NOFOLLOW")
)


def _walk_open(file_real: str, leaf_flags: int) -> int:
    """Open ``file_real`` one component at a time, refusing any symlink.

    ``file_real`` is a realpath'd parent plus the file's own name, so it has no
    dir links when checked. Opening each component with O_NOFOLLOW relative to
    its parent's fd means a link swapped in after the check, or a linked file,
    fails the open (ELOOP/ENOTDIR) instead of being followed, so the file read
    is the file that was checked.
    """
    parts = Path(file_real).parts
    dir_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    dfd = os.open(parts[0], os.O_RDONLY | os.O_DIRECTORY)
    try:
        for comp in parts[1:-1]:
            nfd = os.open(comp, dir_flags, dir_fd=dfd)
            os.close(dfd)
            dfd = nfd
        return os.open(parts[-1], leaf_flags | os.O_NOFOLLOW, dir_fd=dfd)
    finally:
        os.close(dfd)


def _owner_uid(path: PathLike) -> int:
    return os.stat(str(path)).st_uid


def open_contained(path: PathLike, boundary_real: str) -> int:
    """Open ``path`` for reading only if it really lives inside ``boundary_real``.

    The parent dir is resolved (a dotfiles-style dir link inside the boundary is
    fine), but the file itself must not be a link: it is opened by its own name
    with O_NOFOLLOW, so a SKILL.md linked to e.g. ~/.ssh/id_ed25519 is refused.

    Returns a read-only fd for a regular, single-link file. Raises
    ``NotContainedError`` when the file is outside the boundary, is a link, is
    not a regular file, or has more than one hard link (a second link could be
    another user's file); other ``OSError`` when an open hits a swapped-in link.
    Read the content from the fd, never by path again.
    """
    path = Path(path)
    file_real = os.path.join(_real(path.parent), os.path.normcase(path.name))
    if file_real == boundary_real.rstrip(os.sep) or not _inside(file_real, boundary_real):
        raise NotContainedError(f"{path} resolves outside {boundary_real}")
    leaf_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
    if _CAN_WALK:
        try:
            fd = _walk_open(file_real, leaf_flags)
        except PermissionError:
            # A non-root user may lack read permission on an ancestor dir. Root
            # never does, and a non-root scan cannot reach other users' files.
            if hasattr(os, "geteuid") and os.geteuid() == 0:
                raise
            fd = os.open(file_real, leaf_flags | os.O_NOFOLLOW)
    else:
        # Symlink/junction tags only, so OneDrive cloud placeholders (other
        # reparse tags) stay readable.
        if is_symlink_or_junction(file_real):
            raise NotContainedError(f"{path} is a link")
        fd = os.open(file_real, leaf_flags | getattr(os, "O_BINARY", 0))
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink > 1:
            raise NotContainedError(f"{path} is not a single-link regular file")
        # A file owned by a different uid is not this owner's, even in-tree (same
        # rule as rule_read_helpers._open_checked; st_uid is 0 on Windows).
        if st.st_uid != _owner_uid(boundary_real):
            raise NotContainedError(f"{path} is owned by uid {st.st_uid}, not the boundary's owner")
        if not _CAN_WALK:
            # No component-wise open here, so check the file actually opened
            # (the kernel's path for the handle), and fail closed if unknown.
            final = _fd_real_path(fd)
            if final is None:
                raise NotContainedError(f"{path}: cannot verify the opened file's path")
            final = os.path.normcase(os.path.normpath(final))
            if not _inside(final, boundary_real) or os.path.basename(final) != os.path.normcase(path.name):
                raise NotContainedError(f"{path} opened as {final}, outside {boundary_real}")
    except BaseException:
        os.close(fd)
        raise
    return fd
