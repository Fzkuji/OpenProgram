"""One line to the owner about the running version, with no update behind it.

Direct edits set aside and a return to a working version happen before any
client is connected, and no conversation asked for them. So the line goes into
the owner's most recent conversation, where the owner sees it whenever they
open it, and it never starts a model turn.
"""
from __future__ import annotations

import hashlib
import json
import time

_OWNER_SOURCES = frozenset({None, "", "web", "tui", "cli"})
_SEARCHED = 50
SOURCE = "self_update_notice"


def _node_id(notice_id: str) -> str:
    return "su_notice_" + hashlib.sha256(notice_id.encode("utf-8")).hexdigest()[:32]


def _last_reply(index):
    """The newest assistant turn on the branch the conversation is on."""
    node = index.nodes_by_id.get(index.head_id) if index.head_id else None
    seen = set()
    while node is not None and node.id not in seen:
        if node.role == "llm":
            return node
        seen.add(node.id)
        node = index.nodes_by_id.get(node.predecessor) if node.predecessor else None
    return None


def _append(db, session_id: str, notice_id: str, text: str) -> bool:
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call

    node_id = _node_id(notice_id)
    with db._session_lock(session_id):
        pair = db._open(session_id)
        if pair is None:
            return False
        _, index = pair
        reply = _last_reply(index)
        if reply is None:
            return False
        if node_id not in index.nodes_by_id:
            node = Call(
                id=node_id, role="code", name=SOURCE, created_at=time.time(),
                caller=reply.id, predecessor=reply.id,
                output=json.dumps({"notice": text}, ensure_ascii=False),
                metadata={"source": SOURCE, "status": "completed"},
            )
            SessionNodeWriter(db, session_id, advance_head=False).append(node, create_if_missing=False)
    return True


def tell(notice_id: str, text: str) -> bool:
    """Add the line once; ``False`` when the owner has no conversation to put it in."""
    from openprogram.runtime.context.session_db import default_db

    db = default_db()
    for row in db.list_sessions(limit=_SEARCHED):
        if row.get("source") not in _OWNER_SOURCES:
            continue
        session_id = row["id"]
        if _append(db, session_id, notice_id, text):
            return True
    return False
