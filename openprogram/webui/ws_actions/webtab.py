"""Desktop web-tab control plane: open a VISIBLE tab in the desktop shell.

数据面（CDP 9223）只能附着已有页面；可见地开新页要绕道 UI —— 后端广播
``webtab.command``（op=open），桌面壳前端（desktop-bridge.ts 的
installDesktopMenuHandlers）收到后 openWebTab(url) 并经同一条 WS 回
``webtab_result``（带 req_id）。``request_open_tab`` 阻塞等待该回执，
模式与 agent/questions.py 的 ask_blocking 相同（Event + pending 表）。

非桌面客户端收到广播后直接忽略（它们不装该 handler），所以桌面壳没开时
调用方会拿到 timeout / no-clients 的失败结果，自行回落 sidecar。
"""
from __future__ import annotations

import itertools
import json
import threading
import time
import uuid
from typing import Any

# req_id -> (event, result-holder)。holder 为空 dict，首个回执写入
# "result" 键（claim-once），后续同 req_id 的回执直接忽略。
_pending: dict[str, tuple[threading.Event, dict, Any | None]] = {}
_lock = threading.Lock()
_bindings: dict[str, tuple[Any, str, str, str, float, int, int]] = {}
_connection_revisions: dict[Any, int] = {}
_page_revisions: dict[tuple[int, str], int] = {}
_next_revision = itertools.count(1)


def _payload(command: dict, req_id: str) -> str:
    return json.dumps({
        "type": "webtab.command",
        "data": {**command, "req_id": req_id},
    })


def _wait_for_reply(
    command: dict,
    timeout: float,
    *,
    expected_ws=None,
    send,
) -> dict:
    req_id = uuid.uuid4().hex
    ev = threading.Event()
    holder: dict = {}
    with _lock:
        _pending[req_id] = (ev, holder, expected_ws)
    try:
        send(_payload(command, req_id))
        if not ev.wait(timeout):
            return {"ok": False, "error": f"timeout: no desktop shell replied within {timeout:g}s"}
        return holder.get("result") or {"ok": False, "error": "empty reply"}
    finally:
        with _lock:
            _pending.pop(req_id, None)


def _request(command: dict, timeout: float) -> dict:
    from openprogram.webui import server as _s
    if not _s._ws_connections:
        return {"ok": False, "error": "no WS clients connected (desktop shell not open?)"}
    return _wait_for_reply(command, timeout, send=_s._broadcast)


def request_on_ws(ws, command: dict, timeout: float = 5.0) -> dict:
    """Send one web-tab command only to the socket that submitted the turn."""
    from openprogram.webui import server as _s
    if ws not in _s._ws_connections or _s._loop is None:
        return {"ok": False, "error": "originating desktop connection is unavailable"}

    def send(payload: str) -> None:
        import asyncio
        future = asyncio.run_coroutine_threadsafe(ws.send_text(payload), _s._loop)
        future.result(timeout=min(max(timeout, 0.1), 2.0))

    try:
        return _wait_for_reply(
            command, timeout, expected_ws=ws, send=send,
        )
    except Exception as exc:
        return {"ok": False, "error": f"desktop command failed: {type(exc).__name__}: {exc}"}


def register_binding(
    ws,
    window_id: str,
    tab_id: str,
    target_id: str,
    ttl: float = 1800.0,
) -> str:
    binding_id = "surface_" + uuid.uuid4().hex
    with _lock:
        connection_revision = _connection_revisions.get(ws)
        if connection_revision is None:
            connection_revision = next(_next_revision)
            _connection_revisions[ws] = connection_revision
        page_identity = (connection_revision, target_id)
        page_revision = _page_revisions.get(page_identity)
        if page_revision is None:
            page_revision = next(_next_revision)
            _page_revisions[page_identity] = page_revision
        access_revision = next(_next_revision)
        _bindings[binding_id] = (
            ws,
            window_id,
            tab_id,
            target_id,
            time.monotonic() + ttl,
            page_revision,
            access_revision,
        )
    return binding_id


def release_binding(binding_id: str) -> None:
    with _lock:
        _bindings.pop(binding_id, None)


def release_connection(ws) -> None:
    """Revoke bindings and wake exact-socket requests on disconnect."""
    wake = []
    with _lock:
        connection_revision = _connection_revisions.pop(ws, None)
        for binding_id, entry in list(_bindings.items()):
            if entry[0] is ws:
                _bindings.pop(binding_id, None)
        if connection_revision is not None:
            for identity in list(_page_revisions):
                if identity[0] == connection_revision:
                    _page_revisions.pop(identity, None)
        for ev, holder, expected_ws in _pending.values():
            if expected_ws is ws and "result" not in holder:
                holder["result"] = {
                    "ok": False,
                    "error": "originating desktop connection disconnected",
                }
                wake.append(ev)
    for ev in wake:
        ev.set()


def binding_page_key(binding_id: str) -> str:
    """Return a server-owned identity shared by captures of one CDP Page."""
    with _lock:
        entry = _bindings.get(binding_id)
    if entry is None:
        return ""
    return f"page:{entry[5]}"


def binding_revisions(binding_id: str) -> dict[str, int]:
    """Return server-owned Page/access revisions for one live capability."""
    with _lock:
        entry = _bindings.get(binding_id)
    if entry is None:
        return {}
    return {"page_revision": entry[5], "access_revision": entry[6]}


def _invalidate_page(ws, target_id: str) -> None:
    with _lock:
        connection_revision = _connection_revisions.get(ws)
        if connection_revision is not None:
            _page_revisions.pop((connection_revision, target_id), None)
        for binding_id, entry in list(_bindings.items()):
            if entry[0] is ws and entry[3] == target_id:
                _bindings.pop(binding_id, None)


def request_bound_tab(
    binding_id: str,
    *,
    url: str = "",
    timeout: float = 5.0,
    expected_page_revision: int = 0,
    expected_access_revision: int = 0,
) -> dict:
    """Activate the exact visible tab captured for this turn."""
    import os
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        command = {"op": "activate", "binding_id": binding_id}
        if url:
            command["url"] = url
        if expected_page_revision:
            command["expected_page_revision"] = expected_page_revision
        if expected_access_revision:
            command["expected_access_revision"] = expected_access_revision
        return _request(command, timeout)
    with _lock:
        entry = _bindings.get(binding_id)
        revision_mismatch = entry is not None and (
            (
                expected_page_revision
                and entry[5] != expected_page_revision
            ) or (
                expected_access_revision
                and entry[6] != expected_access_revision
            )
        )
        if revision_mismatch:
            _bindings.pop(binding_id, None)
    if entry is None:
        return {
            "ok": False,
            "error": "surface binding is unavailable",
            "reason_code": "page_context_stale",
        }
    if revision_mismatch:
        return {
            "ok": False,
            "error": "surface binding revision changed",
            "reason_code": "page_context_stale",
        }
    ws, window_id, tab_id, target_id, expires_at, _, _ = entry
    if time.monotonic() >= expires_at:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding expired",
            "reason_code": "page_context_stale",
        }
    command = {"op": "activate", "window_id": window_id, "tab_id": tab_id}
    if url:
        command["url"] = url
    result = request_on_ws(ws, command, timeout)
    if not result.get("ok"):
        release_binding(binding_id)
        return result
    if (
        result.get("window_id") != window_id
        or result.get("tab_id") != tab_id
        or result.get("target_id") != target_id
    ):
        _invalidate_page(ws, target_id)
        return {
            "ok": False,
            "error": "bound web tab changed",
            "reason_code": "page_context_stale",
        }
    return result


def request_open_tab(url: str, timeout: float = 15.0) -> dict:
    """Open/focus ``url`` and return the active desktop tab identity."""
    return _request({"op": "open", "url": url}, timeout)


def request_active_tab(timeout: float = 5.0) -> dict:
    """Return the currently visible desktop web tab, if one is active."""
    return _request({"op": "active"}, timeout)


async def handle_webtab_result(ws, cmd: dict):
    req_id = cmd.get("req_id") or ""
    with _lock:
        entry = _pending.get(req_id)
        if entry is None or "result" in entry[1]:
            return  # unknown req_id or already claimed — ignore duplicates
        ev, holder, expected_ws = entry
        if expected_ws is not None and ws is not expected_ws:
            return
        holder["result"] = {
            "ok": bool(cmd.get("ok")),
            "error": cmd.get("error"),
            **({"window_id": cmd["window_id"]}
               if isinstance(cmd.get("window_id"), str) else {}),
            **({"url": cmd["url"]} if isinstance(cmd.get("url"), str) else {}),
            **({"tab_id": cmd["tab_id"]} if isinstance(cmd.get("tab_id"), str) else {}),
            **({"target_id": cmd["target_id"]} if isinstance(cmd.get("target_id"), str) else {}),
            **({"title": cmd["title"]} if isinstance(cmd.get("title"), str) else {}),
            **({"preview": cmd["preview"]} if isinstance(cmd.get("preview"), dict) else {}),
        }
    ev.set()


ACTIONS = {
    "webtab_result": handle_webtab_result,
}
