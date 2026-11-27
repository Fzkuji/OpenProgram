"""Codex's plan limits, as Codex's own app-server reports them.

The usage page shows how much of the ChatGPT plan's windows is left. Here
the number comes from the app-server protocol: ``account/rateLimits/read``
when a process starts, and the sparse ``account/rateLimits/updated``
notifications merged into it after. Codex's own rollout files are the
other source (``models/usage/harnesses/codex.py``); the store keeps
whichever reading is newer. OpenProgram never asks ChatGPT for it itself
(docs/reference/design/providers/auth/subscription-policy.html).

Only the ``codex`` limit family is the plan's: a snapshot for another
family (``limitId``) is left out, as the rollout reader leaves it out.
Each ``CODEX_HOME`` is a separate sign-in, so updates merge only into the
snapshot of the process's own home.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Optional

_log = logging.getLogger(__name__)

AGENT = "codex"
#: The limit family that is the ChatGPT plan's own windows.
PLAN_LIMIT_ID = "codex"
READ_TIMEOUT_SECONDS = 15.0

_lock = threading.Lock()
#: The last snapshot read or merged per ``CODEX_HOME``, camelCase as sent.
_last: dict[str, dict[str, Any]] = {}


def is_plan_family(limit_id: Any) -> bool:
    """Whether a snapshot's limit family is the plan's (none named counts)."""
    return not limit_id or str(limit_id).strip().lower().replace("-", "_") == PLAN_LIMIT_ID


def _window(raw: Any) -> Optional[dict]:
    if not isinstance(raw, dict) or not isinstance(raw.get("usedPercent"), (int, float)):
        return None
    return {
        "used_percent": float(raw["usedPercent"]),
        "window_minutes": raw.get("windowDurationMins"),
        "resets_at": raw.get("resetsAt"),
    }


def stored_form(snapshot: dict) -> dict:
    """A protocol ``RateLimitSnapshot`` in the shape ``harnesses.db`` keeps."""
    out: dict[str, Any] = {}
    for slot in ("primary", "secondary"):
        window = _window(snapshot.get(slot))
        if window is not None:
            out[slot] = window
    credits = snapshot.get("credits")
    if isinstance(credits, dict):
        out["credits"] = {"balance": credits.get("balance"), "unlimited": bool(credits.get("unlimited"))}
    if snapshot.get("planType"):
        out["plan_type"] = snapshot["planType"]
    return out


def _merge(home: str, update: dict) -> dict:
    """Merge a sparse update into ``home``'s last snapshot: a missing or null value keeps the old one."""
    with _lock:
        last = _last.setdefault(home, {})
        for key, value in update.items():
            if value is not None:
                last[key] = value
        return dict(last)


def _replace(home: str, snapshot: dict) -> dict:
    with _lock:
        _last[home] = {k: v for k, v in snapshot.items() if v is not None}
        return dict(_last[home])


def _record(snapshot: dict) -> None:
    data = stored_form(snapshot)
    if not data.get("primary") and not data.get("secondary"):
        return
    try:
        from openprogram.models.usage.harnesses import store
        store().record_quota(AGENT, time.time(), data)
    except Exception:  # noqa: BLE001 - a reading only feeds the usage page
        _log.debug("Codex plan limits not stored", exc_info=True)


def _home(server: Any) -> str:
    from .client import codex_home
    return codex_home(getattr(server, "env", None))


def listener(home: str):
    """App-server listener for ``home``: store each plan-family
    ``account/rateLimits/updated``. Never takes the message."""
    def on_message(message: dict) -> bool:
        if message.get("method") == "account/rateLimits/updated":
            params = message.get("params") or {}
            update = params.get("rateLimits")
            if isinstance(update, dict) and is_plan_family(update.get("limitId")):
                _record(_merge(home, update))
        return False
    return on_message


def read(server: Any) -> None:
    """Ask a running app-server for the plan limits and store the answer."""
    try:
        answer = server.request("account/rateLimits/read", {}, timeout=READ_TIMEOUT_SECONDS) or {}
    except Exception:  # noqa: BLE001 - an API-key login has no plan limits
        _log.debug("Codex did not report plan limits", exc_info=True)
        return
    snapshot = answer.get("rateLimits") if isinstance(answer, dict) else None
    if isinstance(snapshot, dict) and is_plan_family(snapshot.get("limitId")):
        _record(_replace(_home(server), snapshot))


def watch(server: Any) -> None:
    """Follow a started app-server's plan limits: one read, then its updates."""
    server.listen(listener(_home(server)))
    threading.Thread(target=read, args=(server,), name="codex-rate-limits", daemon=True).start()


__all__ = ["is_plan_family", "listener", "read", "stored_form", "watch"]
