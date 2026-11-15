"""The Server's implementation of the core's interface link.

:mod:`openprogram.runtime.interface_link` is how core code notifies the open
clients. Every function looks the Server's state up when it is called, so the
link follows the live module (and tests that replace one of its helpers).
"""
from __future__ import annotations

from collections.abc import Mapping

import json


def _server():
    from openprogram.interfaces.server import server

    return server


def broadcast(frame: dict) -> None:
    _server()._broadcast(json.dumps(frame, ensure_ascii=False, default=str))


def broadcast_chat_response(session_id: str, msg_id: str, response: dict) -> None:
    _server()._broadcast_chat_response(session_id, msg_id, response)


def refresh_context_stats(session_id: str, msg_id: str = "") -> None:
    _server().refresh_context_stats(session_id, msg_id)


def branches_changed(session_id: str) -> None:
    from openprogram.state.store.session.graph.branches import build_branches_payload

    broadcast({"type": "branches_list", "data": build_branches_payload(session_id)})


def live_session(session_id: str) -> dict | None:
    conv = getattr(_server(), "_sessions", {}).get(session_id)
    return conv if isinstance(conv, dict) else None


def session_busy(session_id: str) -> bool:
    """A WebSocket chat turn holds the session: the run registry or the in-memory status."""
    server = _server()
    lock = getattr(server, "_running_tasks_lock", None)
    tasks = getattr(server, "_running_tasks", None)
    if lock is not None and isinstance(tasks, Mapping):
        with lock:
            if session_id in tasks:
                return True
    sessions = getattr(server, "_sessions", None)
    if isinstance(sessions, dict):
        row = sessions.get(session_id) or {}
        if row.get("status") in {"running", "busy", "executing"}:
            return True
    return False


def session_on_screen(session_id: str) -> bool:
    """A connected client has the session open in a window that is not hidden.

    ``mark_session_read`` sets the socket's focus and ``set_window_hidden``
    its hidden flag. Focus stays while hidden: it is also the disconnect
    signal for a waiting question, which a background window keeps holding.
    """
    server = _server()
    with server._ws_lock:
        connections = list(server._ws_connections)
    return any(
        getattr(ws, "_focused_session_id", None) == session_id
        and not getattr(ws, "_window_hidden", False)
        for ws in connections
    )


def set_active_head(session_id: str, head_id: str) -> bool:
    return _server()._set_active_head(session_id, head_id)


def document_file(page: str) -> str:
    """The built Markdown copy in the served export, else the HTML source."""
    from openprogram.interfaces.server.frontend import out_dir, web_dir

    built = out_dir() / "docs" / f"{page}.html.md"
    if built.is_file():
        return str(built)
    source = web_dir().parents[1] / "docs" / f"{page}.html"
    return str(source) if source.is_file() else ""
