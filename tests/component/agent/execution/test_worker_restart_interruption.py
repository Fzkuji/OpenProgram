"""Worker restart interrupts do not become user-visible turn failures."""
from __future__ import annotations

import threading
import importlib
from pathlib import Path

import pytest


@pytest.fixture
def restart_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from openprogram.runtime.context.session_db import SessionDB

    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.default_store", lambda: db,
    )
    return db


def test_worker_shutdown_finishes_agent_stream_as_control_failure(monkeypatch):
    from openprogram.runtime.conversation import run_control
    from openprogram.models.providers.utils.errors import ExecInterrupt
    agent_loop = importlib.import_module("openprogram.runtime.agent.loop")

    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(run_control, "_worker_stopping", stopping)

    class Stream:
        def __init__(self):
            self._result_event = threading.Event()
            self.failed = None

        def fail(self, exc):
            self.failed = exc
            self._result_event.set()

    stream = Stream()
    agent_loop._finish_interrupted_stream(
        stream, ExecInterrupt("worker_stopping"), [], None,
    )

    assert isinstance(stream.failed, ExecInterrupt)
    assert str(stream.failed) == "worker_stopping"
    assert stream._result_event.is_set()


def test_worker_shutdown_error_path_stops_before_transcript_mutation(monkeypatch):
    from openprogram.runtime.conversation import run_control
    from openprogram.runtime.conversation.turn_runtime.error_path import handle_turn_error

    stopping = threading.Event()
    stopping.set()
    monkeypatch.setattr(run_control, "_worker_stopping", stopping)

    touched = []
    monkeypatch.setattr(
        "openprogram.runtime.conversation.turn_runtime.error_path._fold_error_into_placeholder",
        lambda *args, **kwargs: touched.append("fold"),
    )
    monkeypatch.setattr(
        "openprogram.runtime.conversation.turn_runtime.error_path._write_standalone_error_node",
        lambda *args, **kwargs: touched.append("standalone"),
    )

    try:
        handle_turn_error(
            db=None,
            req=None,
            session=None,
            exc=RuntimeError("provider disconnected"),
            writer=None,
            user_msg_id="user-1",
            assistant_msg_id="assistant-1",
            placeholder_inserted=True,
            project_baseline=None,
            on_event=lambda _event: touched.append("event"),
            started_at=0.0,
        )
    except BaseException as exc:
        from openprogram.models.providers.utils.errors import ExecInterrupt

        assert isinstance(exc, ExecInterrupt)
        assert str(exc) == "worker_stopping"
    else:
        raise AssertionError("worker shutdown must interrupt error handling")

    assert touched == []


def test_restart_initial_reuses_existing_anchors_and_user_context(
    restart_db, monkeypatch,
):
    from openprogram.runtime.conversation import turn_runtime as dispatcher_module
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.state.store.session.nodes import Call, ROLE_LLM, ROLE_USER
    from openprogram.state.store import SessionNodeWriter

    session_id = "restart-anchors"
    user_id = "user-1"
    assistant_id = "user-1_reply"
    restart_db.create_session(session_id, "main", source="component")
    writer = SessionNodeWriter(restart_db, session_id)
    writer.append(Call(
        id=user_id, role=ROLE_USER, output="continue the task", created_at=1.0,
    ))
    writer.append(Call(
        id=assistant_id, role=ROLE_LLM, output="", predecessor=user_id,
        created_at=1.1, metadata={"status": "running"},
    ))
    # Move the persisted session head elsewhere. Restart recovery must use
    # the continuation's user anchor rather than this newer active head.
    writer.append(Call(
        id="unrelated-head", role=ROLE_LLM, output="later branch",
        predecessor=assistant_id, created_at=2.0,
    ))

    seen = {}

    def run_loop(*, req, history, **_kwargs):
        seen["req"] = req
        seen["history"] = history
        return "resumed reply", {"input_tokens": 1, "output_tokens": 1}, []

    monkeypatch.setattr(dispatcher_module, "_run_loop_blocking", run_loop)
    monkeypatch.setattr(dispatcher_module, "_memory_write", lambda _sid: None)

    result = dispatcher_module._process_turn_once(
        TurnRequest(
            session_id=session_id,
            user_text="continue the task",
            user_msg_id=user_id,
            agent_id="main",
            source="component",
        ),
        execution_context={"restart_initial": True},
    )

    assert result.failed is False
    assert result.user_msg_id == user_id
    assert result.assistant_msg_id == assistant_id
    # The user anchor is supplied exactly once by agent_loop from req; the
    # rendered history ending at that anchor contains no prior turns. The
    # newer active head must not leak into the resumed model context.
    assert seen["history"] == []
    assert seen["req"].user_text == "continue the task"
    assert seen["req"].user_already_persisted is True
    messages = restart_db.get_messages(session_id)
    assert [item["id"] for item in messages].count(user_id) == 1
    assert [item["id"] for item in messages].count(assistant_id) == 1
    assistant = next(item for item in messages if item["id"] == assistant_id)
    assert assistant["content"] == "resumed reply"

