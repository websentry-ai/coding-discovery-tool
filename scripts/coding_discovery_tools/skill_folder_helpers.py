"""Skill folders: the files bundled next to a skill's SKILL.md, collected so the backend scans the whole folder.

The limits and the scan order mirror ai-gateway-data (`AIToolSkillFile`, `skill_folder.file_priority`), so the
device never sends text the backend would drop. Every file is opened through the contained-read boundary, and
symlinks, junctions and hard links inside the folder are never followed.
"""
import base64
import copy
import functools
import hashlib
import json
import logging
import os
import posixpath
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .constants import is_symlink_or_junction
from .rule_read_helpers import _dir_real_path, _fd_real_path, _open_checked

logger = logging.getLogger(__name__)

SKILL_FILE_NAME = "SKILL.md"
# Mirrors ai-gateway-data AIToolSkillFile: text kept per file, files with text, text per folder.
MAX_FILE_CONTENT_CHARS = 51_200
MAX_FILES_WITH_CONTENT = 100
MAX_TOTAL_CONTENT_CHARS = 200_000
# Folders one tool report may carry, as sent JSON. A skill whose folder doesn't fit goes as SKILL.md only, as before.
MAX_REPORT_FOLDER_BYTES = 8 * 1024 * 1024
# A report still bigger than this drops every folder before upload; the backend refuses one over 15 MiB whole.
MAX_REPORT_BYTES = 14 * 1024 * 1024
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

# Files that hold the user's own credentials. They are listed by name and size only: their text, first bytes and hash
# never leave the device.
# Private keys are withheld whatever their name says ("dist.pem" is an iOS distribution key, not a template).
_PRIVATE_KEY_EXTENSIONS = frozenset({".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".p8", ".ppk", ".der"})
_PRIVATE_KEY_NAMES = frozenset({
    "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "id_ecdsa_sk", "id_ed25519_sk", "secring.gpg",
})
# Key and state files that never come as templates: withheld whatever word their name carries ("sample-app" is an app).
_NEVER_TEMPLATE_NAMES = frozenset({
    "service-account.json", "service_account.json", "serviceaccountkey.json", "application_default_credentials.json",
})
_NEVER_TEMPLATE_SUFFIXES = (".tfstate", ".tfstate.backup")  # Terraform state holds resource secrets in plain text
_NEVER_TEMPLATE_PREFIXES = ("client_secret",)  # Google OAuth: client_secret_<id>.apps.googleusercontent.com.json
_NEVER_TEMPLATE_PARTS = ("firebase-adminsdk",)  # Firebase admin keys: <project>-firebase-adminsdk-<id>.json
# Service-account keys are also matched by "service-account" anywhere in a .json name, or the words "sa" and "key".
# Env and credential files: a template of one (.env.example, credentials.sample.json) has no real values and is sent.
_CREDENTIAL_NAMES = frozenset({
    ".env", ".envrc", ".dev.vars", "credentials", "credentials.json", "credentials.yml", "credentials.yaml",
    "credentials.csv", "rootkey.csv", "secrets.json", "secrets.yml", "secrets.yaml", "secrets.toml", "token.json",
    "tokens.json", ".token", "accounts.json", "kubeconfig", "auth.json",
    ".netrc", ".npmrc", ".pypirc", ".pgpass", ".git-credentials", ".htpasswd",
})
_CREDENTIAL_SUFFIXES = (".env", ".tfvars", ".tfvars.json")  # Terraform variables hold provider keys
# A backup copy holds what the original holds: "credentials.json.bak" is checked as "credentials.json".
_BACKUP_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".save", "~")
# Files with one of these as a whole word in their name (token.txt, credentials.production.json, secrets.db), whatever
# their extension. Code and written docs with the same words (token_counter.py, secrets-management.md) are still sent.
_CREDENTIAL_WORDS = frozenset({
    "token", "tokens", "credential", "credentials", "secret", "secrets", "password", "passwords", "passwd",
    "apikey", "apikeys",
})
# Credential files known by their folder as well as their name, matched on the path's last two parts.
_CREDENTIAL_PATH_ENDS = frozenset({(".kube", "config"), (".docker", "config.json"), (".aws", "config")})
# Data files in a secrets folder; its scripts and docs are still sent, so a payload can't hide there from the scan.
_CREDENTIAL_DIR_NAMES = frozenset({"secrets", ".secrets", "secret"})
# Tool folders that hold only credentials and their caches (.aws/sso/cache/<hash>.json, .ssh/known keys, gcloud tokens).
_CREDENTIAL_TOOL_DIR_NAMES = frozenset({".aws", ".ssh", ".gnupg", ".kube", ".docker", ".azure", "gcloud"})
_SECRETS_DIR_DOC_EXTENSIONS = frozenset({".md", ".mdx", ".rst", ".html", ".htm"})
_TEMPLATE_MARKERS = frozenset({"example", "sample", "template", "dist"})
# The `skipped` value for such a file. The backend accepts it on a file it lists without content.
SKIPPED_SECRET_FILE = "secret_file"
# A file that starts as a private key is one, whatever its name ("deploy_key", "github_work"); docs that only show
# an example key further down are still sent.
_PRIVATE_KEY_START = re.compile(rb"\s*(-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----|PuTTY-User-Key-File-)")
# A PEM file can open with another block first, e.g. "EC PARAMETERS" before the "EC PRIVATE KEY".
_PEM_START = re.compile(rb"\s*-----BEGIN [A-Z0-9 ]+-----")
_PRIVATE_KEY_BLOCK = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY( BLOCK)?-----")

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


def _name_parts(name: str) -> List[str]:
    """The words of a file name, split on dots, dashes and underscores."""
    return [p for p in name.replace("-", ".").replace("_", ".").split(".") if p]


def _is_template_name(name: str) -> bool:
    """A template's usual name: the word last or just before the extension, or the first of two parts (example.env)."""
    parts = _name_parts(name)
    if len(parts) < 2:
        return False
    return parts[-1] in _TEMPLATE_MARKERS or parts[-2] in _TEMPLATE_MARKERS or (
        len(parts) == 2 and parts[0] in _TEMPLATE_MARKERS)


def is_credential_file(path: str) -> bool:
    """True for a file that holds the user's own credentials (.env, a private key, a token file), not a template."""
    parts = path.lower().split("/")
    name = parts[-1]
    while name.endswith(_BACKUP_SUFFIXES):
        # A file named only ".bak" has no extension to strip, so stop once nothing changes.
        stripped = name[:-1] if name.endswith("~") else posixpath.splitext(name)[0]
        if not stripped or stripped == name:
            break
        name = stripped
    ext = posixpath.splitext(name)[1]
    words = _name_parts(name)
    if (ext in _PRIVATE_KEY_EXTENSIONS or name in _PRIVATE_KEY_NAMES
            or tuple(parts[-2:-1] + [name]) in _CREDENTIAL_PATH_ENDS
            or name.lstrip(".") in _NEVER_TEMPLATE_NAMES or name.endswith(_NEVER_TEMPLATE_SUFFIXES)
            or ext == ".json" and (name.startswith(_NEVER_TEMPLATE_PREFIXES)
                                   or any(part in name for part in _NEVER_TEMPLATE_PARTS)
                                   or "service-account" in name or "service_account" in name
                                   or {"sa", "key"} <= set(words))):
        return True
    # A data file in a secrets folder is withheld whatever its name says. A plain .txt there is usually a key, so only
    # code and written docs in a secrets folder are sent.
    if (any(part in _CREDENTIAL_DIR_NAMES or part in _CREDENTIAL_TOOL_DIR_NAMES for part in parts[:-1])
            and file_priority(path) != 0 and ext not in _SECRETS_DIR_DOC_EXTENSIONS):
        return True
    if _is_template_name(name):
        return False
    # A leading dot hides a file without changing what it holds: ".credentials.json" is Claude Code's token store.
    return (name in _CREDENTIAL_NAMES or name.lstrip(".") in _CREDENTIAL_NAMES or name.endswith(_CREDENTIAL_SUFFIXES)
            or name.startswith((".env.", ".env-", ".env_", ".envrc.", ".dev.vars."))
            or file_priority(name) != 0 and ext not in _SECRETS_DIR_DOC_EXTENSIONS
            and (not _CREDENTIAL_WORDS.isdisjoint(words) or {"api", "key"} <= set(words)))


def _homes_parents() -> List[Path]:
    """Folders that hold users' homes, on every OS this runs on."""
    parents = [Path("/Users"), Path("/home")]
    drive = os.environ.get("SystemDrive")
    if drive:
        parents.append(Path(drive + "\\") / "Users")
    return parents


@functools.lru_cache(maxsize=1)
def _machine_user_home_names() -> frozenset:
    """Every account's home as the system names it (pwd, or Windows' profile list), before any link is resolved."""
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
    names = set()
    for home in homes:
        try:
            names.add(Path(os.path.abspath(str(home))))
        except (OSError, ValueError):
            continue
    return frozenset(h for h in names if h != Path(h.anchor))


@functools.lru_cache(maxsize=1)
def _machine_user_homes() -> frozenset:
    """Every account's real home on this machine, so an admin scan guards each user's."""
    real = set()
    for home in _machine_user_home_names():
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
    homes, names = set(_machine_user_homes()), set(_machine_user_home_names())
    try:
        names.add(Path(os.path.abspath(str(Path.home()))))
        homes.add(Path(os.path.realpath(str(Path.home()))))
    except (OSError, RuntimeError, ValueError):
        pass
    # The found path is matched by every name a home goes by, so /home/alice linked to /var/home/alice still matches.
    found_home = _home_of(Path(os.path.abspath(str(found))), names | homes)
    real_home = _home_of(root, homes)
    if real_home is None:
        return False
    if found_home is None:
        return True
    return Path(os.path.realpath(str(found_home))) != real_home


def _skill_root(skill_md: Path) -> Optional[Path]:
    """The real folder of a SKILL.md, or None when it is too broad to walk (a drive, or a user's home or above)."""
    try:
        # Pinned to the path the OS reports for an open handle on it; every later folder and file read must stay inside.
        pinned = _dir_real_path(os.path.realpath(str(skill_md.parent)))
    except (OSError, ValueError):
        return None
    if not pinned:
        return None
    root = Path(pinned)
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


def _inside_root(real: Optional[str], root: Path) -> bool:
    """True when a path the OS reports for an open handle lies in the checked skill root."""
    if not real:
        return False
    real_n, base_n = (os.path.normcase(os.path.normpath(p)) for p in (real, str(root)))
    return real_n == base_n or real_n.startswith(base_n.rstrip(os.sep) + os.sep)


def _sendable(name: str) -> str:
    """The name as valid UTF-8: a lone surrogate (bytes the file system holds that aren't UTF-8) becomes U+FFFD."""
    return "".join("\ufffd" if 0xD800 <= ord(c) <= 0xDFFF else c for c in name)


def _posix_is_link(entry) -> bool:
    """A POSIX entry is a link, or can't be told apart from one."""
    try:
        return entry.is_symlink()
    except OSError:
        return True


def _entry_kind(entry, is_link: bool):
    """(kind, size) of a folder entry, never following a link: link, dir, file or other."""
    if is_link:
        return "link", 0
    try:
        if entry.is_dir(follow_symlinks=False):
            return "dir", 0
        if entry.is_file(follow_symlinks=False):
            return "file", entry.stat(follow_symlinks=False).st_size
    except OSError:
        pass
    return "other", 0


def _scan(scanner, deadline: float, room: int, is_link):
    """Up to the caps, a folder's (name, kind, size) entries sorted by name, and whether reading stopped early."""
    batch = []
    with scanner as entries:
        for n, entry in enumerate(entries):
            if n >= min(MAX_DIR_ENTRIES, room) or (n and n % _DEADLINE_CHECK_EVERY == 0 and time.monotonic() >= deadline):
                return sorted(batch), True
            batch.append((entry.name,) + _entry_kind(entry, is_link(entry)))
    return sorted(batch), False


def _read_dir(path: str, root: Path, deadline: float, room: int):
    """A folder's entries and whether reading stopped early; OSError when the folder isn't inside the checked root."""
    if os.name == "nt":
        # Windows can't list through a folder handle, so the folder's real path is checked before and after the listing.
        if not _inside_root(_dir_real_path(path), root):
            raise OSError(f"{path} is outside {root}")
        listing = _scan(os.scandir(path), deadline, room, lambda e: is_symlink_or_junction(e.path))
        if not _inside_root(_dir_real_path(path), root):
            raise OSError(f"{path} is outside {root}")
        return listing
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        # Listed through the handle actually opened, so a folder swapped for a link after the checks can't redirect it.
        if not _inside_root(_fd_real_path(fd), root):
            raise OSError(f"{path} is outside {root}")
        return _scan(os.scandir(fd), deadline, room, _posix_is_link)
    finally:
        os.close(fd)


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
            entries, cut = _read_dir(os.path.join(str(root), rel_dir), root, deadline, MAX_EXAMINED_ENTRIES - examined)
        except OSError:
            unwalked.append(rel_dir)
            continue
        examined += len(entries)
        if cut:
            unwalked.append(rel_dir)
        for n, (name, kind, size) in enumerate(entries):
            out_of_time = n % _DEADLINE_CHECK_EVERY == 0 and n and time.monotonic() >= deadline
            if len(listed) >= MAX_WALKED_FILES or out_of_time:
                # Cut off inside this folder: what it has left goes unread.
                if any(left != "link" for _, left, _ in entries[n:]):
                    unwalked.append(rel_dir)
                break
            if kind == "link":
                continue
            shown = _sendable(name)
            rel = posixpath.join(rel_dir, shown) if rel_dir else shown
            if kind == "dir":
                if name in SKIP_DIR_NAMES:
                    continue
                # A name that isn't UTF-8 can't be sent as is: the folder is named, with U+FFFD, and left unread.
                if depth + 1 < MAX_FOLDER_DEPTH and shown == name:
                    stack.append((rel, depth + 1))
                else:
                    unwalked.append(rel)
                continue
            if kind != "file":
                continue
            # SKILL.md itself is already sent as the skill's content.
            # Only the skill's own document is sent separately; a second skill.md beside it is a file like any other.
            if (not rel_dir and name == skill_md_name) or name.lower() in _SKIP_FILE_NAMES:
                continue
            # Listed by its U+FFFD name and never read when the real name isn't UTF-8, so the backend sees it as unread.
            listed.append({"path": rel, "size": size, **({} if shown == name else {"truncated": True})})
    # Named, not dropped: without these the list would look like the whole folder.
    return listed + [_not_walked(rel_dir) for rel_dir in sorted(set(unwalked))[:MAX_NOT_WALKED_LISTED]]


def _read_file(root: Path, item: Dict, text_room: int, send_head: bool = True) -> None:
    """Fill item's sha256 (always the whole file's), and its content or binary head, from one contained read."""
    opened = _open_checked(root / item["path"], root, allow_symlink=False)
    if opened is None:
        item["truncated"] = True
        return
    fd, _ = opened
    if not _inside_root(_fd_real_path(fd), root):
        # The file actually opened left the checked root (a folder above it swapped for a link): not this skill's.
        os.close(fd)
        item["truncated"] = True
        return
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
    head = bytes(kept[:_SNIFF_BYTES])
    # A data or config file holding a key block anywhere (a service-account JSON under its default name) is a key too;
    # code and docs that only quote one are still sent.
    is_data = file_priority(item["path"]) in (1, 3)
    if (_PRIVATE_KEY_START.match(head) or (_PEM_START.match(head) and _PRIVATE_KEY_BLOCK.search(head))
            or (is_data and _PRIVATE_KEY_BLOCK.search(head))):
        # Withheld like a named credential file: the backend gets the name and size, never the key or its hash.
        item.pop("sha256", None)
        item["skipped"] = SKIPPED_SECRET_FILE
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
            if item.get("truncated"):
                continue  # listed only: a folder left unread, or a name that isn't UTF-8
            if is_credential_file(item["path"]):
                # The user's own secrets stay on the device: the backend gets the name and size, never a byte of it.
                # Marked skipped, not truncated, so it reads as left out on purpose rather than cut off.
                item["skipped"] = SKIPPED_SECRET_FILE
                continue
            if time.monotonic() >= deadline or item["size"] > MAX_HASHED_FILE_BYTES:
                # Listed by name only, out of time or too big to read: the backend scores it as unread content.
                item["truncated"] = True
                continue
            room = min(MAX_FILE_CONTENT_CHARS, text_budget) if with_text < MAX_FILES_WITH_CONTENT else 0
            _read_file(root, item, room, send_head=heads < MAX_BINARY_HEADS)
            if item.get("skipped"):
                continue  # a private key found by its first bytes: listed by name and size only
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
    dropped = fit_folder_budget(tool_result)
    if any(dropped):
        logger.info(f"Past the {MAX_REPORT_FOLDER_BYTES // (1024 * 1024)} MB report budget for "
                    f"{tool_result.get('name')}: {dropped[0]} file(s) listed by name only, "
                    f"{dropped[1]} skill(s) sent as SKILL.md only")


def _folder_skills(tool_result: Dict):
    """Every skill in a tool's result that carries a folder (`files`)."""
    for project in tool_result.get("projects") or []:
        for skill in (project.get("skills") or []) if isinstance(project, dict) else []:
            if isinstance(skill, dict) and isinstance(skill.get("files"), list):
                yield skill


def _leave_unread(item: Dict) -> None:
    """Keep a file's name, size and hash but not its text or head, like a file past a folder's own limits."""
    if item.pop("content", None) is not None:
        item["truncated"] = True
    item.pop("head", None)


def fit_folder_budget(tool_result: Dict, budget: Optional[int] = None) -> Tuple[int, int]:
    """Fit a tool report's skill folders into its budget, in place, skill by skill in report order.

    Each skill's SKILL.md is counted first and always sent. Its files follow in order while they fit; one that
    doesn't is listed by name only. A skill reached with no budget left goes as SKILL.md only, as before folders were
    sent. Returns (files listed by name only, skills sent as SKILL.md only)."""
    room = MAX_REPORT_FOLDER_BYTES if budget is None else budget
    unread = skill_md_only = 0
    for skill in list(_folder_skills(tool_result)):
        room -= len(json.dumps(skill.get("content") or ""))
        if room <= 0:
            del skill["files"]
            skill_md_only += 1
            continue
        for item in skill["files"]:
            size = len(json.dumps(item))
            if size > room and (item.get("content") is not None or item.get("head")):
                _leave_unread(item)
                unread += 1
                size = len(json.dumps(item))
            room -= size
    return unread, skill_md_only


def drop_all_folders(report: Dict) -> int:
    """Remove every skill's folder from a report's tools, in place, so each goes as SKILL.md only; returns how many."""
    dropped = 0
    for tool in report.get("tools") or []:
        if isinstance(tool, dict):
            for skill in list(_folder_skills(tool)):
                del skill["files"]
                dropped += 1
    return dropped
