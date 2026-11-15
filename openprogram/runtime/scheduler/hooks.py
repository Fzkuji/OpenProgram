"""Webhook tasks: an outside service starts a Scheduler task with a POST.

``POST /hooks/<task id>`` is the one route the owner login does not guard
(``webui/owner_auth.py``); the task's own key does. A caller proves it with
the ``X-OpenProgram-Key`` header, or, for a GitHub webhook given the key as
its secret, with ``X-Hub-Signature-256``. The request body reaches the run
as an outside event (``worker.event_note``): data, never instructions, and
a woken conversation runs it under approvals (``owner_turn``).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from typing import Any, Mapping

PATH_PREFIX = "/hooks/"
KEY_HEADER = "x-openprogram-key"
GITHUB_SIGNATURE_HEADER = "x-hub-signature-256"
BODY_MAX = 64 * 1024
EVENT_TEXT_MAX = 8000
# One task fires at most once in this many seconds; extra calls get 429.
MIN_INTERVAL_S = 10.0

_last_fired: dict[str, float] = {}
_rate_lock = threading.Lock()


def new_key() -> str:
    return secrets.token_urlsafe(24)


def url(task_id: str) -> str:
    """The address an outside service posts to, from the running Web access."""
    base = "http://127.0.0.1:18100"
    try:
        from openprogram.host.security.backend_endpoint import read_active_web_access
        access = read_active_web_access()
        origins = sorted(access.effective_origins)
        base = origins[0] if origins else f"http://127.0.0.1:{access.port}"
    except Exception:
        # No Web access record yet (the server is still starting): the
        # default port is the address it will listen on.
        logging.getLogger(__name__).debug("webhook URL falls back to %s", base, exc_info=True)
    return f"{base.rstrip('/')}{PATH_PREFIX}{task_id}"


def authorized(key: str, headers: Mapping[str, str], body: bytes) -> bool:
    """The request carries the task's key, or a GitHub signature made with it."""
    if not key:
        return False
    given = headers.get(KEY_HEADER) or ""
    if given and hmac.compare_digest(given.encode(), key.encode()):
        return True
    signature = headers.get(GITHUB_SIGNATURE_HEADER) or ""
    if signature.startswith("sha256="):
        expected = "sha256=" + hmac.new(key.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature.encode(), expected.encode())
    return False


def event_text(headers: Mapping[str, str], body: bytes) -> str:
    """The request as the run reads it: the GitHub event name, then the body."""
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        text = body.decode("utf-8", errors="replace")
    try:
        text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
    except ValueError:
        pass
    github_event = headers.get("x-github-event")
    if github_event:
        text = f"GitHub event: {github_event}\n{text}"
    if len(text) > EVENT_TEXT_MAX:
        text = text[:EVENT_TEXT_MAX] + "\n… (truncated)"
    return text


def _rate_limited(task_id: str, now: float) -> bool:
    with _rate_lock:
        last = _last_fired.get(task_id)
        if last is not None and now - last < MIN_INTERVAL_S:
            return True
        _last_fired[task_id] = now
        return False


def receive(task_id: str, headers: Mapping[str, str], body: bytes) -> tuple[int, dict[str, Any]]:
    """Check one call and start its task. Returns the HTTP status and body.

    An unknown task and a wrong key answer the same 404, so the route does
    not tell a caller which task ids exist.
    """
    from . import service
    from openprogram.programs.tools.jobs.cron import worker

    if len(body) > BODY_MAX:
        return 413, {"error": "body_too_large"}
    entry = service.get_task(task_id)
    if not entry or entry.get("type") != "webhook" \
            or not authorized(str(entry.get("hook_key") or ""), headers, body):
        return 404, {"error": "not_found"}
    if entry.get("enabled", True) is False:
        return 409, {"error": "task_disabled"}
    if _rate_limited(task_id, time.monotonic()):
        return 429, {"error": "too_many_calls"}
    proc = worker.fire_event(entry, {"source": "webhook", "text": event_text(headers, body)})
    if proc is worker.PREVIOUS_RUN_ACTIVE:
        return 409, {"error": "previous_run_active"}
    if proc is None:
        return 500, {"error": "task_refused"}
    return 202, {"ok": True, "task_id": task_id}


__all__ = ["PATH_PREFIX", "authorized", "event_text", "new_key", "receive", "url"]
