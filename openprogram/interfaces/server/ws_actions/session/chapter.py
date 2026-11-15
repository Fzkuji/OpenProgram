"""Pin a turn as a chapter of its conversation.

Wire format::

    in:  {"action": "set_message_chapter", "session_id": "...",
          "message_id": "<user message id>", "pinned": true | false,
          "request_id"?: "..."}
    out (broadcast to every connection):
         {"type": "message_chapter_updated",
          "data": {"session_id", "message_id",
                   "chapter": {"pinned_at": <epoch seconds>} | null,
                   "request_id", "action": "set_message_chapter"}}

A pin belongs to the turn and is stored on the turn's USER message node as
``metadata.chapter``, so it travels with that node through every branch
that contains it and comes back on ``load_session`` as the row's
``chapter`` field. Unpinning writes ``chapter: None``; a missing or None
``chapter`` means unpinned. Bad input, an unknown message and a message
that is not a user message are refused with an ``operation_error``.
"""
from __future__ import annotations

import json
import time

from .load import _session_io


def _user_node(db, session_id: str, message_id: str):
    """The session's node ``message_id``, or None; raises when it is not a user message."""
    from openprogram.interfaces.server.ws_errors import OperationError
    for node in db.get_nodes(session_id):
        if node.id != message_id:
            continue
        if not node.is_user():
            raise OperationError("not_a_user_message", scope="session")
        return node
    return None


def _patch_open_views(_s, session_id: str, message_id: str, chapter) -> None:
    """Keep the in-memory conversation mirror in step with the store.
    Transcript reads project from the store, so they carry the pin already."""
    with _s._sessions_lock:
        conv = _s._sessions.get(session_id)
        for row in (conv or {}).get("messages") or []:
            if isinstance(row, dict) and row.get("id") == message_id:
                row["chapter"] = chapter


async def handle_set_message_chapter(ws, cmd: dict) -> None:
    """Pin or unpin the turn a user message starts."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_errors import OperationError
    from openprogram.runtime.context.session_db import default_db

    session_id = cmd.get("session_id")
    message_id = cmd.get("message_id")
    pinned = cmd.get("pinned")
    if (not isinstance(session_id, str) or not session_id
            or not isinstance(message_id, str) or not message_id
            or not isinstance(pinned, bool)):
        raise OperationError("invalid_request", scope="session")
    db = default_db()
    node = await _session_io(_user_node, db, session_id, message_id)
    if node is None:
        raise OperationError("message_not_found", scope="session")
    chapter = {"pinned_at": time.time()} if pinned else None
    await _session_io(db.merge_node_metadata, session_id, message_id, {"chapter": chapter})
    _patch_open_views(_s, session_id, message_id, chapter)
    _s._broadcast(json.dumps({
        "type": "message_chapter_updated",
        "data": {
            "session_id": session_id,
            "message_id": message_id,
            "chapter": chapter,
            "request_id": cmd.get("request_id"),
            "action": "set_message_chapter",
        },
    }, default=str))
