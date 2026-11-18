"""Skill folders: the files bundled next to a skill's SKILL.md, collected so the backend scans the whole folder.

The limits and the scan order mirror ai-gateway-data (`AIToolSkillFile`, `skill_folder.file_priority`), so the
device never sends text the backend would drop. Every file is opened through the contained-read boundary, and
symlinks, junctions and hard links inside the folder are never followed.
"""
import base64
import copy
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
BINARY_HEAD_BYTES = 512  # what the backend reads to tell an image from an executable
_SNIFF_BYTES = 8192  # a NUL in the first bytes marks a file as binary
MAX_HASHED_FILE_SIZE = 8 * 1024 * 1024  # bigger files are listed without a sha256
MAX_HASHED_BYTES_PER_FOLDER = 64 * 1024 * 1024
MAX_FOLDER_DEPTH = 8
FOLDER_TIME_BUDGET_SECONDS = 3.0  # past this the rest of the folder is listed by name only
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


def _is_home_or_above(root: Path) -> bool:
    """True for a user's home, a folder holding one, or a top-level folder under /Users, /home or C:\\Users."""
    try:
        home = Path(os.path.realpath(str(Path.home())))
    except (OSError, RuntimeError, ValueError):
        home = None
    if home is not None and (root == home or root in home.parents):
        return True
    if root in (Path("/root"), Path("/var/root"), Path("/private/var/root")):
        return True
    return any(root.parent == parent or root == parent for parent in _homes_parents())


def _skill_root(skill_md: Path) -> Optional[Path]:
    """The real folder of a SKILL.md, or None when it is too broad to walk (a drive, or a user's home or above)."""
    try:
        root = Path(os.path.realpath(str(skill_md.parent)))
    except (OSError, ValueError):
        return None
    # A skill folder linked to a home would upload that home's files, so it is never walked.
    if root == Path(root.anchor) or len(root.parts) < 3 or _is_home_or_above(root) or not root.is_dir():
        return None
    return root


def _list_files(root: Path, deadline: float) -> List[Dict]:
    """Regular files under root (relative POSIX paths and sizes), never following a link; capped in count."""
    listed: List[Dict] = []
    stack = [("", 0)]
    while stack and len(listed) < MAX_WALKED_FILES and time.monotonic() < deadline:
        rel_dir, depth = stack.pop()
        try:
            with os.scandir(os.path.join(str(root), rel_dir)) as entries:
                entries = sorted(entries, key=lambda e: e.name)
        except OSError:
            continue
        for entry in entries:
            if is_symlink_or_junction(entry.path):
                continue
            rel = posixpath.join(rel_dir, entry.name) if rel_dir else entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name not in SKIP_DIR_NAMES and depth + 1 < MAX_FOLDER_DEPTH:
                        stack.append((rel, depth + 1))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                size = entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
            # SKILL.md itself is already sent as the skill's content.
            if (not rel_dir and entry.name.lower() == SKILL_FILE_NAME.lower()) or entry.name.lower() in _SKIP_FILE_NAMES:
                continue
            listed.append({"path": rel, "size": size})
            if len(listed) >= MAX_WALKED_FILES:
                break
    return listed


def _read_file(root: Path, item: Dict, text_room: int, hash_room: int) -> int:
    """Fill item's sha256, and its content or binary head, from one contained read; returns the bytes hashed."""
    opened = _open_checked(root / item["path"], root, allow_symlink=False)
    if opened is None:
        item["truncated"] = True
        return 0
    fd, st = opened
    hash_it = st.st_size <= min(MAX_HASHED_FILE_SIZE, hash_room)
    keep = max(_SNIFF_BYTES, text_room * 4)  # a character is at most 4 bytes in UTF-8
    digest, kept, read, at_end = hashlib.sha256(), bytearray(), 0, False
    try:
        with os.fdopen(fd, "rb") as handle:
            while hash_it or len(kept) < keep:
                chunk = handle.read(1024 * 1024 if hash_it else keep - len(kept))
                if not chunk:
                    at_end = True
                    break
                read += len(chunk)
                if hash_it:
                    digest.update(chunk)
                kept += chunk[:keep - len(kept)]
            if not at_end:
                at_end = not handle.read(1)
    except OSError:
        item["truncated"] = True
        return 0
    if hash_it:
        item["sha256"] = digest.hexdigest()
    if b"\x00" in kept[:_SNIFF_BYTES]:
        item["binary"] = True
        item["head"] = base64.b64encode(bytes(kept[:BINARY_HEAD_BYTES])).decode("ascii")
        return read if hash_it else 0
    text = bytes(kept).decode("utf-8", errors="replace")
    content = text[:text_room]
    if content:
        item["content"] = content
    # Whole file read and every character kept, or it is marked as cut.
    if not (at_end and read == len(kept)) or len(text) > len(content):
        item["truncated"] = True
    return read if hash_it else 0


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
        files = sorted(_list_files(root, deadline), key=lambda f: (file_priority(f["path"]), f["path"]))
        text_budget, hash_budget, with_text = MAX_TOTAL_CONTENT_CHARS, MAX_HASHED_BYTES_PER_FOLDER, 0
        for item in files:
            if time.monotonic() >= deadline:
                # Listed by name only: the backend scores a folder it could not fully read with care.
                item["truncated"] = True
                continue
            room = min(MAX_FILE_CONTENT_CHARS, text_budget) if with_text < MAX_FILES_WITH_CONTENT else 0
            hash_budget -= _read_file(root, item, room, hash_budget)
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
            file_path = skill.get("file_path")
            if (skill.get("file_name") or "").lower() != SKILL_FILE_NAME.lower() or not file_path:
                continue
            path = Path(file_path)
            if not path.is_absolute():
                continue
            files = collect_skill_files(path)
            if files is not None:
                skill["files"] = files
