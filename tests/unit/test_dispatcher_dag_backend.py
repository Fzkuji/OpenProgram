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
from openprogram.context.session_db import DagSessionDB


@pytest.fixture
def dag_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DagSessionDB:
    db = DagSessionDB(tmp_path / "dag.sqlite")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    return db


def _stub_loop(text: str, usage=None, tool_calls=None):
    def _stub(*, req, history, on_event, cancel_event):
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

    def _stub2(*, req, history, on_event, cancel_event):
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
