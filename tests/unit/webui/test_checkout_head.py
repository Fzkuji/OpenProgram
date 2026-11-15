"""Checkout onto a reply inside the same conversation.

``POST /api/chat/checkout`` onto a reply moves HEAD there, so the active
branch (the transcript) ends at it, and the next message attaches under
HEAD as a sibling of the old continuation — the two then switch with
``< N/M >``. The response carries the HEAD before the move; checking it
out again leaves the DAG as it was. A switch is a view change: it goes
ahead while a turn runs.
"""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.interfaces.server.routes import chat_branches as routes
from openprogram.runtime.agent import turn_request as agent_turn_request
from openprogram.runtime.context.git import sibling_navigation_index
from openprogram.runtime.conversation import turn_runtime as D
from openprogram.state.store import SessionStore

SID = "s-branch"


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SessionStore]:
    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: store)
    try:
        yield store
    finally:
        store.close()


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch):
    from openprogram.interfaces.server import server as srv

    state = {"running": False}
    monkeypatch.setattr(srv, "_sessions", {SID: {"id": SID, "messages": []}})
    monkeypatch.setattr(srv, "_sessions_lock", threading.Lock())
    monkeypatch.setattr(srv, "_is_selected_run_active", lambda sid: state["running"])
    monkeypatch.setattr(srv, "_invalidate_messages", lambda sid: None)
    monkeypatch.setattr(srv, "_save_session", lambda sid: None)
    monkeypatch.setattr(srv, "refresh_context_stats", lambda sid: None)
    return state


def _persist_user(db: SessionStore, uid: str, text: str) -> None:
    """The WS chat's pre-persist: the user node hangs under HEAD."""
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import ROLE_USER, Call

    if db.get_session(SID) is None:
        db.create_session(SID, "main")
    writer = SessionNodeWriter(db, SID)
    if not db.message_exists(SID, "ROOT"):
        writer.append(Call(id="ROOT", role=ROLE_USER, output="", metadata={"display": "root"}))
    pred = (db.get_session(SID) or {}).get("head_id") or "ROOT"
    writer.append(Call(id=uid, role=ROLE_USER, output=text, caller="ROOT", predecessor=pred))
    db.set_head(SID, uid)


def _turn(db: SessionStore, uid: str, text: str, reply: str) -> list[dict]:
    """One user turn with a stub model; returns the history the model saw."""
    _persist_user(db, uid, text)
    seen: list[dict] = []

    def _stub(*, req, history, on_event, cancel_event, **_extra):
        seen.extend(history)
        return reply, {"input_tokens": 1, "output_tokens": 1}, []

    with patch.object(D, "_run_loop_blocking", side_effect=_stub):
        D.process_user_turn(
            agent_turn_request.TurnRequest(
                session_id=SID, agent_id="claude", user_text=text, source="web",
                user_msg_id=uid, user_already_persisted=True,
            ),
            on_event=lambda _e: None,
        )
    return seen


def _checkout(msg_id: str):
    response = asyncio.run(routes.post_chat_checkout({"session_id": SID, "msg_id": msg_id}))
    return response.status_code, json.loads(response.body)


def _branch_ids(db: SessionStore) -> list[str]:
    return [m["id"] for m in db.get_branch(SID)]


def _two_turns(db: SessionStore) -> None:
    _turn(db, "u1", "first", "answer one")
    _turn(db, "u2", "second", "answer two")
    assert _branch_ids(db) == ["u1", "u1_reply", "u2", "u2_reply"]


def test_checkout_moves_head_and_hides_the_tail(db, server):
    _two_turns(db)
    status, body = _checkout("u1_reply")
    assert status == 200
    assert body["head_id"] == "u1_reply"
    assert body["previous_head_id"] == "u2_reply"
    assert _branch_ids(db) == ["u1", "u1_reply"]
    # Nothing was created or removed.
    assert {m["id"] for m in db.get_messages(SID)} >= {"u2", "u2_reply"}


def test_send_after_checkout_creates_a_sibling_of_the_old_continuation(db, server):
    _two_turns(db)
    _checkout("u1_reply")
    history = _turn(db, "u3", "other question", "answer three")
    contents = [m.get("content") for m in history]
    assert "answer one" in contents and "answer two" not in contents
    assert _branch_ids(db) == ["u1", "u1_reply", "u3", "u3_reply"]
    nav = sibling_navigation_index(db.get_messages(SID), target_ids=["u2", "u3"])
    assert nav["u3"][:2] == (2, 2)
    assert nav["u2"][:2] == (1, 2)


def test_back_restores_the_previous_leaf_and_leaves_nothing(db, server):
    _two_turns(db)
    before = sorted(m["id"] for m in db.get_messages(SID))
    _, body = _checkout("u1_reply")
    status, back = _checkout(body["previous_head_id"])
    assert status == 200
    assert back["previous_head_id"] == "u1_reply"
    assert _branch_ids(db) == ["u1", "u1_reply", "u2", "u2_reply"]
    assert sorted(m["id"] for m in db.get_messages(SID)) == before


def test_switching_goes_ahead_while_a_turn_runs(db, server):
    _two_turns(db)
    server["running"] = True
    status, body = _checkout("u1_reply")
    assert status == 200 and body["head_id"] == "u1_reply"
    assert (db.get_session(SID) or {}).get("head_id") == "u1_reply"
