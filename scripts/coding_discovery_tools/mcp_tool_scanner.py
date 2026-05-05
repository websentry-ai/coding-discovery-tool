"""
MCP tool scanner — pure stdlib, no external dependencies.

Given an MCP server config dict (either stdio-style with command/args/env, or
HTTP-style with url/headers), connects to the server and lists its tools via
the MCP JSON-RPC protocol. Safe to run alongside the rest of the discovery
tool: all failures are caught and reported as a `status` field in the result.

Per the repo CLAUDE.md, HTTP is performed via curl subprocess (not urllib) to
work on machines with Zscaler or similar corporate MITM root CAs. Stdio uses
subprocess.Popen with a threaded line reader so it works identically on macOS
and Windows.

Usage:
    from .mcp_tool_scanner import scan_mcp_server
    result = scan_mcp_server({"url": "https://mcp.context7.com/mcp"})
    # result["status"] -> "scanned" | "auth_required" | ...
    # result["tool_names"] -> [str]
"""

import datetime
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "unbound-coding-discovery", "version": "0.1"}

# Total budget per server, in seconds. Kept under the 30s COMMAND_TIMEOUT
# used elsewhere in the codebase.
DEFAULT_TIMEOUT = 25
# Per-RPC read timeout once the subprocess/http session is established.
STDIO_RPC_TIMEOUT = 15
HTTP_RPC_TIMEOUT = 15
# Cap on how many tools/list pages to fetch before giving up (safety).
MAX_PAGINATION_LOOPS = 20
# Cap on stderr capture from stdio servers.
MAX_STDERR_BYTES = 16 * 1024

USER_AGENT = "unbound-mcp-scanner/0.1"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def scan_mcp_server(
    server_config: Dict[str, Any],
    timeout: int = DEFAULT_TIMEOUT,
) -> Dict[str, Any]:
    """Scan one MCP server.

    `server_config` is the raw dict as it appears in an MCP config file
    (before env/headers are stripped for upload). Accepts both Cursor-style
    (url + headers) and stdio-style (command + args + env) shapes.

    Returns a dict with at least:
      - status       : "scanned" | "scanned_partial" | "scanned_empty" |
                       "tools_list_failed" | "auth_required" | "http_error" |
                       "transport_error" | "package_not_found" |
                       "startup_error" | "missing_credentials" |
                       "process_exited" | "protocol_error" | "timeout" |
                       "command_not_found" | "spawn_error" | "scanner_error" |
                       "unknown_config_shape"
      - transport    : "stdio" | "streamable_http" (when applicable)
      - tool_count   : int (when applicable)
      - tool_names   : [str] (when applicable)
      - tools        : [dict] full tool objects with inputSchema (when scanned)
      - server_info  : {name, version, ...} (when handshake succeeded)
      - scanned_at   : ISO-8601 UTC timestamp of the attempt
    """
    scanned_at = _utc_now_iso()
    url = server_config.get("url")
    command = server_config.get("command")

    try:
        if command:
            result = _scan_stdio(
                command=command,
                args=list(server_config.get("args") or []),
                env_extra=dict(server_config.get("env") or {}),
                timeout=timeout,
            )
            result.setdefault("transport", "stdio")
        elif url:
            result = _scan_http(
                url=url,
                extra_headers=dict(server_config.get("headers") or {}),
                timeout=timeout,
            )
            result.setdefault("transport", "streamable_http")
        else:
            result = {"status": "unknown_config_shape"}
    except Exception as exc:  # defence in depth — never let the scanner crash the caller
        logger.warning("scan_mcp_server unexpected error: %s", exc, exc_info=True)
        result = {"status": "scanner_error", "error": f"{type(exc).__name__}: {exc}"}

    result["scanned_at"] = scanned_at
    return result


# ---------------------------------------------------------------------------
# JSON-RPC payloads and shared helpers
# ---------------------------------------------------------------------------

def _init_request(req_id: int) -> Dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO,
        },
    }


def _initialized_notification() -> Dict[str, Any]:
    return {"jsonrpc": "2.0", "method": "notifications/initialized"}


def _tools_list_request(req_id: int, cursor: Optional[str]) -> Dict[str, Any]:
    params: Dict[str, Any] = {"cursor": cursor} if cursor else {}
    return {"jsonrpc": "2.0", "id": req_id, "method": "tools/list", "params": params}


def _utc_now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def _parse_sse_blob(text: str) -> List[Dict[str, Any]]:
    """Parse an SSE body into a list of {event, data or data_raw} dicts."""
    events: List[Dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", text):
        event_name = "message"
        data_lines: List[str] = []
        for line in block.splitlines():
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip() or "message"
            elif line.startswith("data:"):
                data_lines.append(line[len("data:"):].lstrip())
        if not data_lines:
            continue
        raw = "\n".join(data_lines)
        try:
            events.append({"event": event_name, "data": json.loads(raw)})
        except json.JSONDecodeError:
            events.append({"event": event_name, "data_raw": raw})
    return events


def _rpc_from_body(body: str, content_type: str) -> Optional[Dict[str, Any]]:
    if "text/event-stream" in (content_type or "").lower():
        for ev in _parse_sse_blob(body):
            if isinstance(ev.get("data"), dict):
                return ev["data"]
        return None
    try:
        return json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# stdio transport (subprocess with JSON-RPC over stdin/stdout)
# ---------------------------------------------------------------------------

class _LineReader:
    """Background thread that reads newline-delimited bytes from a stream into
    a queue, so the caller can `readline(timeout=...)`. Works on macOS and
    Windows (plain `select` on subprocess stdout doesn't on Windows)."""

    _EOF = object()

    def __init__(self, stream):
        self._stream = stream
        self._q: "queue.Queue[Any]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                line = self._stream.readline()
                if not line:
                    self._q.put(self._EOF)
                    return
                self._q.put(line)
        except Exception:  # closing the pipe races with the read
            self._q.put(self._EOF)

    def readline(self, timeout: float) -> Optional[bytes]:
        """Returns None on timeout, b"" on EOF, otherwise the raw line bytes."""
        try:
            item = self._q.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is self._EOF:
            return b""
        return item


class _StderrCollector:
    """Buffered tail of stderr. Capped by MAX_STDERR_BYTES."""

    def __init__(self, stream):
        self._stream = stream
        self._chunks: List[bytes] = []
        self._total = 0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            while True:
                chunk = self._stream.read(4096)
                if not chunk:
                    return
                with self._lock:
                    if self._total < MAX_STDERR_BYTES:
                        self._chunks.append(chunk)
                        self._total += len(chunk)
        except Exception:
            return

    def text(self) -> str:
        with self._lock:
            return b"".join(self._chunks).decode("utf-8", errors="replace")


def _stdio_send(proc: subprocess.Popen, payload: Dict[str, Any]) -> None:
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(payload) + "\n").encode("utf-8"))
    proc.stdin.flush()


def _stdio_rpc(
    proc: subprocess.Popen,
    reader: _LineReader,
    payload: Dict[str, Any],
    timeout: float,
) -> Optional[Dict[str, Any]]:
    """Send a request, read lines until we see a response with matching id.

    Returns None on timeout or EOF. Non-matching messages (other notifications)
    are discarded. Non-JSON lines on stdout are tolerated and skipped."""
    target_id = payload.get("id")
    _stdio_send(proc, payload)

    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        line = reader.readline(timeout=remaining)
        if line is None:
            return None  # timeout
        if line == b"":
            return None  # EOF
        try:
            msg = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        if msg.get("id") == target_id:
            return msg
        # else: a notification or a response to another id — ignore


def _kill_process(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=1)
                except Exception:
                    pass
    except Exception:
        pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


def _classify_stderr(stderr_text: str, exit_code: Optional[int]) -> Dict[str, Any]:
    text = stderr_text or ""
    # npm registry 404
    if "npm error code E404" in text or "npm ERR! 404" in text:
        match = re.search(r"'([^']+?)@[^']*' could not be found", text)
        return {
            "status": "package_not_found",
            "reason": "npm_registry_404",
            "package": match.group(1) if match else None,
            "exit_code": exit_code,
        }
    if "ENOENT" in text:
        match = re.search(r"ENOENT[^\n]*'([^']+)'", text)
        return {
            "status": "startup_error",
            "reason": "enoent",
            "path": match.group(1) if match else None,
            "exit_code": exit_code,
        }
    if re.search(r"(missing|required)[^\n]*(token|api[_ ]?key|credential)", text, re.IGNORECASE):
        return {"status": "missing_credentials", "exit_code": exit_code}
    return {"status": "process_exited", "exit_code": exit_code}


def _scan_stdio(
    command: str,
    args: List[str],
    env_extra: Dict[str, str],
    timeout: int,
) -> Dict[str, Any]:
    resolved = shutil.which(command) or command
    env = os.environ.copy()
    for k, v in env_extra.items():
        if isinstance(v, str):
            env[k] = v

    try:
        proc = subprocess.Popen(
            [resolved, *args],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
    except FileNotFoundError:
        return {"status": "command_not_found", "command": command}
    except OSError as exc:
        return {"status": "spawn_error", "error": f"{type(exc).__name__}: {exc}"}

    out_reader = _LineReader(proc.stdout)
    err_collector = _StderrCollector(proc.stderr)

    try:
        # initialize
        init_resp = _stdio_rpc(
            proc, out_reader, _init_request(1),
            timeout=min(timeout, STDIO_RPC_TIMEOUT * 2),
        )
        if init_resp is None:
            if proc.poll() is not None:
                # subprocess died before responding — classify via stderr
                stderr_text = err_collector.text()
                out = _classify_stderr(stderr_text, exit_code=proc.returncode)
                out["stderr_tail"] = stderr_text[-1500:] if stderr_text else None
                return out
            return {"status": "timeout", "stage": "initialize"}

        if "result" not in init_resp:
            return {
                "status": "protocol_error",
                "stage": "initialize",
                "parsed": init_resp,
            }

        server_info = init_resp["result"].get("serverInfo")
        capabilities = init_resp["result"].get("capabilities", {})
        negotiated_version = init_resp["result"].get("protocolVersion")

        # initialized notification (no response)
        try:
            _stdio_send(proc, _initialized_notification())
        except Exception:
            pass

        tools: List[Dict[str, Any]] = []
        cursor: Optional[str] = None
        next_id = 2
        errors: List[str] = []

        for _ in range(MAX_PAGINATION_LOOPS):
            tl_resp = _stdio_rpc(
                proc, out_reader,
                _tools_list_request(next_id, cursor),
                timeout=STDIO_RPC_TIMEOUT,
            )
            if tl_resp is None:
                if proc.poll() is not None:
                    errors.append(f"process exited: rc={proc.returncode}")
                else:
                    errors.append(f"tools/list timeout (id={next_id})")
                break
            next_id += 1
            if "result" not in tl_resp:
                errors.append("tools/list: no result")
                break
            result = tl_resp["result"]
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break

        if tools and not errors:
            status = "scanned"
        elif tools:
            status = "scanned_partial"
        elif errors:
            # No tools AND tools/list reported errors — this is a failure,
            # not a server with an empty catalog. Surface it so consumers
            # don't treat it as a successful zero-tool scan.
            status = "tools_list_failed"
        else:
            status = "scanned_empty"

        return {
            "status": status,
            "protocol_version": negotiated_version,
            "server_info": server_info,
            "capabilities": capabilities,
            "tool_count": len(tools),
            "tool_names": [t.get("name") for t in tools],
            "tools": tools,
            "errors": errors or None,
        }
    finally:
        _kill_process(proc)


# ---------------------------------------------------------------------------
# Streamable HTTP transport (curl-based per CLAUDE.md)
# ---------------------------------------------------------------------------

def _build_headers(
    session_id: Optional[str],
    extra: Optional[Dict[str, str]],
) -> Dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
        "User-Agent": USER_AGENT,
    }
    if session_id:
        h["Mcp-Session-Id"] = session_id
    if extra:
        h.update(extra)
    return h


def _curl_request(
    method: str,
    url: str,
    headers: Dict[str, str],
    body: Optional[str],
    timeout: int,
) -> Tuple[Optional[int], Optional[Dict[str, str]], Optional[str], Optional[str]]:
    """Run a curl request. Returns (status_code, headers_lowercased, body, error).

    Uses `-i` so status+headers+body come through stdout. No redirect follow.
    Headers are piped to curl via `-K -` (config-file mode read from stdin)
    rather than `-H` flags so bearer tokens never appear in the OS process
    table where any local user could read them with `ps`."""
    args = ["curl", "-sS", "-i", "-X", method, "--max-time", str(timeout), "-K", "-"]
    if body is not None:
        args += ["--data-binary", body]
    args.append(url)

    config = "".join(
        f'header = "{_curl_config_quote(f"{k}: {v}")}"\n'
        for k, v in headers.items()
    )

    try:
        completed = subprocess.run(
            args,
            input=config.encode("utf-8"),
            capture_output=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        return None, None, None, "timeout"
    except Exception as exc:
        return None, None, None, f"subprocess: {type(exc).__name__}: {exc}"

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        return None, None, None, f"curl exit {completed.returncode}: {stderr[:200]}"

    raw = completed.stdout.decode("utf-8", errors="replace")
    return _parse_curl_response(raw)


def _curl_config_quote(value: str) -> str:
    """Escape a string for inclusion inside a curl `-K` config-file double-
    quoted value. curl supports backslash escapes for `\\`, `"`, `\\t`, `\\n`,
    `\\r`, `\\v` — anything else we leave as-is."""
    return (
        value.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("\t", "\\t")
    )


def _parse_curl_response(
    raw: str,
) -> Tuple[Optional[int], Optional[Dict[str, str]], Optional[str], Optional[str]]:
    """Parse `curl -i` output: possibly multiple 'HTTP/...\\n<hdrs>\\n\\n' blocks
    if there were intermediate responses (100-continue, etc.), then body."""
    remaining = raw
    last_status = None
    last_headers: Dict[str, str] = {}
    body = ""

    while True:
        parts = re.split(r"\r?\n\r?\n", remaining, maxsplit=1)
        if len(parts) < 2:
            body = remaining
            break
        header_block, rest = parts
        lines = header_block.splitlines()
        if not lines:
            body = rest
            break
        status_match = re.match(r"HTTP/\d+(?:\.\d+)?\s+(\d+)", lines[0])
        if not status_match:
            body = remaining
            break
        status = int(status_match.group(1))
        hdrs: Dict[str, str] = {}
        for line in lines[1:]:
            if ":" in line:
                k, v = line.split(":", 1)
                hdrs[k.strip().lower()] = v.strip()

        # An informational 1xx response is followed by another header block.
        # A 3xx redirect with curl (no -L) ends here.
        if 100 <= status < 200:
            remaining = rest
            continue

        last_status = status
        last_headers = hdrs
        body = rest
        break

    if last_status is None:
        return None, None, body, "could not parse HTTP status line"
    return last_status, last_headers, body, None


def _alt_transport_url(url: str) -> Optional[str]:
    """Many hosted MCP servers expose /mcp (Streamable HTTP) and /sse (legacy SSE)
    as parallel endpoints. If POST to one returns 404/405, the other is worth a try."""
    for a, b in (("/sse", "/mcp"), ("/mcp", "/sse")):
        if url.endswith(a):
            return url[: -len(a)] + b
    return None


def _scan_http(
    url: str,
    extra_headers: Dict[str, str],
    timeout: int,
    _tried_alt: bool = False,
) -> Dict[str, Any]:
    errors: List[str] = []

    # initialize
    init_headers = _build_headers(session_id=None, extra=extra_headers)
    init_body = json.dumps(_init_request(1))
    status, resp_headers, body, err = _curl_request(
        "POST", url, init_headers, init_body, timeout=min(timeout, HTTP_RPC_TIMEOUT),
    )
    if err:
        return {"status": "transport_error", "error": err}

    # If POST-to-/sse 404/405s, retry against /mcp — common aliasing pattern.
    if not _tried_alt and status in (404, 405):
        alt = _alt_transport_url(url)
        if alt:
            alt_result = _scan_http(alt, extra_headers, timeout, _tried_alt=True)
            if alt_result.get("status") != "http_error":
                alt_result["scanned_url"] = alt
                alt_result["original_url"] = url
                return alt_result

    if status in (401, 403):
        www = (resp_headers or {}).get("www-authenticate", "") or ""
        meta_url = _resource_metadata_url(www)
        oauth = _fetch_oauth_metadata(meta_url, timeout) if meta_url else None
        return {
            "status": "auth_required",
            "http_status": status,
            "www_authenticate": www or None,
            "oauth": oauth,
        }

    if status is None or status >= 400:
        return {
            "status": "http_error",
            "http_status": status,
            "body_excerpt": (body or "")[:300],
        }

    content_type = (resp_headers or {}).get("content-type", "")
    init_msg = _rpc_from_body(body or "", content_type)
    if not init_msg or "result" not in init_msg:
        return {
            "status": "protocol_error",
            "http_status": status,
            "content_type": content_type,
            "body_excerpt": (body or "")[:300],
        }

    session_id = (resp_headers or {}).get("mcp-session-id")
    server_info = init_msg["result"].get("serverInfo")
    capabilities = init_msg["result"].get("capabilities", {})
    negotiated_version = init_msg["result"].get("protocolVersion")

    # initialized notification (best effort)
    try:
        _curl_request(
            "POST", url,
            _build_headers(session_id, extra_headers),
            json.dumps(_initialized_notification()),
            timeout=min(timeout, HTTP_RPC_TIMEOUT),
        )
    except Exception:
        pass

    # tools/list with pagination
    tools: List[Dict[str, Any]] = []
    cursor: Optional[str] = None
    next_id = 2
    for _ in range(MAX_PAGINATION_LOOPS):
        tl_body = json.dumps(_tools_list_request(next_id, cursor))
        st, rh, bd, e = _curl_request(
            "POST", url,
            _build_headers(session_id, extra_headers),
            tl_body,
            timeout=min(timeout, HTTP_RPC_TIMEOUT),
        )
        if e:
            errors.append(f"tools/list: {e}")
            break
        if st is None or st >= 400:
            errors.append(f"tools/list HTTP {st}")
            break
        ct = (rh or {}).get("content-type", "")
        msg = _rpc_from_body(bd or "", ct)
        if not msg or "result" not in msg:
            errors.append(f"tools/list: no result (ct={ct})")
            break
        result = msg["result"]
        tools.extend(result.get("tools") or [])
        cursor = result.get("nextCursor")
        next_id += 1
        if not cursor:
            break

    if tools and not errors:
        status_out = "scanned"
    elif tools:
        status_out = "scanned_partial"
    elif errors:
        status_out = "tools_list_failed"
    else:
        status_out = "scanned_empty"

    return {
        "status": status_out,
        "protocol_version": negotiated_version,
        "server_info": server_info,
        "capabilities": capabilities,
        "tool_count": len(tools),
        "tool_names": [t.get("name") for t in tools],
        "tools": tools,
        "errors": errors or None,
    }


# ---------------------------------------------------------------------------
# OAuth metadata enrichment (RFC 9728 / RFC 8414 / OIDC discovery)
# ---------------------------------------------------------------------------

def _resource_metadata_url(www_authenticate: str) -> Optional[str]:
    match = re.search(r'resource_metadata="([^"]+)"', www_authenticate or "")
    return match.group(1) if match else None


def _curl_get_json(url: str, timeout: int) -> Tuple[Optional[int], Optional[Dict[str, Any]]]:
    status, _, body, err = _curl_request(
        "GET", url, {"Accept": "application/json", "User-Agent": USER_AGENT},
        body=None, timeout=timeout,
    )
    if err or status is None or status >= 400 or not body:
        return status, None
    try:
        return status, json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return status, None


def _fetch_oauth_metadata(
    resource_metadata_url: str,
    timeout: int,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {"resource_metadata_url": resource_metadata_url}
    status, rm = _curl_get_json(resource_metadata_url, timeout)
    if rm is None:
        out["error"] = f"could not fetch protected-resource metadata (status={status})"
        return out
    out["resource_metadata"] = rm

    authorization_servers = rm.get("authorization_servers") or []
    out["authorization_servers"] = []
    for issuer in authorization_servers:
        entry: Dict[str, Any] = {"issuer": issuer}
        candidates = [
            issuer.rstrip("/") + "/.well-known/oauth-authorization-server",
            issuer.rstrip("/") + "/.well-known/openid-configuration",
        ]
        for well_known in candidates:
            _, body = _curl_get_json(well_known, timeout)
            if not body:
                continue
            entry["metadata_url"] = well_known
            for field in (
                "authorization_endpoint",
                "token_endpoint",
                "registration_endpoint",
                "scopes_supported",
                "grant_types_supported",
                "code_challenge_methods_supported",
            ):
                entry[field] = body.get(field)
            break
        out["authorization_servers"].append(entry)
    return out


# ---------------------------------------------------------------------------
# Parallel helper for callers that have many servers to scan.
# ---------------------------------------------------------------------------

def scan_many(
    servers: List[Tuple[str, Dict[str, Any]]],
    max_workers: int = 4,
    timeout: int = DEFAULT_TIMEOUT,
    progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
) -> List[Tuple[str, Dict[str, Any]]]:
    """Scan multiple servers in parallel via a thread pool.

    `servers` is a list of (label, config) tuples. Returns a list of
    (label, result) in the same order. `progress`, if provided, is called
    once per server as it completes."""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: Dict[int, Tuple[str, Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_index = {
            pool.submit(scan_mcp_server, cfg, timeout): i
            for i, (_, cfg) in enumerate(servers)
        }
        for future in as_completed(future_to_index):
            i = future_to_index[future]
            label, _ = servers[i]
            try:
                res = future.result()
            except Exception as exc:  # belt and suspenders
                res = {
                    "status": "scanner_error",
                    "error": f"{type(exc).__name__}: {exc}",
                    "scanned_at": _utc_now_iso(),
                }
            results[i] = (label, res)
            if progress:
                try:
                    progress(label, res)
                except Exception:
                    pass

    return [results[i] for i in range(len(servers))]
