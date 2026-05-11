"""
3-step S3 upload flow for the coding discovery payload.

Step 1: POST /api/v1/ai-tools/report/upload-url/  → presigned PUT URL + object_key
Step 2: PUT  <presigned_url>                       → S3 stores the JSON
Step 3: POST /api/v1/ai-tools/report/from-s3/      → backend creates inbox row + queues Celery

Any failure in any step returns (False, retryable=True) so the caller
(``send_report_to_backend``) falls back to the legacy /api/v1/ai-tools/report/
endpoint, which has its own 3-attempt curl retry. We deliberately do NOT
retry the S3 path itself: keeping it single-attempt is simpler and the legacy
fallback already covers transient failures.

curl is used exclusively (per ``CLAUDE.md``: never urllib — Zscaler intercepts HTTPS
with a custom CA that urllib can't see).
"""
import json
import logging
import os
import subprocess
import tempfile
from typing import Dict, Optional, Tuple

from .utils import normalize_url, report_to_sentry

logger = logging.getLogger(__name__)

UPLOAD_URL_PATH = "/api/v1/ai-tools/report/upload-url/"
FROM_S3_PATH = "/api/v1/ai-tools/report/from-s3/"

# Per-step curl timeouts. Step 2 (the actual upload) gets the longest budget
# because the body can reach 15 MB on slow connections.
META_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 180

# Aggressive connect timeout so a customer firewall silently dropping S3 traffic
# fails fast (~10s) instead of stalling the whole user-facing scan for the full
# --max-time budget. Falls back to the legacy endpoint immediately on connect failure.
CONNECT_TIMEOUT_SECONDS = 10


def should_use_s3(payload: Dict) -> bool:
    """
    Only data reports (payload with a non-empty ``tools`` array) should go
    through S3. Scan-lifecycle events (in_progress/completed/failed) and
    metrics-only calls are tiny and stay on the legacy endpoint.
    """
    if payload.get("scan_event"):
        return False
    tools = payload.get("tools")
    return isinstance(tools, list) and len(tools) > 0


def try_s3_upload(
    backend_url: str,
    api_key: str,
    payload: Dict,
    sentry_context: Optional[Dict] = None,
) -> Tuple[bool, bool]:
    """
    Run the 3-step flow. Returns (success, retryable).

    `payload` is the full report dict and is expected to already carry any
    optional fields (``app_name``, ``sentry_metrics``) — the caller in
    ``utils.send_report_to_backend`` adds them before invoking us.

    On any failure, returns (False, True) — the caller treats this as
    "fall back to legacy endpoint and let the legacy retry/queue logic handle it".
    """
    base = normalize_url(backend_url)
    ctx = sentry_context or {}

    # ─── Step 1: get presigned URL ──────────────────────────────────────
    meta_body = {"device_id": payload.get("device_id")}
    if payload.get("run_id"):
        meta_body["run_id"] = payload["run_id"]

    ok, status, body, err = _curl_post_json(
        f"{base}{UPLOAD_URL_PATH}", api_key, meta_body, META_TIMEOUT_SECONDS,
    )
    if not ok:
        _report_step_failure("upload_url_request", status, body, err, ctx)
        return False, True
    if status != 200:
        # 503 means S3 not configured on the backend; quietly fall back.
        # Anything else is logged.
        if status != 503:
            _report_step_failure("upload_url_request", status, body, None, ctx)
        return False, True

    try:
        url_response = json.loads(body or "{}")
        upload_url = url_response["upload_url"]
        object_key = url_response["object_key"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        logger.error(f"S3 step 1: malformed upload-url response: {e}; body={body[:200] if body else ''}")
        report_to_sentry(e, {**ctx, "phase": "upload_url_parse"}, level="warning")
        return False, True

    # ─── Step 2: PUT to S3 ──────────────────────────────────────────────
    try:
        s3_payload_json = json.dumps(payload)
    except (TypeError, ValueError) as e:
        logger.error(f"S3 step 2: failed to serialize payload: {e}")
        report_to_sentry(e, {**ctx, "phase": "upload_serialize"}, level="warning")
        return False, True

    ok, status, body, err = _curl_put_to_s3(upload_url, s3_payload_json)
    if not ok or status is None or not (200 <= status < 300):
        _report_step_failure("s3_put", status, body, err, ctx)
        return False, True

    # ─── Step 3: notify backend ─────────────────────────────────────────
    # Mirror exactly what the legacy POST sends as top-level metadata so the
    # backend's /from-s3/ handler receives the same context (including
    # `app_name` for MDM-tagged scans like JumpCloud).
    notify_body = {
        "device_id": payload.get("device_id"),
        "object_key": object_key,
        "system_user": payload.get("system_user"),
        "home_user": payload.get("home_user"),
        "run_id": payload.get("run_id"),
        "app_name": payload.get("app_name"),
        "sentry_metrics": payload.get("sentry_metrics"),
    }
    # Strip None values to avoid sending bogus keys.
    notify_body = {k: v for k, v in notify_body.items() if v is not None}

    ok, status, body, err = _curl_post_json(
        f"{base}{FROM_S3_PATH}", api_key, notify_body, META_TIMEOUT_SECONDS,
    )
    if not ok:
        _report_step_failure("from_s3_request", status, body, err, ctx)
        return False, True
    if not (200 <= status < 300):
        _report_step_failure("from_s3_request", status, body, None, ctx)
        return False, True

    return True, False


# ───────────────────────────────────────────────────────────────────────
# Internal helpers
# ───────────────────────────────────────────────────────────────────────

def _curl_post_json(url: str, api_key: str, body: Dict, timeout: int):
    """
    Returns (ok, status_code_or_None, response_body, stderr_or_None).
    `ok` is False when curl itself errored (DNS, connection, SSL).
    """
    try:
        body_json = json.dumps(body)
    except (TypeError, ValueError) as e:
        return False, None, "", f"serialize: {e}"

    try:
        result = subprocess.run(
            [
                "curl", "-s",
                "-X", "POST",
                "-H", f"Authorization: Bearer {api_key}",
                "-H", "Content-Type: application/json",
                "-H", "User-Agent: AI-Tools-Discovery/1.0",
                "-d", body_json,
                "--connect-timeout", str(CONNECT_TIMEOUT_SECONDS),
                "--max-time", str(timeout),
                "-w", "\n%{http_code}",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 5,
        )
    except subprocess.TimeoutExpired:
        return False, None, "", "timeout"
    except Exception as e:
        return False, None, "", str(e)

    return _parse_curl(result)


def _curl_put_to_s3(upload_url: str, body_json: str):
    """
    PUT the JSON body to a presigned S3 URL.
    Writes to a temp file first to avoid ARG_MAX exhaustion on large payloads.
    """
    try:
        fd, tmp_path = tempfile.mkstemp(prefix="ai-discovery-s3-", suffix=".json")
    except OSError as e:
        return False, None, "", f"tmpfile: {e}"

    try:
        try:
            os.write(fd, body_json.encode("utf-8"))
        finally:
            os.close(fd)

        try:
            result = subprocess.run(
                [
                    "curl", "-s",
                    "-X", "PUT",
                    "-H", "Content-Type: application/json",
                    "--data-binary", f"@{tmp_path}",
                    "--connect-timeout", str(CONNECT_TIMEOUT_SECONDS),
                    "--max-time", str(UPLOAD_TIMEOUT_SECONDS),
                    "-w", "\n%{http_code}",
                    upload_url,
                ],
                capture_output=True,
                text=True,
                timeout=UPLOAD_TIMEOUT_SECONDS + 5,
            )
        except subprocess.TimeoutExpired:
            return False, None, "", "timeout"
        except Exception as e:
            return False, None, "", str(e)

        return _parse_curl(result)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _parse_curl(result):
    """Split curl's stdout into (ok, status_code_or_None, body, stderr)."""
    if result.returncode != 0:
        return False, None, result.stdout or "", (result.stderr or "").strip()

    stdout = result.stdout or ""
    parts = stdout.rsplit("\n", 1)
    status_str = parts[-1].strip() if parts else ""
    body = parts[0] if len(parts) > 1 else ""

    if not status_str.isdigit():
        return False, None, body, f"unparseable status: {status_str!r}"

    return True, int(status_str), body, None


def _report_step_failure(phase, status, body, err, ctx):
    """Single point for logging + Sentry on any S3-step failure."""
    msg = f"S3 upload step '{phase}' failed: status={status}, err={err}, body={(body or '')[:200]}"
    logger.warning(msg)
    try:
        raise RuntimeError(msg)
    except RuntimeError as exc:
        report_to_sentry(
            exc,
            {**ctx, "phase": phase, "http_code": status},
            level="warning",
        )
