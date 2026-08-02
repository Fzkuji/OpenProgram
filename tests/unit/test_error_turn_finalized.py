"""A turn that fails is a finished turn, not a missing one.

An exception mid-turn used to skip finalization entirely, so the errored
turn left no git commit — a hole in the timeline exactly where something
went wrong. The error node is now committed like any other terminal node,
head stops on it, and a retry forks from its predecessor while the failed
line stays in the graph without entering the retry's context.

See docs/reference/design/runtime/dag/overview.md §"Failure and retry".
"""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from openprogram.agent import dispatcher as D
from openprogram.store import SessionStore


@pytest.fixture
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionStore:
    store = SessionStore(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.store.session_store.default_store", lambda: store)
    monkeypatch.setattr("openprogram.store.default_store", lambda: store)
    return store


def _persist_user(db: SessionStore, sid: str, uid: str, text: str) -> None:
    """Mirror of webui server._append_msg's SessionDB writes."""
    from openprogram.context.nodes import Call, ROLE_USER
    from openprogram.store import GraphStoreShim

    if db.get_session(sid) is None:
        db.create_session(sid, "main")
    shim = GraphStoreShim(db, sid)
    if not db.message_exists(sid, "ROOT"):
        shim.append(Call(id="ROOT", role=ROLE_USER, output="",
                         metadata={"display": "root"}))
    pred = (db.get_session(sid) or {}).get("head_id") or ""
    shim.append(Call(id=uid, role=ROLE_USER, output=text,
                     caller="ROOT", predecessor=pred or "ROOT"))
    db.set_head(sid, uid)


def _run_turn(sid: str, uid: str, text: str, reply: str) -> list[dict]:
    """A turn whose agent loop succeeds."""
    captured: list[dict] = []

    def _stub(*, req, history, on_event, cancel_event, **_extra):
        captured.extend(history)
        return reply, {"input_tokens": 1, "output_tokens": 1}, []

    with patch.object(D, "_run_loop_blocking", side_effect=_stub):
        D.process_user_turn(
            D.TurnRequest(session_id=sid, agent_id="claude", user_text=text,
                          source="web", user_msg_id=uid,
                          user_already_persisted=True),
            on_event=lambda _e: None,
        )
    return captured


def _run_failing_turn(sid: str, uid: str, text: str,
                      exc: Exception | None = None):
    """A turn whose agent loop raises partway through."""
    exc = exc or RuntimeError("provider exploded mid-stream")

    def _boom(*, req, history, on_event, cancel_event, **_extra):
        raise exc

    with patch.object(D, "_run_loop_blocking", side_effect=_boom):
        return D.process_user_turn(
            D.TurnRequest(session_id=sid, agent_id="claude", user_text=text,
                          source="web", user_msg_id=uid,
                          user_already_persisted=True),
            on_event=lambda _e: None,
        )


def _retry_fork(db: SessionStore, sid: str, src_id: str, new_id: str) -> None:
    """Mirror of _fork_user_turn_and_run's fork path DB writes."""
    src = next(m for m in db.get_messages(sid) if m["id"] == src_id)
    db.append_message(sid, {
        "role": "user", "id": new_id, "content": src.get("content", ""),
        "timestamp": time.time(),
        "predecessor": src.get("predecessor") or "ROOT",
        "forked_from": src_id,
    })
    db.set_head(sid, new_id)


def _commit_subjects(db: SessionStore, sid: str) -> list[str]:
    git, _ = db._open(sid)
    return [c.message for c in git.log(limit=100)]


# --- the error node is a real terminal node --------------------------------

def test_failed_turn_reports_failure(db: SessionStore):
    _persist_user(db, "s-err", "u1", "hello")
    result = _run_failing_turn("s-err", "u1", "hello")

    assert result.failed is True
    assert "provider exploded" in (result.error or "")


def test_error_node_enters_the_graph(db: SessionStore):
    sid = "s-err-node"
    _persist_user(db, sid, "u1", "hello")
    _run_failing_turn(sid, "u1", "hello")

    node = next((m for m in db.get_messages(sid) if m["id"] == "u1_reply"), None)
    assert node is not None, "the failed turn left no node in the graph"
    assert node.get("status") == "error"
    assert "provider exploded" in (node.get("content") or "")


def test_head_stops_on_the_error_node(db: SessionStore):
    sid = "s-err-head"
    _persist_user(db, sid, "u1", "hello")
    _run_failing_turn(sid, "u1", "hello")

    session = db.get_session(sid) or {}
    assert session.get("head_id") == "u1_reply"
    assert session.get("status") == "failed"


def test_error_turn_is_committed_to_git(db: SessionStore):
    """The hole this whole change exists to close."""
    sid = "s-err-git"
    _persist_user(db, sid, "u1", "hello")
    before = len(_commit_subjects(db, sid))

    _run_failing_turn(sid, "u1", "hello")

    subjects = _commit_subjects(db, sid)
    assert len(subjects) > before, "the failed turn produced no commit"
    assert any("error" in s for s in subjects), (
        f"no commit marks the errored turn: {subjects}")


def test_error_and_success_turns_both_commit(db: SessionStore):
    """A failure between two good turns leaves no gap in the timeline."""
    sid = "s-err-mixed"
    _persist_user(db, sid, "u1", "first")
    _run_turn(sid, "u1", "first", "reply 1")

    _persist_user(db, sid, "u2", "second")
    _run_failing_turn(sid, "u2", "second")

    _persist_user(db, sid, "u3", "third")
    _run_turn(sid, "u3", "third", "reply 3")

    subjects = _commit_subjects(db, sid)
    assert any("first" in s for s in subjects)
    assert any("second" in s for s in subjects), "errored turn is missing"
    assert any("third" in s for s in subjects)


# --- retry over a failed turn ---------------------------------------------

def test_retry_after_error_keeps_the_failed_line(db: SessionStore):
    """The failed branch stays in the graph — it is a record, not garbage."""
    sid = "s-err-retry"
    _persist_user(db, sid, "u1", "hello")
    _run_failing_turn(sid, "u1", "hello")

    _retry_fork(db, sid, "u1", "u1r")
    _run_turn(sid, "u1r", "hello", "reply after retry")

    ids = {m["id"] for m in db.get_messages(sid)}
    assert "u1_reply" in ids, "the failed attempt was discarded"
    assert "u1r_reply" in ids


def test_retry_context_excludes_the_failed_attempt(db: SessionStore):
    """A retry must not be told about the error it is retrying."""
    sid = "s-err-ctx"
    _persist_user(db, sid, "u1", "first")
    _run_turn(sid, "u1", "first", "reply 1")

    _persist_user(db, sid, "u2", "second")
    _run_failing_turn(sid, "u2", "second", RuntimeError("kaboom"))

    _retry_fork(db, sid, "u2", "u2r")
    hist = _run_turn(sid, "u2r", "second", "reply 2 retry")

    contents = [str(m.get("content") or "") for m in hist]
    assert any("reply 1" in c for c in contents), "shared prefix was lost"
    assert not any("kaboom" in c for c in contents), (
        "the failed attempt leaked into the retry's context")


def test_retry_branch_is_the_active_one(db: SessionStore):
    sid = "s-err-branch"
    _persist_user(db, sid, "u1", "hello")
    _run_failing_turn(sid, "u1", "hello")

    _retry_fork(db, sid, "u1", "u1r")
    _run_turn(sid, "u1r", "hello", "good reply")

    ids = [m["id"] for m in db.get_branch(sid)]
    assert ids == ["u1r", "u1r_reply"]
    assert "u1_reply" not in ids, "failed line is on the active branch"


def test_render_context_skips_the_failed_branch(db: SessionStore):
    """render_context walks the active branch, so the failed line is absent.

    The error node stays in the graph and stays reachable; it is simply on
    a branch the new head does not descend from.
    """
    from openprogram.context.nodes import render_context
    from openprogram.store.session.graphstore_shim import GraphStoreShim

    sid = "s-err-render"
    _persist_user(db, sid, "u1", "first")
    _run_turn(sid, "u1", "first", "reply 1")

    _persist_user(db, sid, "u2", "second")
    _run_failing_turn(sid, "u2", "second", RuntimeError("kaboom"))

    _retry_fork(db, sid, "u2", "u2r")
    _run_turn(sid, "u2r", "second", "reply 2 retry")

    graph = GraphStoreShim(db, sid).load()
    read_ids = render_context(graph, head_id="u2r_reply")

    assert "u1_reply" in read_ids, "shared prefix missing from context"
    assert "u2r_reply" in read_ids, "the retry's own reply is missing"
    assert "u2_reply" not in read_ids, (
        "the failed attempt was rendered into the retry's context")
    # ...but the failed node is still on record.
    assert graph.nodes.get("u2_reply") is not None
