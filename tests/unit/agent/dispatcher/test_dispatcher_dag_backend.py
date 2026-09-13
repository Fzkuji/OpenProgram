"""Verify dispatcher works when default_db() returns DagSessionDB.

The point of this file is not to re-test dispatcher logic — that's
already covered in test_dispatcher.py — but to prove the
DagSessionDB adapter satisfies the API surface dispatcher actually
hits at runtime: create_session, get_session, get_branch,
get_messages, append_message, set_head, update_session.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openprogram.agent import dispatcher as D
from openprogram.store import SessionStore as DagSessionDB


@pytest.fixture
def dag_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DagSessionDB:
    db = DagSessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


def _stub_loop(text: str, usage=None, tool_calls=None):
    def _stub(*, req, history, on_event, cancel_event, **_extra):
        on_event({"type": "chat_response",
                  "data": {"type": "stream_event",
                           "event": {"type": "text", "text": text}}})
        return text, usage or {"input_tokens": 5, "output_tokens": 2}, list(tool_calls or [])
    return _stub


def test_persists_user_and_assistant_on_dag(dag_db: DagSessionDB):
    events: list = []
    req = D.TurnRequest(
        session_id="s-dag-1",
        agent_id="claude",
        user_text="hello dag",
        source="cli",
    )
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("hi back")
    ):
        D.process_user_turn(req, on_event=events.append)

    msgs = dag_db.get_messages("s-dag-1")
    roles = [m["role"] for m in msgs]
    assert "user" in roles
    assert "assistant" in roles
    assistant = [m for m in msgs if m["role"] == "assistant"][-1]
    assert assistant["content"] == "hi back"


def test_persists_agent_request_and_iteration_counters(dag_db: DagSessionDB):
    req = D.TurnRequest(
        session_id="s-dag-counters",
        agent_id="claude",
        user_text="count",
        source="cli",
    )
    usage = {
        "input_tokens": 5,
        "output_tokens": 2,
        "provider_request_count": 3,
        "agent_iteration_count": 2,
    }
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("done", usage=usage)
    ):
        D.process_user_turn(req, on_event=lambda _event: None)

    assistant = [
        message for message in dag_db.get_messages("s-dag-counters")
        if message["role"] == "assistant"
    ][-1]
    assert assistant["execution_kind"] == "agent"
    assert assistant["provider_request_count"] == 3
    assert assistant["agent_iteration_count"] == 2


def test_head_id_advances_on_dag(dag_db: DagSessionDB):
    req = D.TurnRequest(
        session_id="s-dag-2",
        agent_id="claude",
        user_text="ping",
        source="cli",
    )
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("pong")
    ):
        D.process_user_turn(req, on_event=lambda _e: None)

    sess = dag_db.get_session("s-dag-2")
    assert sess is not None
    assert sess["head_id"] is not None
    # head should be the assistant message id (last appended)
    msgs = dag_db.get_messages("s-dag-2")
    assert sess["head_id"] == msgs[-1]["id"]


def test_first_turn_spawn_leaves_head_off_root(dag_db: DagSessionDB):
    """A session whose FIRST turn is a spawn turn never parks head on ROOT.

    Spawn turns are head-neutral (context/compaction.md §5): branch root,
    placeholder, reply and finalize all run with ``advance_head=False``.
    The ROOT node the turn-prep step seeds is the one write left, so
    seeding it through a head-advancing shim made "ROOT" the last value
    written and the session head stayed there for good. Ordinary sessions
    hid it because their first turn overwrites head milliseconds later.
    """
    req = D.TurnRequest(
        session_id="s-dag-spawn",
        agent_id="claude",
        user_text="go",
        source="agent_spawn",
        branch_from=None,
        # Cross-session spawn: the caller lives in another session's
        # graph, so this session has no turn of its own yet.
        spawn_caller="other-session-reply",
        advance_head=False,
    )
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("done")
    ):
        D.process_user_turn(req, on_event=lambda _e: None)

    # The spawn really ran — its branch root is on the graph.
    roots = [m for m in dag_db.get_messages("s-dag-spawn")
             if m.get("content") == "go"]
    assert roots, "spawn branch root was never written"

    assert (dag_db.get_session("s-dag-spawn") or {}).get("head_id") != "ROOT"


def test_cross_session_exact_fork_persists_source_provenance(
    dag_db: DagSessionDB,
):
    """A remote caller and a target predecessor are distinct DAG edges."""
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("target base")
    ):
        base = D.process_user_turn(
            D.TurnRequest(
                session_id="target-session",
                agent_id="claude",
                user_text="target prompt",
                source="cli",
            ),
            on_event=lambda _e: None,
        )
    target_head = (dag_db.get_session("target-session") or {})["head_id"]

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("remote result")
    ):
        spawned = D.process_user_turn(
            D.TurnRequest(
                session_id="target-session",
                agent_id="claude",
                user_text="continue remotely",
                source="agent_spawn",
                branch_from=base.assistant_msg_id,
                spawn_caller="source-assistant",
                spawned_from_session="source-session",
                advance_head=False,
            ),
            on_event=lambda _e: None,
        )

    assert (dag_db.get_session("target-session") or {})["head_id"] == target_head
    pair = dag_db._open("target-session")
    assert pair is not None
    user_node = pair[1].nodes_by_id[spawned.user_msg_id]
    assert user_node.predecessor == base.assistant_msg_id
    assert user_node.caller == "source-assistant"
    assert (user_node.metadata or {})["spawned_from_session"] == "source-session"


def test_history_passed_to_loop_on_dag(dag_db: DagSessionDB):
    # Round 1
    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub_loop("reply 1")
    ):
        D.process_user_turn(
            D.TurnRequest(session_id="s-dag-3", agent_id="claude",
                          user_text="first", source="cli"),
            on_event=lambda _e: None,
        )
    # Round 2: capture history
    captured_history: list = []

    def _stub2(*, req, history, on_event, cancel_event, **_extra):
        captured_history.extend(history)
        return "reply 2", {"input_tokens": 1, "output_tokens": 1}, []

    with __import__("unittest.mock", fromlist=["patch"]).patch.object(
        D, "_run_loop_blocking", side_effect=_stub2
    ):
        D.process_user_turn(
            D.TurnRequest(session_id="s-dag-3", agent_id="claude",
                          user_text="second", source="cli"),
            on_event=lambda _e: None,
        )

    contents = [m["content"] for m in captured_history]
    assert "first" in contents
    assert "reply 1" in contents
