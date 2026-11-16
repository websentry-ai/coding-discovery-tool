"""Claude Code memory discovery: the auto memory and subagent memory folders of one user."""
import hashlib
import json
import logging
import os
import re
import stat
import time
from heapq import nsmallest
from itertools import chain, islice, takewhile
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Tuple
from urllib.parse import urlsplit

from . import cache as _state
from .constants import MAX_CONFIG_FILE_SIZE, MAX_SEARCH_DEPTH, is_symlink_or_junction
from .hooks_extractor import MAX_HOOK_CONFIG_SIZE
from .memory_secret_rules import RULES_VERSION, find_secrets
from .rule_read_helpers import read_rule_file_contained
from .utils import _write_file_secure, report_to_sentry

logger = logging.getLogger(__name__)

MEMORY_CACHE_FILENAME = "memory-scan-cache.json"
INDEX_FILE_NAME = "MEMORY.md"
MAX_FILES_PER_MEMORY_FOLDER = 300
MAX_DIRS_PER_MEMORY_FOLDER = 100
MEMORY_SCAN_BUDGET_SECONDS = 10
TRANSCRIPT_HEAD_BYTES = 256 * 1024
TRANSCRIPT_HEAD_LINES = 40
MAX_TRANSCRIPTS_TRIED = 5
MAX_PROJECT_FOLDERS = 500
MAX_SUBAGENT_FOLDERS_PER_BASE = 50
MAX_MEMORY_FOLDERS = 150
MAX_FLAGGED_FILES_PER_FOLDER = 50
MAX_MEMORY_CONTENT_BYTES = 2 * 1024 * 1024
MAX_URL_HOSTS_PER_FILE = 50
MAX_URL_HOSTS_PER_FOLDER = 200
MAX_FINDINGS_PER_FILE = 100
MAX_CACHE_ENTRIES_PER_USER = 50000

_URL = re.compile(r"https?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_HOSTNAME = re.compile(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+")


def memory_scan_enabled(scan_start_response: Dict) -> bool:
    """True only when the scan-start response body carries memory_scan_enabled: true."""
    try:
        return json.loads(scan_start_response.get("body") or "").get("memory_scan_enabled") is True
    except Exception:
        return False


def url_hosts(content: str) -> List[str]:
    """Sorted unique hostnames of the http(s) URLs in content."""
    return sorted({host for host in map(_hostname, _URL.findall(content)) if host})[:MAX_URL_HOSTS_PER_FILE]


def _hostname(url: str) -> Optional[str]:
    try:
        host = (urlsplit(url).hostname or "").rstrip(".,;:!?*")
    except ValueError:
        return None
    return host if _HOSTNAME.fullmatch(host) else None


def _subdirs(parent: Path, limit: int) -> List[Path]:
    """The first `limit` real subdirectories of parent in name order."""
    try:
        return nsmallest(limit, (
            Path(entry.path) for entry in os.scandir(parent)
            if entry.is_dir(follow_symlinks=False) and not is_symlink_or_junction(entry.path)))
    except (OSError, ValueError):
        return []


def _project_path(claude_project_dir: Path) -> Tuple[Optional[str], bool]:
    """(the project's real path from a session transcript's cwd or None, False when a short head may still gain one)."""
    try:
        transcripts = sorted(claude_project_dir.glob("*.jsonl"))[:MAX_TRANSCRIPTS_TRIED]
    except OSError:
        return None, True
    settled = True
    for transcript in transcripts:
        read = read_rule_file_contained(transcript, claude_project_dir, max_size=TRANSCRIPT_HEAD_BYTES)
        lines = (read[0] if read else "").split("\n")
        settled = settled and (len(lines) >= TRANSCRIPT_HEAD_LINES or not read or read[1])
        for line in lines[:TRANSCRIPT_HEAD_LINES]:
            try:
                cwd = json.loads(line).get("cwd")
            except (ValueError, AttributeError, RecursionError):
                continue
            if isinstance(cwd, str) and os.path.isabs(cwd):
                return cwd, True
    return None, settled


def _cached_project_path(entry: object, dir_mtime: int) -> Tuple[bool, Optional[str]]:
    """(usable, path): a found path is kept for good, a missing one only while the folder is unchanged."""
    try:
        path, mtime = entry["project_path"], entry["mtime"]
    except (KeyError, TypeError):
        return False, None
    if not isinstance(mtime, int) or not (path is None or (isinstance(path, str) and os.path.isabs(path))):
        return False, None
    return (path is not None or mtime == dir_mtime), path


def _resolved_project_path(
        project_dir: Path, cached_entry: object, deadline: float) -> Tuple[Optional[str], Optional[Dict]]:
    """(project path, cache entry to keep or None when unresolved in time or not yet settled)."""
    try:
        dir_mtime = os.lstat(project_dir).st_mtime_ns
    except OSError:
        return None, None
    usable, cached_path = _cached_project_path(cached_entry, dir_mtime)
    if usable:
        return cached_path, cached_entry
    if time.monotonic() >= deadline:
        return None, None
    project_path, settled = _project_path(project_dir)
    return project_path, {"project_path": project_path, "mtime": dir_mtime} if settled else None


def _relocated_memory_dir(home: Path) -> Optional[Path]:
    """The autoMemoryDirectory set in the user's settings.json, or None; never the home itself or a folder above it."""
    read = read_rule_file_contained(
        home / ".claude" / "settings.json", home, allow_symlink=True, max_size=MAX_HOOK_CONFIG_SIZE)
    try:
        relocated = json.loads(read[0]).get("autoMemoryDirectory") if read and not read[1] else None
        if not isinstance(relocated, str):
            return None
        path = home / relocated[2:] if relocated.startswith("~/") else Path(relocated)
        resolved, resolved_home = Path(os.path.realpath(path)), Path(os.path.realpath(home))
    except (OSError, ValueError, AttributeError):
        return None
    if not path.is_absolute() or resolved == resolved_home or resolved in resolved_home.parents:
        return None
    return path


def _subagent_folders(base_dir: Path, project_path: Optional[str], scope: str) -> List[Dict]:
    return [
        {"path": agent_dir, "project_path": project_path, "memory_type": "subagent",
         "agent_name": agent_dir.name, "scope": scope}
        for agent_dir in _subdirs(base_dir, MAX_SUBAGENT_FOLDERS_PER_BASE)
    ]


def _owned_real_dir(folder: Path, home: Path) -> bool:
    """A real directory owned by the scanned user, and on Windows inside their home: never a link or a junction."""
    try:
        if is_symlink_or_junction(folder):
            return False
        if os.name == "nt" and Path(os.path.realpath(home)) not in Path(os.path.realpath(folder)).parents:
            return False
        folder_stat = os.lstat(folder)
        return stat.S_ISDIR(folder_stat.st_mode) and folder_stat.st_uid == os.stat(home).st_uid
    except (OSError, ValueError):
        return False


def _memory_folders(home: Path, deadline: float, cached_entries: Dict) -> Tuple[List[Dict], Dict]:
    """(every memory folder of this user, project-path cache entries): auto, relocated auto, then subagent folders."""
    claude_dir = home / ".claude"
    resolved = {
        project_dir: _resolved_project_path(project_dir, cached_entries.get(str(project_dir)), deadline)
        for project_dir in _subdirs(claude_dir / "projects", MAX_PROJECT_FOLDERS)
    }
    auto_folders = [
        {"path": project_dir / "memory", "memory_type": "auto", "scope": "user", "project_path": project_path}
        for project_dir, (project_path, _) in resolved.items()
    ]
    relocated_folders = [
        {"path": relocated_dir, "project_path": None, "memory_type": "auto", "scope": "user"}
        for relocated_dir in [_relocated_memory_dir(home)] if relocated_dir
    ]
    project_paths = sorted({folder["project_path"] for folder in auto_folders if folder["project_path"]})
    project_folders = [
        folder
        for project_path in takewhile(lambda _: time.monotonic() < deadline, project_paths)
        for folder in (
            _subagent_folders(Path(project_path) / ".claude" / "agent-memory", project_path, "project")
            + _subagent_folders(Path(project_path) / ".claude" / "agent-memory-local", project_path, "local")
        )
    ]
    user_folders = _subagent_folders(claude_dir / "agent-memory", None, "user")
    first_by_path = {}
    for folder in chain(auto_folders, relocated_folders, user_folders, project_folders):
        if len(first_by_path) >= MAX_MEMORY_FOLDERS:
            break
        if _owned_real_dir(folder["path"], home):
            first_by_path.setdefault(folder["path"], folder)
    project_entries = {str(project_dir): entry for project_dir, (_, entry) in resolved.items() if entry}
    return list(first_by_path.values()), project_entries


def _markdown_files(folder: Path, deadline: float) -> Iterator[Path]:
    """Every *.md under folder down to MAX_SEARCH_DEPTH, the folder's own MEMORY.md first; links are not entered."""
    for directory, subdirs, names in islice(os.walk(folder), MAX_DIRS_PER_MEMORY_FOLDER):
        if time.monotonic() >= deadline:
            return
        within_depth = len(Path(directory).relative_to(folder).parts) < MAX_SEARCH_DEPTH
        subdirs[:] = sorted(
            name for name in subdirs if within_depth and not is_symlink_or_junction(os.path.join(directory, name)))
        for name in sorted(names, key=lambda file_name: (file_name != INDEX_FILE_NAME, file_name)):
            if name.lower().endswith(".md"):
                yield Path(directory) / name


def _cached_verdict(entry: object) -> Dict:
    """entry when it has the shape this module writes, else {} so the file is rescanned."""
    try:
        well_formed = (
            isinstance(entry["size"], int) and isinstance(entry["mtime"], int) and isinstance(entry["sha256"], str)
            and isinstance(entry["url_hosts"], list) and isinstance(entry["findings"], list)
            and all(isinstance(host, str) for host in entry["url_hosts"])
            and all(isinstance(finding["rule_id"], str) and isinstance(finding["line"], int)
                    for finding in entry["findings"])
        )
    except (KeyError, TypeError):
        return {}
    return entry if well_formed else {}


def _check_file(path: Path, folder: Path, cached_entry: object) -> Optional[Tuple[Dict, Optional[Dict]]]:
    """(cache entry, payload item or None for an unsent topic file) for one memory file; None when unreadable."""
    try:
        file_stat = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    cached = _cached_verdict(cached_entry)
    is_index = path.parent == folder and path.name == INDEX_FILE_NAME
    unchanged = cached.get("size") == file_stat.st_size and cached.get("mtime") == file_stat.st_mtime_ns
    if unchanged and not is_index and not cached["findings"]:
        return cached, None
    read = read_rule_file_contained(path, folder)
    if read is None:
        return None
    content, truncated, size, last_modified = read
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    verdict = cached if cached.get("sha256") == content_hash else {
        "findings": find_secrets(content)[:MAX_FINDINGS_PER_FILE], "url_hosts": url_hosts(content)}
    entry = {"size": file_stat.st_size, "mtime": file_stat.st_mtime_ns, "sha256": content_hash,
             "findings": verdict["findings"], "url_hosts": verdict["url_hosts"]}
    if not is_index and not entry["findings"]:
        return entry, None
    item = {"file_path": str(path), "file_name": path.name, "content": content, "size": size,
            "last_modified": last_modified, "truncated": truncated}
    if entry["findings"]:
        item["findings"] = entry["findings"]
    return entry, item


def _fits_budget(item: Dict, content_budget: List[int]) -> bool:
    """True and spends the item's sent size (JSON-escaped) from the budget when it fits."""
    cost = len(json.dumps(item["content"]))
    if cost > content_budget[0]:
        return False
    content_budget[0] -= cost
    return True


def _scan_folder(folder: Dict, cached_entries: Dict, deadline: float,
                 content_budget: List[int]) -> Tuple[Optional[Dict], Dict]:
    """(payload entry or None for a folder with no memory files, cache entries by file path)."""
    root = folder["path"]
    candidates = islice(_markdown_files(root, deadline), MAX_FILES_PER_MEMORY_FOLDER)
    attempted = [
        (path, _check_file(path, root, cached_entries.get(str(path))))
        for path in takewhile(lambda _: time.monotonic() < deadline, candidates)
    ]
    checked = [(path,) + result for path, result in attempted if result]
    entries = {str(path): entry for path, entry, _ in checked}
    if not checked:
        return None, entries
    index_path = root / INDEX_FILE_NAME
    flagged = [entry for entry in entries.values() if entry["findings"]]
    truncated = [entry for entry in entries.values() if entry["size"] > MAX_CONFIG_FILE_SIZE]
    clean = [entry for entry in entries.values()
             if not entry["findings"] and entry["size"] <= MAX_CONFIG_FILE_SIZE]
    index_item = next((item for path, _, item in checked if path == index_path), None)
    index = index_item if index_item and _fits_budget(index_item, content_budget) else None
    flagged_items = [item for path, _, item in checked if item and path != index_path]
    sent = list(islice(
        (item for item in flagged_items if _fits_budget(item, content_budget)), MAX_FLAGGED_FILES_PER_FOLDER))
    not_sent = len(flagged_items) - len(sent) + bool(index_item and not index)
    return {
        **folder,
        "path": str(root),
        "index": index,
        "flagged": sent,
        "counts": {"total": len(checked), "flagged": len(flagged), "clean": len(clean), "truncated": len(truncated),
                   **({"not_sent": not_sent} if not_sent else {})},
        "url_hosts": sorted({host for entry in entries.values() for host in entry["url_hosts"]})[
            :MAX_URL_HOSTS_PER_FOLDER],
    }, entries


def _folders_hash(folders: List[Dict]) -> str:
    def stable(memory_file: Optional[Dict]) -> Optional[Dict]:
        return memory_file and {key: value for key, value in memory_file.items() if key != "last_modified"}

    hashed = [
        {**folder, "index": stable(folder["index"]), "flagged": [stable(item) for item in folder["flagged"]]}
        for folder in folders
    ]
    canonical = json.dumps(hashed, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cache_path() -> Path:
    return _state.UNBOUND_DIR / MEMORY_CACHE_FILENAME


def _read_cache() -> Dict:
    """Cached verdicts by home user; empty when the cache is missing, unreadable or written by other rules."""
    try:
        with _cache_path().open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        users = data["users"] if data["rules_version"] == RULES_VERSION else {}
        return users if isinstance(users, dict) else {}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def _write_cache(users: Dict) -> None:
    try:
        if _state.UNBOUND_DIR != _state._HOME_STATE_DIR and _state.UNBOUND_DIR.is_symlink():
            return
        cache = {"rules_version": RULES_VERSION, "users": users}
        _write_file_secure(_cache_path(), json.dumps(cache).encode("utf-8"))
    except OSError as e:
        logger.debug(f"memory-scan-cache write failed: {e}")


def collect_claude_memories(home: Path, home_user: str) -> Optional[Dict]:
    """The memories block for one user's Claude Code report, or None when the scan failed or ran out of time."""
    try:
        deadline = time.monotonic() + MEMORY_SCAN_BUDGET_SECONDS
        users = _read_cache()
        cached_entries = users.get(home_user)
        cached_entries = cached_entries if isinstance(cached_entries, dict) else {}
        memory_folders, project_entries = _memory_folders(home, deadline, cached_entries)
        content_budget = [MAX_MEMORY_CONTENT_BYTES]
        scanned = [
            _scan_folder(folder, cached_entries, deadline, content_budget)
            for folder in takewhile(lambda _: time.monotonic() < deadline, memory_folders)
        ]
        entries = {**project_entries,
                   **{path: entry for _, folder_entries in scanned for path, entry in folder_entries.items()}}
        finished = time.monotonic() < deadline
        if not finished:
            entries = {**entries, **{path: entry for path, entry in cached_entries.items() if path not in entries}}
        entries = dict(islice(entries.items(), MAX_CACHE_ENTRIES_PER_USER))
        if entries != cached_entries:
            _write_cache({**users, home_user: entries})
        if not finished:
            logger.warning(f"Claude memory scan for {home_user} ran out of time; the next run resumes it")
            return None
        folders = [payload for payload, _ in scanned if payload]
        return {"hash": _folders_hash(folders), "folders": folders}
    except Exception as e:
        logger.warning(f"Claude memory discovery failed for {home_user}: {e}")
        report_to_sentry(e, {"phase": "memory_scan"}, level="warning")
        return None
