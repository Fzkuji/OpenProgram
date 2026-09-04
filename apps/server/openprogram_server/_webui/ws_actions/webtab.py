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
_bindings: dict[str, tuple[Any, str, str, str, float, int, int, int, bool]] = {}
_connection_revisions: dict[Any, int] = {}
_page_revisions: dict[tuple[int, str], int] = {}
_desktop_windows: dict[Any, str] = {}
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
        from openprogram.webui.ws_delivery import send_to_connection

        if not send_to_connection(ws, payload, _s._loop):
            raise RuntimeError("originating desktop connection is unavailable")

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
    geometry_revision: int = 0,
    allow_background: bool = False,
    expected_connection_revision: int | None = None,
) -> str:
    binding_id = "surface_" + uuid.uuid4().hex
    with _lock:
        connection_revision = _connection_revisions.get(ws)
        if (
            expected_connection_revision is not None
            and connection_revision != expected_connection_revision
        ):
            raise RuntimeError("Desktop connection changed during Page binding")
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
            max(0, int(geometry_revision)),
            bool(allow_background),
        )
    return binding_id


def release_binding(binding_id: str) -> None:
    with _lock:
        _bindings.pop(binding_id, None)


def release_connection(ws) -> None:
    """Revoke bindings and wake exact-socket requests on disconnect."""
    wake = []
    with _lock:
        _desktop_windows.pop(ws, None)
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


def registered_desktop_windows() -> list[tuple[Any, str, int]]:
    """Return registered renderer sockets and connection revisions."""
    with _lock:
        return [
            (ws, window_id, _connection_revisions[ws])
            for ws, window_id in _desktop_windows.items()
            if ws in _connection_revisions
        ]


def ensure_connection_revision(ws) -> int:
    """Return the connection generation used to reject late async results."""
    with _lock:
        revision = _connection_revisions.get(ws)
        if revision is None:
            revision = next(_next_revision)
            _connection_revisions[ws] = revision
        return revision


def binding_owner_revision(binding_id: str) -> tuple[Any, int] | None:
    """Return one binding owner and its current connection revision atomically."""
    with _lock:
        entry = _bindings.get(binding_id)
        if entry is None:
            return None
        revision = _connection_revisions.get(entry[0])
        if revision is None:
            return None
        return entry[0], revision


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
    return {
        "page_revision": entry[5],
        "access_revision": entry[6],
        "geometry_revision": entry[7],
    }


def binding_connection(binding_id: str):
    """Return the renderer connection owned by a live server binding."""
    with _lock:
        entry = _bindings.get(binding_id)
    return entry[0] if entry is not None else None


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
    expected_geometry_revision: int = 0,
) -> dict:
    """Resolve the exact bound Page without requiring OS foreground state."""
    import os
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        command = {"op": "activate", "binding_id": binding_id}
        if url:
            command["url"] = url
        if expected_page_revision:
            command["expected_page_revision"] = expected_page_revision
        if expected_access_revision:
            command["expected_access_revision"] = expected_access_revision
        if expected_geometry_revision:
            command["expected_geometry_revision"] = expected_geometry_revision
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
            ) or (
                expected_geometry_revision
                and entry[7] != expected_geometry_revision
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
    ws, window_id, tab_id, target_id, expires_at, _, _, _, allow_background = entry
    if time.monotonic() >= expires_at:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding expired",
            "reason_code": "page_context_stale",
        }
    command = {
        "op": "resolve" if allow_background else "activate",
        "window_id": window_id,
        "tab_id": tab_id,
    }
    if url:
        command["url"] = url
    if expected_geometry_revision:
        command["expected_geometry_revision"] = expected_geometry_revision
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
    result_geometry_revision = result.get("geometry_revision")
    if expected_geometry_revision and (
        type(result_geometry_revision) is not int
        or result_geometry_revision != expected_geometry_revision
    ):
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "web tab geometry changed during activation",
            "reason_code": "page_context_stale",
        }
    return result


def request_page_inventory(binding_id: str, timeout: float = 5.0) -> dict:
    """List every Page owned by the same renderer as one accepted binding."""
    with _lock:
        entry = _bindings.get(binding_id)
    if entry is None:
        return {
            "ok": False,
            "error": "surface binding is unavailable",
            "reason_code": "page_context_stale",
        }
    if time.monotonic() >= entry[4]:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding expired",
            "reason_code": "page_context_stale",
        }
    result = request_on_ws(
        entry[0], {"op": "list", "window_id": entry[1]}, timeout,
    )
    if result.get("ok") and result.get("window_id") != entry[1]:
        return {
            "ok": False,
            "error": "Page inventory belongs to another window",
            "reason_code": "page_context_stale",
        }
    return result


def request_close_tab(binding_id: str, timeout: float = 5.0) -> dict:
    """Close the desktop tab owned by one live surface binding."""
    import os
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        return _request({"op": "close", "binding_id": binding_id}, timeout)
    with _lock:
        entry = _bindings.get(binding_id)
    if entry is None:
        return {
            "ok": False,
            "error": "surface binding is unavailable",
            "reason_code": "page_context_stale",
        }
    ws, _window_id, tab_id, _target_id, expires_at, *_rest = entry
    if time.monotonic() >= expires_at:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding expired",
            "reason_code": "page_context_stale",
        }
    result = request_on_ws(ws, {"op": "close", "tab_id": tab_id}, timeout)
    if result.get("ok"):
        release_binding(binding_id)
    return result


def _ws_for_window(window_id: str):
    with _lock:
        for ws, wid in _desktop_windows.items():
            if wid == window_id:
                return ws
    return None


def _bind_opened_tab(result: dict) -> dict:
    if not result.get("ok"):
        return result
    window_id = result.get("window_id")
    tab_id = result.get("tab_id")
    target_id = result.get("target_id")
    if not (
        isinstance(window_id, str) and window_id
        and isinstance(tab_id, str) and tab_id
        and isinstance(target_id, str) and target_id
    ):
        return result
    ws = _ws_for_window(window_id)
    if ws is None:
        return result
    result["binding_id"] = register_binding(
        ws, window_id, tab_id, target_id, allow_background=True,
    )
    return result


def request_open_tab(url: str, timeout: float = 15.0) -> dict:
    """Open/focus ``url`` and return the active desktop tab identity."""
    import os
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        return _request({"op": "open", "url": url}, timeout)
    return _bind_opened_tab(_request({"op": "open", "url": url}, timeout))


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
        pages = []
        for raw in (cmd.get("pages") or [])[:64]:
            if not isinstance(raw, dict):
                continue
            page = {
                key: raw[key]
                for key in (
                    "tab_id", "target_id", "title", "url", "region",
                    "opener_tab_id", "tab_entry_id",
                )
                if isinstance(raw.get(key), str)
            }
            page["focused"] = bool(raw.get("focused"))
            page["visible"] = bool(raw.get("visible"))
            if type(raw.get("geometry_revision")) is int:
                page["geometry_revision"] = max(0, raw["geometry_revision"])
            placement = raw.get("placement")
            if isinstance(placement, dict) and placement.get("mode") in {"single", "split"}:
                page["placement"] = {
                    "mode": placement["mode"],
                    **({"pane_id": placement["pane_id"]}
                       if isinstance(placement.get("pane_id"), str) else {}),
                    **({"order": placement["order"]}
                       if type(placement.get("order")) is int else {}),
                }
            pages.append(page)
        tab_entries = []
        for raw in (cmd.get("tab_entries") or [])[:64]:
            if not isinstance(raw, dict) or raw.get("mode") not in {"single", "split"}:
                continue
            entry_id = raw.get("id")
            if not isinstance(entry_id, str):
                continue
            tab_ids = [
                tab_id for tab_id in (raw.get("tab_ids") or [])[:2]
                if isinstance(tab_id, str)
            ]
            entry = {"id": entry_id, "mode": raw["mode"], "tab_ids": tab_ids}
            split = raw.get("split")
            if raw["mode"] == "split" and isinstance(split, dict):
                panes = []
                for pane in (split.get("panes") or [])[:2]:
                    if not isinstance(pane, dict):
                        continue
                    if not (
                        isinstance(pane.get("pane_id"), str)
                        and type(pane.get("order")) is int
                        and isinstance(pane.get("tab_id"), str)
                    ):
                        continue
                    panes.append({
                        "pane_id": pane["pane_id"],
                        "order": pane["order"],
                        "tab_id": pane["tab_id"],
                    })
                ratio = split.get("ratio")
                entry["split"] = {
                    "axis": "horizontal",
                    **({"ratio": float(ratio)}
                       if isinstance(ratio, (int, float)) and not isinstance(ratio, bool)
                       else {}),
                    "panes": panes,
                }
            tab_entries.append(entry)
        window_id = cmd.get("window_id")
        if ws is not None and isinstance(window_id, str) and window_id:
            _desktop_windows[ws] = window_id
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
            **({"geometry_revision": cmd["geometry_revision"]}
               if isinstance(cmd.get("geometry_revision"), int) else {}),
            **({"reason_code": cmd["reason_code"]}
               if isinstance(cmd.get("reason_code"), str) else {}),
            **({"inventory_revision": max(0, cmd["inventory_revision"])}
               if type(cmd.get("inventory_revision")) is int else {}),
            **({"active_tab_entry_id": cmd["active_tab_entry_id"]}
               if isinstance(cmd.get("active_tab_entry_id"), str) else {}),
            **({"focused_tab_id": cmd["focused_tab_id"]}
               if isinstance(cmd.get("focused_tab_id"), str) else {}),
            **({"tab_entries": tab_entries}
               if isinstance(cmd.get("tab_entries"), list) else {}),
            **({"pages": pages} if isinstance(cmd.get("pages"), list) else {}),
        }
    ev.set()


async def handle_webtab_register(ws, cmd: dict):
    """Associate one authenticated renderer socket with its Desktop window."""
    window_id = cmd.get("window_id")
    if not isinstance(window_id, str) or not window_id or len(window_id) > 160:
        return
    with _lock:
        if ws not in _connection_revisions:
            _connection_revisions[ws] = next(_next_revision)
        _desktop_windows[ws] = window_id


ACTIONS = {
    "webtab_register": handle_webtab_register,
    "webtab_result": handle_webtab_result,
}
