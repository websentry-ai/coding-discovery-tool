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
from pathlib import Path
from typing import Optional, Union

PathLike = Union[str, Path]


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
