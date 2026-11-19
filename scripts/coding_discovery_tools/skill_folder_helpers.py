"""Skill folders: the files bundled next to a skill's SKILL.md, collected so the backend scans the whole folder.

The limits and the scan order mirror ai-gateway-data (`AIToolSkillFile`, `skill_folder.file_priority`), so the
device never sends text the backend would drop. Every file is opened through the contained-read boundary, and
symlinks, junctions and hard links inside the folder are never followed.
"""
import base64
import copy
import functools
import hashlib
import logging
import os
import posixpath
import time
from pathlib import Path
from typing import Dict, List, Optional

from .constants import is_symlink_or_junction
from .rule_read_helpers import _open_checked

logger = logging.getLogger(__name__)

SKILL_FILE_NAME = "SKILL.md"
# Mirrors ai-gateway-data AIToolSkillFile: text kept per file, files with text, text per folder.
MAX_FILE_CONTENT_CHARS = 51_200
MAX_FILES_WITH_CONTENT = 100
MAX_TOTAL_CONTENT_CHARS = 200_000
# Past the backend's 1,000 listed files: it picks which to keep, unreadable binaries and scripts first.
MAX_WALKED_FILES = 5000
# Stands for a folder the walk could not finish (too deep, too many files, out of time). It has no extension, so
# the backend treats it as possible unread code and rates the skill at least Medium.
NOT_WALKED_NAME = "(not walked)"
MAX_NOT_WALKED_LISTED = 50
BINARY_HEAD_BYTES = 512  # what the backend reads to tell an image from an executable
_SNIFF_BYTES = 8192  # a NUL in the first bytes marks a file as binary
# Files up to this size are read and hashed whole (sha256 is always the real digest); a bigger one is listed by path
# and size only, never opened. The largest of 33,272 files in 7,263 real skills was 7.7 MiB, and GitHub refuses files
# past 100 MiB, so a skill file above it is an anomaly. Size-based, so which files are hashed never depends on timing.
MAX_HASHED_FILE_BYTES = 100 * 1024 * 1024
MAX_BINARY_HEADS = 1000  # binaries sent with their first bytes; the backend keeps at most 1,000 files anyway
MAX_DIR_ENTRIES = 10_000  # entries read from one folder; a bigger folder is marked as not walked
MAX_EXAMINED_ENTRIES = 50_000  # entries looked at in the whole skill folder, files and folders alike
_DEADLINE_CHECK_EVERY = 256  # entries between time checks while a folder is read
MAX_FOLDER_DEPTH = 8
# A safety stop for pathological folders (a slow network disk, a huge tree), far above real ones (0.02 s for the
# slowest of 74 real skills), so the output does not change from run to run.
FOLDER_TIME_BUDGET_SECONDS = 15.0
# Directories that hold tooling or dependencies, not the skill's own files.
SKIP_DIR_NAMES = frozenset({
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
    ".tox", ".idea", ".vscode",
})
_SKIP_FILE_NAMES = frozenset({".ds_store", "thumbs.db", "desktop.ini"})

# Mirrors ai-gateway-data skill_folder.file_priority: 0 code, 1 config, 2 docs, 3 everything else.
_CODE_EXTENSIONS = frozenset({
    '.sh', '.bash', '.zsh', '.fish', '.ps1', '.psm1', '.bat', '.cmd', '.py', '.js', '.mjs', '.cjs', '.ts', '.tsx',
    '.jsx', '.rb', '.go', '.rs', '.php', '.pl', '.lua', '.java', '.kt', '.swift', '.cs', '.applescript', '.vbs',
})
_CONFIG_EXTENSIONS = frozenset({'', '.toml', '.ini', '.cfg', '.conf', '.env', '.yaml', '.yml', '.plist', '.xml'})
_DOC_EXTENSIONS = frozenset({'.md', '.mdx', '.txt', '.rst', '.html', '.htm'})
_CODE_NAMES = frozenset({'makefile', 'dockerfile', 'justfile', 'procfile'})

# One walk per folder per run: several tools can report the same skill folder.
_FOLDER_CACHE: Dict[str, Optional[List[Dict]]] = {}


def file_priority(path: str) -> int:
    """Scan rank of a bundled file, as the backend ranks it."""
    name = posixpath.basename(path).lower()
    ext = posixpath.splitext(name)[1]
    if ext in _CODE_EXTENSIONS or name in _CODE_NAMES:
        return 0
    if ext in _CONFIG_EXTENSIONS:
        return 1
    if ext in _DOC_EXTENSIONS:
        return 2
    return 3


def _homes_parents() -> List[Path]:
    """Folders that hold users' homes, on every OS this runs on."""
    parents = [Path("/Users"), Path("/home")]
    drive = os.environ.get("SystemDrive")
    if drive:
        parents.append(Path(drive + "\\") / "Users")
    return parents


@functools.lru_cache(maxsize=1)
def _machine_user_homes() -> frozenset:
    """Every account's home on this machine (pwd, or Windows' profile list), so an admin scan guards each user's."""
    homes = set()
    try:
        if os.name == "nt":
            from .utils import windows_user_homes  # pylint: disable=import-outside-toplevel
            homes.update(windows_user_homes().values())
        else:
            import pwd  # pylint: disable=import-outside-toplevel
            homes.update(Path(entry.pw_dir) for entry in pwd.getpwall() if entry.pw_dir not in ("", "/"))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug(f"Could not list user homes: {e}")
    real = set()
    for home in homes:
        try:
            real.add(Path(os.path.realpath(str(home))))
        except (OSError, ValueError):
            continue
    return frozenset(h for h in real if h != Path(h.anchor))


def _is_home_or_above(root: Path) -> bool:
    """True for any user's home, a folder holding one, or a top-level folder under /Users, /home or C:\\Users."""
    homes = set(_machine_user_homes())
    try:
        homes.add(Path(os.path.realpath(str(Path.home()))))
    except (OSError, RuntimeError, ValueError):
        pass
    if any(root == home or root in home.parents for home in homes):
        return True
    if root in (Path("/root"), Path("/var/root"), Path("/private/var/root")):
        return True
    return any(root.parent == parent or root == parent for parent in _homes_parents())


def _home_of(path: Path, homes) -> Optional[Path]:
    """The account home a path sits in (the deepest one, for nested homes), or None."""
    inside = [home for home in homes if path == home or home in path.parents]
    return max(inside, key=lambda home: len(home.parts)) if inside else None


def _crosses_home(found: Path, root: Path) -> bool:
    """True when a link anywhere on the path moved the folder into a different account's home than it was found in."""
    homes = set(_machine_user_homes())
    try:
        homes.add(Path(os.path.realpath(str(Path.home()))))
    except (OSError, RuntimeError, ValueError):
        pass
    # The found path is resolved by its own home's real name, so /var vs /private/var style aliases still match.
    found_home = _home_of(Path(os.path.abspath(str(found))), {Path(os.path.abspath(str(h))) for h in homes} | homes)
    real_home = _home_of(root, homes)
    if real_home is None:
        return False
    if found_home is None:
        return True
    return Path(os.path.realpath(str(found_home))) != real_home


def _skill_root(skill_md: Path) -> Optional[Path]:
    """The real folder of a SKILL.md, or None when it is too broad to walk (a drive, or a user's home or above)."""
    try:
        root = Path(os.path.realpath(str(skill_md.parent)))
    except (OSError, ValueError):
        return None
    # A skill folder linked to a home would upload that home's files, so it is never walked.
    if root == Path(root.anchor) or len(root.parts) < 3 or _is_home_or_above(root) or not root.is_dir():
        return None
    # Nor one a linked parent folder moved into another account's home: that user's files are not this report's.
    if _crosses_home(skill_md.parent, root):
        return None
    return root


def _not_walked(rel_dir: str) -> Dict:
    """A name-only entry for a folder the walk never finished, so the backend treats it as unread content."""
    return {"path": posixpath.join(rel_dir, NOT_WALKED_NAME) if rel_dir else NOT_WALKED_NAME, "size": 0,
            "truncated": True}


def _read_dir(path: str, deadline: float, room: int):
    """A folder's entries sorted by name, and whether reading stopped early (entry cap, examined cap or time)."""
    batch = []
    with os.scandir(path) as entries:
        for n, entry in enumerate(entries):
            if n >= min(MAX_DIR_ENTRIES, room) or (n and n % _DEADLINE_CHECK_EVERY == 0 and time.monotonic() >= deadline):
                return sorted(batch, key=lambda e: e.name), True
            batch.append(entry)
    return sorted(batch, key=lambda e: e.name), False


def _list_files(root: Path, deadline: float, skill_md_name: str = SKILL_FILE_NAME) -> List[Dict]:
    """Regular files under root (relative POSIX paths and sizes), never following a link; folders left unread are named."""
    listed: List[Dict] = []
    unwalked: List[str] = []  # folders past the depth, count, size or time limit
    stack = [("", 0)]
    examined = 0
    while stack:
        if len(listed) >= MAX_WALKED_FILES or examined >= MAX_EXAMINED_ENTRIES or time.monotonic() >= deadline:
            unwalked.extend(rel_dir for rel_dir, _ in stack)
            break
        rel_dir, depth = stack.pop()
        try:
            # Read lazily and bounded: a folder with a million entries must not stall the scan or fill memory.
            entries, cut = _read_dir(os.path.join(str(root), rel_dir), deadline, MAX_EXAMINED_ENTRIES - examined)
        except OSError:
            unwalked.append(rel_dir)
            continue
        examined += len(entries)
        if cut:
            unwalked.append(rel_dir)
        for n, entry in enumerate(entries):
            out_of_time = n % _DEADLINE_CHECK_EVERY == 0 and n and time.monotonic() >= deadline
            if len(listed) >= MAX_WALKED_FILES or out_of_time:
                # Cut off inside this folder: what it has left goes unread.
                if any(not is_symlink_or_junction(e.path) for e in entries[n:]):
                    unwalked.append(rel_dir)
                break
            if is_symlink_or_junction(entry.path):
                continue
            rel = posixpath.join(rel_dir, entry.name) if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in SKIP_DIR_NAMES:
                        continue
                    if depth + 1 < MAX_FOLDER_DEPTH:
                        stack.append((rel, depth + 1))
                    else:
                        unwalked.append(rel)
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            # SKILL.md itself is already sent as the skill's content.
            # Only the skill's own document is sent separately; a second skill.md beside it is a file like any other.
            if (not rel_dir and entry.name == skill_md_name) or entry.name.lower() in _SKIP_FILE_NAMES:
                continue
            listed.append({"path": rel, "size": size})
    # Named, not dropped: without these the list would look like the whole folder.
    return listed + [_not_walked(rel_dir) for rel_dir in sorted(set(unwalked))[:MAX_NOT_WALKED_LISTED]]


def _read_file(root: Path, item: Dict, text_room: int, send_head: bool = True) -> None:
    """Fill item's sha256 (always the whole file's), and its content or binary head, from one contained read."""
    opened = _open_checked(root / item["path"], root, allow_symlink=False)
    if opened is None:
        item["truncated"] = True
        return
    fd, _ = opened
    keep = max(_SNIFF_BYTES, text_room * 4)  # a character is at most 4 bytes in UTF-8
    digest, kept, read = hashlib.sha256(), bytearray(), 0
    try:
        with os.fdopen(fd, "rb") as handle:
            # Read to the end every time: a file's digest is what makes any edit to it a new upload and a new version.
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                read += len(chunk)
                if read > MAX_HASHED_FILE_BYTES:
                    item["truncated"] = True  # grew past the cap after it was listed: sent like any oversized file
                    return
                digest.update(chunk)
                kept += chunk[:keep - len(kept)]
        item["sha256"] = digest.hexdigest()
    except OSError:
        item["truncated"] = True
        return
    if b"\x00" in kept[:_SNIFF_BYTES]:
        item["binary"] = True
        if send_head:
            item["head"] = base64.b64encode(bytes(kept[:BINARY_HEAD_BYTES])).decode("ascii")
        return
    text = bytes(kept).decode("utf-8", errors="replace")
    content = text[:text_room]
    if content:
        item["content"] = content
    # Whole file kept and every character sent, or it is marked as cut.
    if read > len(kept) or len(text) > len(content):
        item["truncated"] = True


def collect_skill_files(skill_md: Path) -> Optional[List[Dict]]:
    """The skill's other files as the backend's `files` list, or None when its folder can't be walked safely."""
    root = _skill_root(skill_md)
    if root is None:
        return None
    key = str(root)
    if key in _FOLDER_CACHE:
        cached = _FOLDER_CACHE[key]
        return copy.deepcopy(cached) if cached is not None else None
    deadline = time.monotonic() + FOLDER_TIME_BUDGET_SECONDS
    try:
        files = sorted(_list_files(root, deadline, skill_md.name), key=lambda f: (file_priority(f["path"]), f["path"]))
        text_budget, with_text, heads = MAX_TOTAL_CONTENT_CHARS, 0, 0
        for item in files:
            if posixpath.basename(item["path"]) == NOT_WALKED_NAME and item.get("truncated"):
                continue  # a folder left unread, not a file to open
            if time.monotonic() >= deadline or item["size"] > MAX_HASHED_FILE_BYTES:
                # Listed by name only, out of time or too big to read: the backend scores it as unread content.
                item["truncated"] = True
                continue
            room = min(MAX_FILE_CONTENT_CHARS, text_budget) if with_text < MAX_FILES_WITH_CONTENT else 0
            _read_file(root, item, room, send_head=heads < MAX_BINARY_HEADS)
            heads += 1 if item.get("head") else 0
            if item.get("content"):
                with_text += 1
                text_budget -= len(item["content"])
            elif not item.get("binary") and item["size"] > 0:
                item["truncated"] = True
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.debug(f"Could not collect skill folder {root}: {e}")
        files = None
    _FOLDER_CACHE[key] = files
    return copy.deepcopy(files) if files is not None else None


def attach_skill_folders(tool_result: Dict) -> None:
    """Add `files` to every SKILL.md skill in a tool's discovery result, in place; others are left as they are."""
    for project in tool_result.get("projects") or []:
        for skill in (project.get("skills") or []) if isinstance(project, dict) else []:
            if not isinstance(skill, dict) or "files" in skill or not skill.get("skill_name"):
                continue
            # Commands and agents are single files, even one named SKILL.md: its siblings are other commands.
            if skill.get("type", "skill") != "skill":
                continue
            file_path = skill.get("file_path")
            if (skill.get("file_name") or "").lower() != SKILL_FILE_NAME.lower() or not file_path:
                continue
            path = Path(file_path)
            if not path.is_absolute():
                continue
            files = collect_skill_files(path)
            if files is not None:
                skill["files"] = files
