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

import asyncio
import base64
import binascii
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
_instance_id = uuid.uuid4().hex[:12]
_restore_jobs: dict[int, tuple[int, asyncio.Task]] = {}
_RESTORABLE = frozenset({"restoring", "unavailable", "restore_failed"})
RESPONSE_TIMEOUT_REASON_CODE = "desktop_response_timeout"
_PNG_DATA_URL_PREFIX = "data:image/png;base64,"
# Leave JSON framing headroom below Uvicorn's 16 MiB WebSocket message limit.
_MAX_SCREENSHOT_DATA_URL_CHARS = 12 * 1024 * 1024


def validated_open_ownership(value: Any) -> dict[str, bool]:
    """Return a trusted created/reused pair, or no ownership evidence."""
    if not isinstance(value, dict):
        return {}
    created = value.get("created")
    reused = value.get("reused")
    if type(created) is not bool or type(reused) is not bool or created == reused:
        return {}
    return {"created": created, "reused": reused}


def _validated_png_data_url(value: Any) -> str | None:
    """Return one bounded, base64-valid PNG data URL from the renderer."""
    if (
        not isinstance(value, str)
        or len(value) <= len(_PNG_DATA_URL_PREFIX)
        or len(value) > _MAX_SCREENSHOT_DATA_URL_CHARS
        or not value.startswith(_PNG_DATA_URL_PREFIX)
    ):
        return None
    try:
        raw = base64.b64decode(
            value[len(_PNG_DATA_URL_PREFIX):], validate=True,
        )
    except (binascii.Error, ValueError):
        return None
    return value if raw.startswith(b"\x89PNG\r\n\x1a\n") else None


def _payload(command: dict, req_id: str) -> str:
    from openprogram.agent.run_control import get_current_session_id

    session_id = get_current_session_id()
    if session_id and "session_id" not in command:
        command = {**command, "session_id": session_id}
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
            return {
                "ok": False,
                "reason_code": RESPONSE_TIMEOUT_REASON_CODE,
                "error": f"timeout: no desktop shell replied within {timeout:g}s",
            }
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
        connection_generation = connection_revision
    page_key = page_key_for_revision(page_revision)
    try:
        from openprogram.browser_resources import retain_from_binding, BrowserResourceStore
        retain_from_binding(
            binding_id, window_id, tab_id, target_id, connection_generation,
        )
        BrowserResourceStore().adopt_successor(
            successor_id=page_key,
            window_id=window_id,
            tab_id=tab_id,
            connection_generation=connection_generation,
        )
    except Exception:
        pass
    return binding_id


def release_binding(binding_id: str) -> None:
    with _lock:
        _bindings.pop(binding_id, None)


def release_connection(ws) -> None:
    """Revoke bindings and wake exact-socket requests on disconnect."""
    wake = []
    page_keys = []
    with _lock:
        _desktop_windows.pop(ws, None)
        connection_revision = _connection_revisions.pop(ws, None)
        for binding_id, entry in list(_bindings.items()):
            if entry[0] is ws:
                page_keys.append(page_key_for_revision(entry[5]))
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
    job = _restore_jobs.pop(id(ws), None)
    if job is not None and not job[1].done():
        job[1].cancel()
    if page_keys:
        try:
            from openprogram.browser_resources import BrowserResourceStore
            store = BrowserResourceStore()
            for page_key in dict.fromkeys(page_keys):
                store.mark_unavailable(page_key)
        except Exception:
            pass


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


def page_key_for_revision(page_revision: int) -> str:
    """Return a process-incarnated Page identity that does not collide after restart."""
    return f"page:{_instance_id}:{int(page_revision)}"


def page_revision_for_key(page_key: str) -> int | None:
    """Return the current-process page revision encoded in a page_key, if any."""
    prefix = f"page:{_instance_id}:"
    if not isinstance(page_key, str) or not page_key.startswith(prefix):
        return None
    rest = page_key[len(prefix):]
    if not rest.isdigit():
        return None
    return int(rest)


def binding_page_key(binding_id: str) -> str:
    """Return a server-owned identity shared by captures of one CDP Page."""
    with _lock:
        entry = _bindings.get(binding_id)
    if entry is None:
        return ""
    return page_key_for_revision(entry[5])


def binding_page_descriptor(binding_id: str) -> dict[str, Any]:
    """Return exact live binding identity. Empty if the handle is gone."""
    with _lock:
        entry = _bindings.get(binding_id)
        connection_revision = (
            _connection_revisions.get(entry[0]) if entry is not None else None
        )
    if entry is None:
        return {}
    return {
        "window_id": entry[1],
        "tab_id": entry[2],
        "target_id": entry[3],
        "page_key": page_key_for_revision(entry[5]),
        "connection_generation": int(connection_revision or 0),
    }


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
    page_keys = []
    with _lock:
        connection_revision = _connection_revisions.get(ws)
        if connection_revision is not None:
            _page_revisions.pop((connection_revision, target_id), None)
        for binding_id, entry in list(_bindings.items()):
            if entry[0] is ws and entry[3] == target_id:
                page_keys.append(page_key_for_revision(entry[5]))
                _bindings.pop(binding_id, None)
    if page_keys:
        try:
            from openprogram.browser_resources import mark_binding_unavailable
            for page_key in dict.fromkeys(page_keys):
                mark_binding_unavailable(page_key=page_key)
        except Exception:
            pass


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


def request_bound_screenshot(
    binding_id: str,
    *,
    timeout: float = 5.0,
    expected_page_revision: int = 0,
    expected_access_revision: int = 0,
    expected_geometry_revision: int = 0,
) -> dict:
    """Capture one exact Page through Electron without revealing the view."""
    import os
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        command = {"op": "screenshot", "binding_id": binding_id}
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
    if entry is None:
        return {
            "ok": False,
            "error": "surface binding is unavailable",
            "reason_code": "page_context_stale",
        }
    if revision_mismatch:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding revision changed",
            "reason_code": "page_context_stale",
        }
    ws, window_id, tab_id, target_id, expires_at, *_rest = entry
    if time.monotonic() >= expires_at:
        release_binding(binding_id)
        return {
            "ok": False,
            "error": "surface binding expired",
            "reason_code": "page_context_stale",
        }
    command = {
        "op": "screenshot",
        "window_id": window_id,
        "tab_id": tab_id,
    }
    if expected_geometry_revision:
        command["expected_geometry_revision"] = expected_geometry_revision
    result = request_on_ws(ws, command, timeout)
    if not result.get("ok"):
        return result
    if result.get("window_id") != window_id or result.get("tab_id") != tab_id:
        _invalidate_page(ws, target_id)
        return {
            "ok": False,
            "error": "captured web tab changed",
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
            "error": "web tab geometry changed during capture",
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
        page_key = binding_page_key(binding_id)
        release_binding(binding_id)
        if page_key:
            try:
                from openprogram.browser_resources import BrowserResourceStore
                BrowserResourceStore().mark_closed(page_key)
            except Exception:
                pass
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


def request_open_tab(
    url: str, timeout: float = 15.0, *, session_id: str | None = None,
) -> dict:
    """Open/focus ``url`` and return the active desktop tab identity."""
    import os
    from openprogram.agent.run_control import get_current_session_id

    owner = session_id or get_current_session_id()
    command = {"op": "open", "url": url}
    if owner:
        command["session_id"] = owner
    if os.environ.get("OPENPROGRAM_IN_AGENTIC_SUBPROCESS") == "1":
        return _request(command, timeout)
    return _bind_opened_tab(_request(command, timeout))


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
        image_data_url = _validated_png_data_url(cmd.get("image_data_url"))
        invalid_image = "image_data_url" in cmd and image_data_url is None
        holder["result"] = {
            "ok": bool(cmd.get("ok")) and not invalid_image,
            "error": (
                cmd.get("error")
                if not invalid_image
                else "invalid desktop web tab screenshot"
            ),
            **({"window_id": cmd["window_id"]}
               if isinstance(cmd.get("window_id"), str) else {}),
            **({"url": cmd["url"]} if isinstance(cmd.get("url"), str) else {}),
            **({"tab_id": cmd["tab_id"]} if isinstance(cmd.get("tab_id"), str) else {}),
            **({"target_id": cmd["target_id"]} if isinstance(cmd.get("target_id"), str) else {}),
            **({"title": cmd["title"]} if isinstance(cmd.get("title"), str) else {}),
            **({"preview": cmd["preview"]} if isinstance(cmd.get("preview"), dict) else {}),
            **({"image_data_url": image_data_url} if image_data_url else {}),
            **({"geometry_revision": cmd["geometry_revision"]}
               if isinstance(cmd.get("geometry_revision"), int) else {}),
            **({"reason_code": cmd["reason_code"]}
               if isinstance(cmd.get("reason_code"), str) else {}),
            **validated_open_ownership(cmd),
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


def _connection_revision_of(ws) -> int | None:
    with _lock:
        return _connection_revisions.get(ws)


def restore_window_pages(ws, window_id: str, expected_revision: int | None = None) -> None:
    """Rebind native inventory Pages onto fresh keys; transfer stored tab ownership."""
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor
    if trusted_runtime_actor(getattr(ws, "scope", None), surface="ws") is None:
        return
    generation = _connection_revision_of(ws)
    if generation is None:
        return
    if expected_revision is not None and generation != expected_revision:
        return
    from openprogram.browser_resources import (
        BrowserResourceStore, publish_bound_page,
    )
    store = BrowserResourceStore()
    store.mark_window_restorable(window_id, "restoring")
    result = request_on_ws(ws, {"op": "list", "window_id": window_id}, timeout=12.0)
    if expected_revision is not None and _connection_revision_of(ws) != expected_revision:
        return
    pages = result.get("pages") if result.get("ok") else None
    if not result.get("ok") or not isinstance(pages, list):
        store.mark_window_restorable(window_id, "restore_failed")
        return
    seen_tabs: set[str] = set()
    for page in pages:
        if not isinstance(page, dict):
            continue
        tab_id = page.get("tab_id")
        target_id = page.get("target_id")
        if not isinstance(tab_id, str) or not tab_id:
            continue
        seen_tabs.add(tab_id)
        if not isinstance(target_id, str) or not target_id:
            for resource_id in store.page_keys_for_tab(window_id, tab_id):
                row = store.get_resource(resource_id) or {}
                if (row.get("lifecycle") or "") in _RESTORABLE:
                    store.mark_restore_failed(resource_id)
            continue
        try:
            binding_id = register_binding(
                ws, window_id, tab_id, target_id, allow_background=True,
                expected_connection_revision=generation,
            )
            title = page.get("title") if isinstance(page.get("title"), str) else ""
            url = page.get("url") if isinstance(page.get("url"), str) else ""
            publish_bound_page(
                binding_page_key(binding_id),
                window_id=window_id,
                tab_id=tab_id,
                connection_generation=generation,
                title=title,
                target=url,
            )
        except Exception:
            for resource_id in store.page_keys_for_tab(window_id, tab_id):
                row = store.get_resource(resource_id) or {}
                if (row.get("lifecycle") or "") in _RESTORABLE:
                    store.mark_restore_failed(resource_id)
    if not seen_tabs:
        store.mark_window_restorable(window_id, "restore_failed")
        return
    with store.connect() as db:
        leftovers = db.execute(
            """SELECT resource_id, tab_id FROM browser_resources
            WHERE window_id=? AND lifecycle='restoring'""",
            (window_id,),
        ).fetchall()
    for row in leftovers:
        if (row["tab_id"] or "") not in seen_tabs:
            store.mark_restore_failed(row["resource_id"])


def schedule_window_restore(ws, window_id: str, revision: int) -> asyncio.Task | None:
    """Start at most one restore job per socket revision; return immediately."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    key = id(ws)
    existing = _restore_jobs.get(key)
    if existing is not None and existing[0] == revision and not existing[1].done():
        return existing[1]
    if existing is not None and not existing[1].done():
        existing[1].cancel()

    async def _run() -> None:
        try:
            await asyncio.to_thread(restore_window_pages, ws, window_id, revision)
        except asyncio.CancelledError:
            raise
        finally:
            current = _restore_jobs.get(key)
            task = asyncio.current_task()
            if current is not None and current[1] is task:
                _restore_jobs.pop(key, None)

    task = loop.create_task(_run())
    _restore_jobs[key] = (revision, task)
    return task


async def handle_webtab_register(ws, cmd: dict):
    """Associate one authenticated renderer socket with its Desktop window."""
    window_id = cmd.get("window_id")
    if not isinstance(window_id, str) or not window_id or len(window_id) > 160:
        return
    with _lock:
        if ws not in _connection_revisions:
            _connection_revisions[ws] = next(_next_revision)
        _desktop_windows[ws] = window_id
        revision = _connection_revisions[ws]
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor
    if trusted_runtime_actor(getattr(ws, "scope", None), surface="ws") is None:
        return
    schedule_window_restore(ws, window_id, revision)


async def handle_webtab_closed(ws, cmd: dict):
    """Mark the exact native Page closed when the originating renderer destroys it."""
    window_id = cmd.get("window_id")
    tab_id = cmd.get("tab_id")
    if not isinstance(window_id, str) or not window_id or not isinstance(tab_id, str) or not tab_id:
        return
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor
    if trusted_runtime_actor(getattr(ws, "scope", None), surface="ws") is None:
        return
    from openprogram.browser_resources import (
        BrowserResourceStore, fence_page_writes,
        page_keys_for_socket_tab, project_conversation_resources, emit_browser_resource,
    )
    keys = page_keys_for_socket_tab(ws, window_id, tab_id)
    for page_key in keys:
        fence_page_writes(page_key)
        try:
            BrowserResourceStore().mark_closed(page_key)
        except Exception:
            continue
        try:
            associations = BrowserResourceStore().associations_for_page(page_key)
            conversation = next(
                (item.get("conversation_session_id") or item.get("session_id")
                 for item in associations
                 if item.get("conversation_session_id") or item.get("session_id")),
                None,
            )
            if conversation:
                rows, _, _ = project_conversation_resources(conversation)
                row = next((item for item in rows if item["resource_id"] == page_key), None)
                if row is not None:
                    emit_browser_resource(row, page_key=page_key)
        except Exception:
            pass
    closed_revisions = {
        revision
        for revision in (page_revision_for_key(page_key) for page_key in keys)
        if revision is not None
    }
    with _lock:
        connection_revision = _connection_revisions.get(ws)
        for binding_id, entry in list(_bindings.items()):
            if entry[0] is ws and entry[1] == window_id and entry[2] == tab_id:
                if connection_revision is not None:
                    _page_revisions.pop((connection_revision, entry[3]), None)
                _bindings.pop(binding_id, None)
        if connection_revision is not None and closed_revisions:
            for identity, revision in list(_page_revisions.items()):
                if identity[0] == connection_revision and revision in closed_revisions:
                    _page_revisions.pop(identity, None)


_HUMAN_INPUT_KINDS = frozenset({"pointer", "key", "scroll", "navigate"})


async def handle_webtab_human_input(ws, cmd: dict):
    """Fence later Agent writes for the exact Page of this renderer tab."""
    window_id = cmd.get("window_id")
    tab_id = cmd.get("tab_id")
    sequence = cmd.get("sequence")
    if sequence is None:
        sequence = cmd.get("input_seq")
    kind = cmd.get("kind")
    if (
        not isinstance(window_id, str) or not window_id
        or not isinstance(tab_id, str) or not tab_id
        or type(sequence) is not int or sequence < 1
        or kind not in _HUMAN_INPUT_KINDS
    ):
        return
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor
    if trusted_runtime_actor(getattr(ws, "scope", None), surface="ws") is None:
        return
    from openprogram.browser_resources import handle_human_page_input
    await handle_human_page_input(
        ws=ws, window_id=window_id, tab_id=tab_id, input_seq=sequence, kind=kind,
    )


ACTIONS = {
    "webtab_register": handle_webtab_register,
    "webtab_result": handle_webtab_result,
    "webtab_human_input": handle_webtab_human_input,
    "webtab_closed": handle_webtab_closed,
}
