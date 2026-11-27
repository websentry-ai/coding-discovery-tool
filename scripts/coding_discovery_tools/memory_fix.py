"""Fix It for memory files: after a completed root scan on macOS, removes credentials the backend has confirmed."""
import hashlib
import json
import logging
import os
import posixpath
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Tuple

from .memory_fix_helper import FAILURE_REASONS, MAX_FILE_BYTES, PROTOCOL
from .rule_read_helpers import _open_checked
from .s3_uploader import _curl_post_json
from .utils import clip_to_deadline, curl_kill_grace, normalize_url

logger = logging.getLogger(__name__)

CAPABILITY = "memory_fix"
KILL_SWITCH_ENV = "UNBOUND_MEMORY_FIX"
FIXES_PATH = "/api/v1/ai-tools/memory-fixes/"
RESULTS_PATH = "/api/v1/ai-tools/memory-fixes/results/"
POLL_INTERVAL_SECONDS = 120
POLL_WINDOW_SECONDS = 20 * 60
REQUEST_TIMEOUT_SECONDS = 30
HELPER_TIMEOUT_SECONDS = 30
# The self-test runs before every root scan on macOS, so it gets less time than a fix.
PING_TIMEOUT_SECONDS = 10
# Below the backend's 2000, so the hash list sent on curl's command line stays well inside ARG_MAX.
FILES_PER_POST = 500
RESULTS_PER_POST = 100
RESULT_POST_TRIES = 3
_FALSEY = frozenset({"0", "false", "no", "off"})


class _Link(NamedTuple):
    domain: str
    api_key: str
    device_id: str
    deadline: Optional[float]


def fixes_pending(scan_start_response: Dict) -> bool:
    """True only when the scan-start response body carries memory_fixes_pending: true."""
    try:
        return json.loads(scan_start_response.get("body") or "").get("memory_fixes_pending") is True
    except Exception:
        return False


def helper_command() -> List[str]:
    if getattr(sys, "frozen", False):
        return [os.path.realpath(sys.executable), "memory-fix"]
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory_fix_helper.py")
    return [sys.executable, "-I", helper]


def _euid() -> int:
    return os.geteuid() if hasattr(os, "geteuid") else -1


def _one_json_line(out: bytes) -> Optional[Dict]:
    """The helper's answer when stdout is exactly one JSON object line, else None."""
    try:
        line, rest = out.decode("utf-8").split("\n", 1)
        answer = json.loads(line)
    except (UnicodeDecodeError, ValueError, RecursionError):
        return None
    return answer if rest == "" and isinstance(answer, dict) else None


def run_helper(request: Dict, uid: Optional[int], gid: Optional[int],
               timeout: float = HELPER_TIMEOUT_SECONDS) -> Optional[Dict]:
    """The helper's answer to request, run as uid/gid when the agent is root, or None when it gave none."""
    as_user = {}
    if _euid() == 0:
        if uid is None or gid is None or uid == 0:
            return None
        as_user = {"user": uid, "group": gid, "extra_groups": []}
    try:
        child = subprocess.Popen(helper_command(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, env={}, cwd="/", close_fds=True, **as_user)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        logger.warning(f"Memory fix helper did not start: {type(e).__name__}")
        return None
    try:
        out, _ = child.communicate(json.dumps(request).encode("utf-8"), timeout=timeout)
    except Exception as e:
        logger.warning(f"Memory fix helper gave no answer: {type(e).__name__}")
        try:
            child.kill()
            child.communicate(timeout=5)
        except Exception as reap_error:
            logger.warning(f"Memory fix helper not reaped: {type(reap_error).__name__}")
        return None
    return _one_json_line(out) if child.returncode == 0 else None


def _switched_on() -> bool:
    return os.environ.get(KILL_SWITCH_ENV, "").strip().lower() not in _FALSEY


def _owner(home_user: str) -> Optional[Tuple[int, int]]:
    """(uid, gid) of the OS user, or None when the name is empty or unknown."""
    if not home_user:
        return None
    try:
        import pwd  # POSIX only; Windows never reaches here.
        entry = pwd.getpwnam(home_user)
    except (ImportError, KeyError, TypeError, ValueError):
        return None
    return entry.pw_uid, entry.pw_gid


def _home_owner(home: Optional[str]) -> Optional[Tuple[int, int]]:
    """(uid, primary gid) of the account owning the home folder; the folder's name is not always the account's."""
    try:
        import pwd  # POSIX only; Windows never reaches here.
        entry = pwd.getpwuid(os.stat(home).st_uid)
    except (ImportError, KeyError, OSError, TypeError, ValueError):
        return None
    return entry.pw_uid, entry.pw_gid


def supports_fixes(users: List[str]) -> bool:
    """True when this is a macOS root run, the kill switch is not off and the helper answers as a scanned user."""
    try:
        if sys.platform != "darwin" or _euid() != 0 or not _switched_on():
            return False
        owner = next((owner for owner in map(_owner, users) if owner and owner[0] != 0), None)
        answer = owner and run_helper({"op": "ping"}, *owner, timeout=PING_TIMEOUT_SECONDS)
        return bool(answer) and answer.get("ok") is True and answer.get("protocol") == PROTOCOL
    except Exception as e:
        logger.warning(f"Memory fix self-test failed: {type(e).__name__}")
        return False


def held_files(home_user: str, home: Path, memories: Optional[Dict]) -> List[Dict]:
    """The user-scope memory files under home/.claude that this run reported, as the report named them."""
    claude_dir = posixpath.normpath(str(home / ".claude")) + "/"
    held = []
    for folder in (memories or {}).get("folders") or []:
        root = folder.get("path")
        if folder.get("scope") != "user" or not isinstance(root, str):
            continue
        if not (posixpath.normpath(root) + "/").startswith(claude_dir):
            continue
        for item in [folder.get("index")] + list(folder.get("flagged") or []):
            if isinstance(item, dict) and isinstance(item.get("file_path"), str) and not item.get("truncated"):
                held.append({"home_user": home_user, "home": str(home), "file_path": item["file_path"],
                             "memory_root": root})
    return held


def _file_sha256(candidate: Dict) -> Optional[str]:
    """sha256 of the file's bytes through the scan's own safe open, or None when it is not whole strict UTF-8."""
    opened = _open_checked(Path(candidate["file_path"]), Path(candidate["memory_root"]), False)
    if opened is None:
        return None
    fd, file_stat = opened
    try:
        if file_stat.st_size > MAX_FILE_BYTES:
            return None
        data = b""
        while len(data) <= MAX_FILE_BYTES:
            chunk = os.read(fd, MAX_FILE_BYTES + 1 - len(data))
            if not chunk:
                break
            data += chunk
        if len(data) > MAX_FILE_BYTES:
            return None
        data.decode("utf-8", errors="strict")
        return hashlib.sha256(data).hexdigest()
    except (OSError, UnicodeDecodeError):
        return None
    finally:
        os.close(fd)


def _post_json(link: _Link, path: str, body: Dict) -> Optional[Dict]:
    """The backend's JSON answer to one POST, or None when it failed or was not a JSON object."""
    timeout = clip_to_deadline(REQUEST_TIMEOUT_SECONDS, link.deadline)
    if timeout is None:
        return None
    try:
        ok, status, text, err = _curl_post_json(
            f"{normalize_url(link.domain)}{path}", link.api_key, body, timeout,
            kill_grace=curl_kill_grace(link.deadline))
        if not ok or status is None or not 200 <= status < 300:
            logger.warning(f"Memory fix post to {path} failed: status={status} err={err}")
            return None
        answer = json.loads(text)
    except Exception as e:
        logger.warning(f"Memory fix post to {path} failed: {type(e).__name__}")
        return None
    return answer if isinstance(answer, dict) else None


def _placements(link: _Link, posted: List[Dict]) -> Optional[List[Dict]]:
    """Every answer the backend gave for the posted files, or None when a post failed."""
    answers = []
    for start in range(0, len(posted), FILES_PER_POST):
        files = [{key: entry[key] for key in ("home_user", "file_path", "sha256")}
                 for entry in posted[start:start + FILES_PER_POST]]
        answer = _post_json(link, FIXES_PATH, {"device_id": link.device_id, "files": files})
        fixes = answer.get("fixes") if answer else None
        if not isinstance(fixes, list):
            return None
        answers.extend(fix for fix in fixes if isinstance(fix, dict))
    return answers


def _post_results(link: _Link, results: List[Dict]) -> bool:
    """True when every result reached the backend; a post that fails is tried again, as the edit is already made."""
    sent = True
    for start in range(0, len(results), RESULTS_PER_POST):
        body = {"device_id": link.device_id, "results": results[start:start + RESULTS_PER_POST]}
        if not any(_post_json(link, RESULTS_PATH, body) is not None for _ in range(RESULT_POST_TRIES)):
            sent = False
    return sent


def _spans(answer: Dict) -> Optional[List[List[int]]]:
    spans = answer.get("spans")
    if not isinstance(spans, list):
        return None
    pairs = [[span.get("start"), span.get("end")] for span in spans if isinstance(span, dict)]
    valid = all(isinstance(edge, int) and not isinstance(edge, bool) for pair in pairs for edge in pair)
    return pairs if valid and len(pairs) == len(spans) else None


def _apply(entry: Dict, answer: Dict, deadline: Optional[float]) -> Optional[Dict]:
    """The result to post for one ready fix, or None when there is nothing to report yet."""
    fix_id, token, spans = answer.get("id"), answer.get("token"), _spans(answer)
    timeout = clip_to_deadline(HELPER_TIMEOUT_SECONDS, deadline)
    if not isinstance(fix_id, int) or isinstance(fix_id, bool) or not isinstance(token, str) or not spans:
        return None
    if timeout is None:
        return None
    owner = _home_owner(entry.get("home")) if entry.get("home_user") else None
    if owner is None:
        return None
    if owner[0] == 0:
        return {"id": fix_id, "outcome": "failed", "reason": "refused", "token": token}
    reply = run_helper({"op": "fix", "file_path": entry["file_path"], "memory_root": entry["memory_root"],
                        "sha256": entry["sha256"], "spans": spans}, *owner, timeout=timeout)
    outcome = (reply or {}).get("outcome")
    if outcome == "fixed":
        return {"id": fix_id, "outcome": "fixed", "token": token}
    if outcome == "failed" and reply.get("reason") in FAILURE_REASONS:
        return {"id": fix_id, "outcome": "failed", "reason": reply["reason"], "token": token}
    return None


def _tally(result: Optional[Dict]) -> str:
    if result is None:
        return "no_answer"
    return "fixed" if result["outcome"] == "fixed" else "failed_" + result["reason"]


def _hashed(waiting: Dict[Tuple[str, str], Dict], counts: Counter) -> List[Dict]:
    """The waiting files with their current sha256; a file that cannot be hashed is dropped for this run."""
    posted = []
    for key, candidate in list(waiting.items()):
        digest = _file_sha256(candidate)
        if digest is None:
            del waiting[key]
            counts["not_posted"] += 1
        else:
            posted.append({**candidate, "sha256": digest})
    return posted


def _round(link: _Link, waiting: Dict[Tuple[str, str], Dict], counts: Counter) -> bool:
    """One post of the waiting files and the fixes it hands out; False when the backend could not be asked."""
    posted = {(entry["home_user"], entry["file_path"]): entry for entry in _hashed(waiting, counts)}
    answers = _placements(link, list(posted.values())) if posted else []
    if answers is None:
        return False
    results, still_pending = [], set()
    try:
        for answer in answers:
            try:
                key = (answer.get("home_user"), answer.get("file_path"))
                if not all(isinstance(part, str) for part in key) or key not in posted:
                    continue
                status = answer.get("status")
                if status == "pending":
                    still_pending.add(key)
                elif status != "ready":
                    counts["changed" if status == "changed" else "unknown_status"] += 1
                else:
                    result = _apply(posted[key], answer, link.deadline)
                    counts[_tally(result)] += 1
                    if result:
                        results.append(result)
            except Exception as e:
                counts["answer_error"] += 1
                logger.warning(f"Memory fix answer skipped: {type(e).__name__}")
    finally:
        # Only a file the backend called pending is asked about again; any other has no fix waiting on this run.
        for key in [key for key in waiting if key not in still_pending]:
            del waiting[key]
        # Posted even if an answer went wrong: an edit already made must not go unreported.
        if not _post_results(link, results):
            counts["results_post_failed"] += 1
    return True


def _poll(link: _Link, candidates: List[Dict], counts: Counter) -> None:
    waiting = {(candidate["home_user"], candidate["file_path"]): candidate for candidate in candidates}
    stop_at = time.monotonic() + POLL_WINDOW_SECONDS
    if link.deadline is not None:
        stop_at = min(stop_at, link.deadline)
    while waiting:
        counts["rounds"] += 1
        if not _round(link, waiting, counts):
            counts["post_failed"] += 1
            return
        if not waiting or time.monotonic() + POLL_INTERVAL_SECONDS >= stop_at:
            return
        time.sleep(POLL_INTERVAL_SECONDS)


def apply_pending_fixes(domain: str, api_key: str, device_id: str, candidates: List[Dict], *,
                        deadline: Optional[float] = None) -> Dict[str, int]:
    """Applies the fixes the backend hands out for the candidates and reports them; never raises."""
    counts: Counter = Counter()
    try:
        link = _Link(domain, api_key, device_id, deadline)
        _poll(link, candidates, counts)
        logger.info("Memory file fixes: " + (", ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"))
    except Exception as e:
        logger.warning(f"Memory file fixes stopped: {type(e).__name__}")
    return dict(counts)
