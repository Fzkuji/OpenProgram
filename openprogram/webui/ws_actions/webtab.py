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

import json
import threading
import uuid

# req_id -> (event, result-holder)。holder 为空 dict，首个回执写入
# "result" 键（claim-once），后续同 req_id 的回执直接忽略。
_pending: dict[str, tuple[threading.Event, dict]] = {}
_lock = threading.Lock()


def _request(command: dict, timeout: float) -> dict:
    from openprogram.webui import server as _s
    if not _s._ws_connections:
        return {"ok": False, "error": "no WS clients connected (desktop shell not open?)"}
    req_id = uuid.uuid4().hex
    ev = threading.Event()
    holder: dict = {}
    with _lock:
        _pending[req_id] = (ev, holder)
    try:
        _s._broadcast(json.dumps({
            "type": "webtab.command",
            "data": {**command, "req_id": req_id},
        }))
        if not ev.wait(timeout):
            return {"ok": False, "error": f"timeout: no desktop shell replied within {timeout:g}s"}
        return holder.get("result") or {"ok": False, "error": "empty reply"}
    finally:
        with _lock:
            _pending.pop(req_id, None)


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
        ev, holder = entry
        holder["result"] = {
            "ok": bool(cmd.get("ok")),
            "error": cmd.get("error"),
            **({"url": cmd["url"]} if isinstance(cmd.get("url"), str) else {}),
            **({"tab_id": cmd["tab_id"]} if isinstance(cmd.get("tab_id"), str) else {}),
            **({"target_id": cmd["target_id"]} if isinstance(cmd.get("target_id"), str) else {}),
        }
    ev.set()


ACTIONS = {
    "webtab_result": handle_webtab_result,
}
