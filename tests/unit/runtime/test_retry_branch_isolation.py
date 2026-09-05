"""Regression: retrying a user turn must isolate the new branch.

Simulates the exact webui flow: WS chat pre-persists the user node
(server._append_msg shape), dispatcher runs with
user_already_persisted=True, then POST /api/chat/retry forks a sibling
user message (webui/_chat_routes._fork_user_turn_and_run +
persistence.save_messages) and re-runs. Assert:

  * the history handed to the agent loop contains ONLY the active
    branch (no sibling attempts),
  * get_branch after the retry contains only the new branch.
"""
from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.store import SessionStore


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SessionStore]:
    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.store.default_store", lambda: store)
    try:
        yield store
    finally:
        store.close()


def _persist_user(db: SessionStore, sid: str, uid: str, text: str) -> None:
    """Mirror of webui server._append_msg's SessionDB writes."""
    from openprogram.context.nodes import Call, ROLE_USER
    from openprogram.store import SessionNodeWriter

    if db.get_session(sid) is None:
        db.create_session(sid, "main")
    shim = SessionNodeWriter(db, sid)
    if not db.message_exists(sid, "ROOT"):
        shim.append(Call(id="ROOT", role=ROLE_USER, output="",
                         metadata={"display": "root"}))
    pred = (db.get_session(sid) or {}).get("head_id") or ""
    shim.append(Call(id=uid, role=ROLE_USER, output=text,
                     caller="ROOT", predecessor=pred or "ROOT"))
    db.set_head(sid, uid)


def _run_turn(sid: str, uid: str, text: str, reply: str) -> list[dict]:
    """process_user_turn with a stub loop; returns captured history."""
    captured: list[dict] = []

    def _stub(*, req, history, on_event, cancel_event, **_extra):
        captured.extend(history)
        return reply, {"input_tokens": 1, "output_tokens": 1}, []

    with patch.object(D, "_run_loop_blocking", side_effect=_stub):
        D.process_user_turn(
            D.TurnRequest(session_id=sid, agent_id="claude",
                          user_text=text, source="web",
                          user_msg_id=uid, user_already_persisted=True),
            on_event=lambda _e: None,
        )
    return captured


def _retry_fork(db: SessionStore, sid: str, src_id: str, new_id: str) -> None:
    """Mirror of _fork_user_turn_and_run's fork path DB writes:
    _append_msg appends the sibling user node, then explicitly moves
    HEAD (append itself only advances on chain extension)."""
    src = next(m for m in db.get_messages(sid) if m["id"] == src_id)
    db.append_message(sid, {
        "role": "user", "id": new_id, "content": src.get("content", ""),
        "timestamp": time.time(),
        "predecessor": src.get("predecessor") or "ROOT",
        "forked_from": src_id,
    })
    db.set_head(sid, new_id)


def test_retry_first_turn_isolates_branch(db: SessionStore):
    sid = "s-retry-1"
    _persist_user(db, sid, "u1", "hello")
    hist = _run_turn(sid, "u1", "hello", "reply 1")
    assert [m.get("content") for m in hist if m.get("role") == "user"] == []

    _retry_fork(db, sid, "u1", "u1r")
    hist = _run_turn(sid, "u1r", "hello", "reply 1 retry")
    contents = [m.get("content") for m in hist]
    assert "reply 1" not in contents, "old attempt's reply leaked into LLM context"
    assert contents.count("hello") == 0, "sibling user attempts leaked into LLM context"

    branch = db.get_branch(sid)
    ids = [m["id"] for m in branch]
    assert "u1" not in ids and "u1_reply" not in ids
    assert ids == ["u1r", "u1r_reply"]


def test_retry_mid_session_isolates_branch(db: SessionStore):
    sid = "s-retry-2"
    _persist_user(db, sid, "u1", "hello")
    _run_turn(sid, "u1", "hello", "reply 1")
    _persist_user(db, sid, "u2", "how are you")
    _run_turn(sid, "u2", "how are you", "reply 2")

    _retry_fork(db, sid, "u2", "u2r")
    hist = _run_turn(sid, "u2r", "how are you", "reply 2 retry")
    contents = [m.get("content") for m in hist]
    assert "reply 1" in contents           # shared prefix stays
    assert "reply 2" not in contents       # forked-away sibling subtree gone
    assert contents.count("how are you") == 0

    branch = db.get_branch(sid)
    ids = [m["id"] for m in branch]
    assert ids == ["u1", "u1_reply", "u2r", "u2r_reply"]


def test_double_retry_isolates_branch(db: SessionStore):
    sid = "s-retry-3"
    _persist_user(db, sid, "u1", "hi")
    _run_turn(sid, "u1", "hi", "r1")
    _retry_fork(db, sid, "u1", "u1r")
    _run_turn(sid, "u1r", "hi", "r2")
    _retry_fork(db, sid, "u1r", "u1r2")
    hist = _run_turn(sid, "u1r2", "hi", "r3")
    contents = [m.get("content") for m in hist]
    assert "r1" not in contents and "r2" not in contents
    assert contents.count("hi") == 0

    branch = db.get_branch(sid)
    assert [m["id"] for m in branch] == ["u1r2", "u1r2_reply"]


def test_dag_render_excludes_prompt_and_placeholder(db: SessionStore):
    """The dispatcher inserts the assistant placeholder BEFORE the loop
    runs, so at prepare() time the branch tip is [..., userN, empty
    placeholder]. The DAG render must exclude both (agent_loop re-adds
    the user prompt itself) — with the old guard the trailing user
    message was rendered AND re-added as the prompt, so the model saw
    it twice ("I see 2 user message(s)")."""
    from openprogram.agent.internals._turn_lifecycle import insert_placeholder
    from openprogram.context.engine import DefaultContextEngine

    sid = "s-render-1"
    # Turn 1 complete, turn 2 in flight (placeholder at tip).
    _persist_user(db, sid, "u1", "hello")
    insert_placeholder(db, sid, "u1_reply", "u1", "web")
    db.set_head(sid, "u1_reply")
    from openprogram.store import SessionNodeWriter
    SessionNodeWriter(db, sid).update("u1_reply", output="reply 1",
                                   metadata={"status": "completed"})
    _persist_user(db, sid, "u2", "second question")
    insert_placeholder(db, sid, "u2_reply", "u2", "web")
    db.set_head(sid, "u2_reply")

    engine = DefaultContextEngine.__new__(DefaultContextEngine)
    msgs = engine._build_messages_from_dag(
        session_id=sid, history=[], model=None)
    texts = []
    for m in msgs:
        c = getattr(m, "content", None)
        if isinstance(c, list):
            texts.append(" ".join(getattr(p, "text", "") or "" for p in c))
        else:
            texts.append(str(c or ""))
    joined = "\n".join(texts)
    assert "hello" in joined and "reply 1" in joined
    assert "second question" not in joined, (
        "trailing user node leaked into the DAG render — the model "
        "would see the current user message twice")
    roles = [getattr(m, "role", "") for m in msgs]
    assert roles.count("assistant") == 1, "empty placeholder was rendered"


def test_checkout_switches_both_views(db: SessionStore):
    sid = "s-retry-4"
    _persist_user(db, sid, "u1", "hi")
    _run_turn(sid, "u1", "hi", "r1")
    _retry_fork(db, sid, "u1", "u1r")
    _run_turn(sid, "u1r", "hi", "r2")

    # sibling checkout back to the ORIGINAL branch's leaf
    leaf = db.get_deepest_leaf(sid, "u1")
    db.set_head(sid, leaf)
    branch = db.get_branch(sid)
    assert [m["id"] for m in branch] == ["u1", "u1_reply"]

    # a follow-up turn on the checked-out branch sees only that branch
    _persist_user(db, sid, "u2", "next")
    hist = _run_turn(sid, "u2", "next", "r-next")
    contents = [m.get("content") for m in hist]
    assert "r1" in contents
    assert "r2" not in contents
