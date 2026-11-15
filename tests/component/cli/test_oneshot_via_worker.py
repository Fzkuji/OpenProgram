"""A ``--print`` turn runs on the running worker.

The CLI connects to the worker's WebSocket with the owner token, sends the
same ``chat`` command the Web UI sends, and relays the frames: text deltas
stream, the result ends the turn, a follow-up question is answered from
the terminal, an error frame becomes an error with its code, and a
structured-output turn returns the parsed value the result frame carries.
A new conversation carries the model ``--model`` names and the directory
the command runs in. Without a worker the turn runs in-process.
"""
from __future__ import annotations

import io
import json
import sys
import threading
import types
from contextlib import contextmanager

import pytest

from openprogram.host.security.backend_endpoint import BackendEndpoint
from openprogram.interfaces.cli import worker_turn
from openprogram.interfaces.cli.worker_turn import (
    WorkerTurnCancelled,
    WorkerTurnError,
    resolve_worker,
    run_turn_via_worker,
    structured_output_error,
)

TOKEN = "t" * 43
SESSION = "local_test1234"


@contextmanager
def fake_worker(script, capabilities=None, identity=None):
    """A worker that runs ``script(connection, command)`` once a chat arrives.

    ``capabilities`` are the names the worker announces on connect
    (``worker_capabilities``); without them it is an older worker.

    ``script`` sends frames with ``connection.send(dict)`` and may read
    further commands with ``connection.recv_json()``. The handshake frames
    the real worker sends first are sent here too. The request headers of
    every connection are kept for the test to inspect.
    """
    from websockets.sync.server import serve

    seen: dict = {"headers": [], "commands": []}

    def handler(connection):
        request_headers = connection.request.headers
        seen["headers"].append({
            "Authorization": request_headers.get("Authorization"),
            "Origin": request_headers.get("Origin"),
        })

        def send(frame: dict) -> None:
            connection.send(json.dumps(frame))

        def recv_json() -> dict:
            command = json.loads(connection.recv())
            seen["commands"].append(command)
            return command

        connection.send_frame = send
        connection.recv_json = recv_json
        for frame in (
            {"type": "functions_list", "data": []},
            {"type": "provider_info", "data": {}},
            {"type": "frontend_build", "data": {"id": "test"}},
            *([{"type": "worker_capabilities",
                "data": {name: True for name in capabilities or ()},
                **({"server": identity} if identity else {})}]
              if capabilities or identity else []),
        ):
            send(frame)
        command = recv_json()
        script(connection, command)

    server = serve(handler, "127.0.0.1", 0)
    port = server.socket.getsockname()[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield BackendEndpoint(origin=f"http://127.0.0.1:{port}", token=TOKEN), seen
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _ack(connection, command, **extra):
    connection.send_frame({"type": "chat_ack", "data": {
        "session_id": command["session_id"], "msg_id": "u1",
        "execution_id": "exec1", "status_version": 3,
        "client_request_id": command.get("client_request_id"), **extra,
    }})


def _response(connection, session_id, **data):
    connection.send_frame({"type": "chat_response",
                           "data": {"session_id": session_id, **data}})


def test_turn_streams_text_and_ends_with_the_result():
    def script(connection, command):
        assert command["action"] == "chat"
        assert command["text"] == "hello"
        assert command["session_id"] == SESSION
        assert "response_format" not in command
        _ack(connection, command)
        # Another session's frames share the socket and are skipped.
        _response(connection, "other", type="stream_event",
                  event={"type": "text", "text": "NOT MINE"})
        _response(connection, "other", type="result", content="NOT MINE")
        _response(connection, SESSION, type="status", content="thinking")
        _response(connection, SESSION, type="stream_event",
                  event={"type": "text", "text": "hel"})
        _response(connection, SESSION, type="stream_event",
                  event={"type": "thinking", "text": "hmm"})
        _response(connection, SESSION, type="stream_event",
                  event={"type": "text", "text": "lo"})
        _response(connection, SESSION, type="result", content="hello back",
                  msg_id="u1", assistant_msg_id="a1")

    deltas: list[str] = []
    with fake_worker(script) as (endpoint, seen):
        turn = run_turn_via_worker(endpoint, SESSION, "hello", on_text=deltas.append)

    assert turn.reply == "hello back"
    assert turn.structured_output is None
    assert (turn.msg_id, turn.assistant_msg_id) == ("u1", "a1")
    assert deltas == ["hel", "lo"]
    headers = seen["headers"][0]
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Origin"] == endpoint.origin


def test_frames_of_a_sub_agent_branch_do_not_end_the_turn():
    """A sub-agent the turn spawns runs in the same session under its own
    message id: its text, its result and its error are not this turn's."""
    def script(connection, command):
        _ack(connection, command)
        sid = command["session_id"]
        _response(connection, sid, type="stream_event", msg_id="child1",
                  event={"type": "text", "text": "child text"})
        _response(connection, sid, type="error", msg_id="child1",
                  content="[denied] the child was refused a tool")
        _response(connection, sid, type="result", msg_id="child1",
                  content="the child's report", assistant_msg_id="child1_reply")
        _response(connection, sid, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "parent text"})
        _response(connection, sid, type="result", msg_id="u1",
                  content="the parent's reply", assistant_msg_id="u1_reply")

    deltas: list[str] = []
    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "do it", on_text=deltas.append)
    assert turn.reply == "the parent's reply"
    assert turn.assistant_msg_id == "u1_reply"
    assert deltas == ["parent text"]


def test_frames_before_the_ack_belong_to_this_send_only_when_they_say_so():
    """Before the ack names the turn, another branch of the same session can
    end (its result) or be stopped (its cancelled): neither is this turn's.
    Only a frame echoing this send's request id is, as a refusal is."""
    def script(connection, command):
        sid = command["session_id"]
        _response(connection, sid, type="result", msg_id="other_branch",
                  content="another branch's reply")
        _response(connection, sid, type="cancelled", content="cancelled")
        _response(connection, sid, type="error", content="another branch failed")
        _ack(connection, command)
        _response(connection, sid, type="result", msg_id="u1", content="mine",
                  assistant_msg_id="u1_reply")

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go")
    assert turn.reply == "mine"


def test_follow_up_question_is_answered_through_the_socket():
    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="follow_up_question",
                  question="Which one?", function="ask_user")
        answer = connection.recv_json()
        assert answer == {"action": "follow_up_answer", "session_id": SESSION,
                          "answer": "the second"}
        _response(connection, SESSION, type="follow_up_question",
                  question="Sure?", function="ask_user")
        unanswered = connection.recv_json()
        assert unanswered["answer"] == {"_cancelled": True}
        _response(connection, SESSION, type="result", content="done")

    answers = iter(["the second", None])
    asked: list[tuple[str, str]] = []

    def ask(question, function):
        asked.append((question, function))
        return next(answers)

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", ask=ask)

    assert turn.reply == "done"
    assert asked == [("Which one?", "ask_user"), ("Sure?", "ask_user")]


def test_error_frame_carries_code_and_issues():
    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="error",
                  content="[error] StructuredOutputValidationError: nope",
                  code="validation_failed", issues=[{"path": "$.a", "message": "x"}])

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError) as info:
            run_turn_via_worker(endpoint, SESSION, "go")
    assert info.value.code == "validation_failed"
    assert info.value.issues == [{"path": "$.a", "message": "x"}]
    assert (info.value.reason, info.value.retryable, info.value.kind) == (None, None, None)
    assert str(info.value).startswith("[error] StructuredOutputValidationError")


def test_error_frame_carries_the_runtime_classification():
    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="error", content="[error] stalled",
                  reason="transport", retryable=True, kind="stream_idle")

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError) as info:
            run_turn_via_worker(endpoint, SESSION, "go")
    assert (info.value.reason, info.value.retryable, info.value.kind) == (
        "transport", True, "stream_idle")


def test_admission_refusal_before_the_ack_ends_the_turn():
    def script(connection, command):
        _response(connection, SESSION, type="error", code="session_busy",
                  content="a turn is already running",
                  client_request_id=command["client_request_id"])

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError) as info:
            run_turn_via_worker(endpoint, SESSION, "go")
    assert info.value.code == "session_busy"


def test_cancelled_frame_raises_cancelled():
    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="cancelled", content="cancelled")

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnCancelled):
            run_turn_via_worker(endpoint, SESSION, "go")


def test_socket_closing_before_the_ack_is_an_error():
    def script(connection, command):
        connection.close()

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError, match="before the turn started"):
            run_turn_via_worker(endpoint, SESSION, "go")


@pytest.fixture
def quick_reattach(monkeypatch):
    """A reattach window measured in tenths of a second."""
    monkeypatch.setattr(worker_turn, "_REATTACH_WINDOW_S", 0.5)
    monkeypatch.setattr(worker_turn, "_REATTACH_INTERVAL_S", 0.02)
    monkeypatch.setattr(worker_turn, "_SNAPSHOT_INTERVAL_S", 0.1)


def _replay(connection, status, **snapshot):
    connection.send_frame({"type": "execution.replay", "execution_id": "exec1",
                           "snapshot": {"execution_id": "exec1", "status": status, **snapshot}})


def test_no_worker_after_the_socket_closed_mid_turn_is_an_error(quick_reattach):
    """After the ack the turn outlives the socket; without a worker to
    follow it on, the relay says so and where to look."""
    def script(connection, command):
        _ack(connection, command)
        connection.close()

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError, match="none came back.*Web UI shows it"):
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: None)


def test_turn_is_followed_on_the_worker_that_comes_back(quick_reattach):
    """A worker restart closes the socket; the relay reconnects, reads the
    execution's state and relays the rest of the same turn."""
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        assert command == {"action": "execution.replay", "execution_id": "exec1",
                           "after_sequence": 0, "snapshot_only": True}
        _replay(connection, "running")
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "after the restart"})
        _response(connection, SESSION, type="result", content="after the restart",
                  msg_id="u1", assistant_msg_id="u1_reply")

    deltas: list[str] = []
    with fake_worker(script) as (endpoint, seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", on_text=deltas.append,
                                   reconnect=lambda: endpoint)
    assert turn.reply == "after the restart"
    assert (turn.msg_id, turn.assistant_msg_id) == ("u1", "u1_reply")
    assert deltas == ["after the restart"]
    assert [c["action"] for c in seen["commands"]] == ["chat", "execution.replay"]
    assert len(seen["headers"]) == 2


def test_turn_that_ended_while_no_socket_was_open_reads_its_reply(quick_reattach):
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, "completed")
        load = connection.recv_json()
        assert load == {"action": "load_session", "session_id": SESSION}
        connection.send_frame({"type": "session_loaded", "data": {"id": SESSION, "messages": [
            {"id": "u1", "role": "user", "content": "go"},
            {"id": "u1_reply", "role": "assistant",
             "content": [{"type": "text", "text": "done "}, {"type": "text", "text": "while away"}]},
        ]}})

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)
    assert turn.reply == "done while away"
    assert (turn.msg_id, turn.assistant_msg_id) == ("u1", "u1_reply")


def test_quiet_socket_after_a_reattach_reads_the_state_again(quick_reattach):
    """The turn ended between the state read and the frames: nothing arrives,
    so the relay asks again and finds it completed."""
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, "running")
        again = connection.recv_json()
        assert again["action"] == "execution.replay"
        _replay(connection, "completed")
        assert connection.recv_json()["action"] == "load_session"
        connection.send_frame({"type": "session_loaded", "data": {"id": SESSION, "messages": [
            {"id": "u1_reply", "role": "assistant", "content": "late reply"}]}})

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)
    assert turn.reply == "late reply"


@pytest.mark.parametrize("status, reason, raises, match", [
    ("failed", "provider_error", WorkerTurnError, "failed on the worker \\(provider_error\\)"),
    ("cancelled", None, WorkerTurnCancelled, "cancelled"),
    ("reconciliation_required", "effect_reconciliation", WorkerTurnError, "outcome is unknown"),
])
def test_execution_that_ended_badly_while_away_is_reported(quick_reattach, status, reason, raises, match):
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, status, reason_code=reason)

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(raises, match=match):
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)


@pytest.mark.parametrize("reason", ["owner_lost", "restart_new_turn"])
def test_a_turn_a_restart_continues_as_a_new_turn_is_followed_to_its_end(
        quick_reattach, monkeypatch, reason):
    """A restart ends the turn as interrupted, with whatever reason the
    recovery path wrote, and continues it as a new turn once the next Worker
    settles the restart intent. Until then the relay reads the state again;
    then it follows the new turn to its reply, which ends the command as a
    completed run."""
    answers = iter([("pending", None), ("admitted", "exec2")])
    looked_up: list = []

    def continuation(execution_id):
        looked_up.append(execution_id)
        return next(answers)

    monkeypatch.setattr(worker_turn, "_restart_continuation", continuation)

    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, "interrupted", reason_code=reason)
        again = connection.recv_json()
        assert again["execution_id"] == "exec1"
        _replay(connection, "interrupted", reason_code=reason)
        follow = connection.recv_json()
        assert follow == {"action": "execution.replay", "execution_id": "exec2",
                          "after_sequence": 0, "snapshot_only": True}
        connection.send_frame({"type": "execution.replay", "execution_id": "exec2",
                               "snapshot": {"execution_id": "exec2", "status": "running",
                                            "status_version": 1}})
        _response(connection, SESSION, type="result", msg_id="u1", content="the old turn's")
        _response(connection, SESSION, type="result", msg_id="exec2_user",
                  content="continued reply", assistant_msg_id="exec2_user_reply")

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)
    assert looked_up == ["exec1", "exec1"]
    assert (turn.reply, turn.restarted) == ("continued reply", True)
    assert turn.assistant_msg_id == "exec2_user_reply"


def test_an_interrupted_turn_no_restart_continues_is_reported(quick_reattach, monkeypatch):
    monkeypatch.setattr(worker_turn, "_restart_continuation", lambda execution_id: ("none", None))

    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, "interrupted", reason_code="owner_lost")

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError, match="interrupted on the worker \\(owner_lost\\)"):
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)


def test_a_restart_intent_that_never_settles_is_reported(quick_reattach, monkeypatch):
    monkeypatch.setattr(worker_turn, "_restart_continuation",
                        lambda execution_id: ("pending", None))

    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        _replay(connection, "interrupted", reason_code="owner_lost")
        try:
            while connection.recv_json()["action"] == "execution.replay":
                _replay(connection, "interrupted", reason_code="owner_lost")
        except Exception:  # noqa: BLE001 - the relay gave up and closed
            pass

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError, match="has not continued the turn") as info:
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)
    assert info.value.code == "restart_pending"


def test_the_new_turn_of_a_restart_follows_the_settled_intent(tmp_path, monkeypatch):
    """Read from the store the Worker writes: a new-turn intent is pending
    until it settles; settled as admitted, the continuation is the turn
    admitted under the intent's key; settled otherwise, there is none."""
    from openprogram.runtime.execution import restart
    from openprogram.runtime.execution.execution_tree import (
        RESTART_REQUESTED,
        RESTART_SETTLED,
        continuation_key,
    )
    from openprogram.runtime.execution.model import CapabilitySet
    from openprogram.runtime.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    store.create_revision(revision_id="r1", manifest={"entrypoint": "agent"})

    def admit(**extra):
        return store.admit_execution(
            session_id=SESSION, revision_id="r1", input_ref="i", input_hash=str(extra),
            entrypoint="agent", trusted_actor={"subject": "u"}, config_snapshot_ref="c",
            capabilities=CapabilitySet(pause=False, step=False, steer=False), **extra)

    def intent(execution, mode):
        with store._transaction() as connection:
            restart.record_intent(store, connection, execution, interrupted_at=1.0,
                                  seconds=-1, mode=mode)
        return [e for e in store.list_events(execution.execution_id)
                if e.kind == RESTART_REQUESTED][-1]

    def settle(execution, request, outcome):
        with store._transaction() as connection:
            store._append_event(connection, execution_id=execution.execution_id,
                                kind=RESTART_SETTLED, created_at=2.0,
                                payload={"request_sequence": request.sequence,
                                         "outcome": outcome})

    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    old = admit(execution_id="exec_old")
    assert worker_turn._restart_continuation("exec_old") == ("none", None)
    intent(old, "checkpoint")
    assert worker_turn._restart_continuation("exec_old") == ("none", None)
    request = intent(old, "new_turn")
    assert worker_turn._restart_continuation("exec_old") == ("pending", None)
    new = admit(admission_key=continuation_key("exec_old", request.sequence))
    settle(old, request, "new_turn_admitted")
    assert worker_turn._restart_continuation("exec_old") == ("admitted", new.execution_id)

    other = admit(execution_id="exec_other")
    refused = intent(other, "new_turn")
    settle(other, refused, "noninteractive")
    assert worker_turn._restart_continuation("exec_other") == ("none", None)


def test_unknown_execution_after_a_reattach_is_an_error(quick_reattach):
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command)
            connection.close()
            return
        connection.send_frame({"type": "execution.replay", "execution_id": "exec1", "error": "not_found"})

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnError, match="no longer knows"):
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=lambda: endpoint)


def test_structured_output_is_sent_and_read_from_the_result_frame():
    from openprogram.models.providers.structured_output import normalize_response_format

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}},
              "required": ["ok"], "additionalProperties": False}
    response_format = normalize_response_format(schema)

    def script(connection, command):
        sent = command["response_format"]
        assert sent["type"] == "json_schema"
        assert sent["schema"] == schema
        # The worker accepts exactly what was sent.
        assert normalize_response_format(sent).schema == schema
        _ack(connection, command)
        _response(connection, SESSION, type="result", content='{"ok": true}',
                  structured_output={"ok": True}, structured_output_mode="native")

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", response_format=response_format)
    assert turn.structured_output == {"ok": True}
    assert turn.reply == '{"ok": true}'


def test_no_worker_without_a_reachable_endpoint(monkeypatch):
    from openprogram.host.security import backend_endpoint as be

    def refuse(state_dir=None):
        raise be.OwnerAuthError("no active Web access snapshot")

    monkeypatch.setattr(be, "resolve_backend_endpoint", refuse)
    monkeypatch.delenv(worker_turn.IN_PROCESS_ENV, raising=False)
    assert resolve_worker() is None


def test_in_process_env_skips_the_worker(monkeypatch):
    from openprogram.host.security import backend_endpoint as be

    monkeypatch.setattr(
        be, "resolve_backend_endpoint",
        lambda state_dir=None: BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN),
    )
    monkeypatch.setenv(worker_turn.IN_PROCESS_ENV, "1")
    assert resolve_worker() is None
    monkeypatch.delenv(worker_turn.IN_PROCESS_ENV)
    assert resolve_worker() is not None


def test_structured_output_error_maps_codes_to_exit_classes():
    from openprogram.models.providers.structured_output import (
        StructuredOutputError,
        StructuredOutputSchemaError,
        StructuredOutputUnsupportedError,
    )
    schema = structured_output_error(WorkerTurnError("bad", code="invalid_schema"))
    unsupported = structured_output_error(WorkerTurnError("no", code="unsupported"))
    failed = structured_output_error(
        WorkerTurnError("no", code="validation_failed", issues=[{"path": "$"}]))
    assert type(schema) is StructuredOutputSchemaError
    assert type(unsupported) is StructuredOutputUnsupportedError
    assert type(failed) is StructuredOutputError
    assert failed.code == "validation_failed" and failed.issues == [{"path": "$"}]


def test_result_frame_names_structured_output_only_for_a_structured_turn():
    from openprogram.runtime.conversation.turn_runtime import _structured_output_fields

    plain = types.SimpleNamespace(structured_output_mode=None, structured_output=None)
    structured = types.SimpleNamespace(structured_output_mode="tool",
                                       structured_output={"ok": True})
    assert _structured_output_fields(plain) == {}
    assert _structured_output_fields(structured) == {
        "structured_output": {"ok": True}, "structured_output_mode": "tool"}


# --- the CLI entry point prefers the worker and falls back in-process ---


def _no_runtime():
    raise AssertionError("the in-process runtime must not be initialised")


def _resumable(monkeypatch):
    """``--resume`` names a conversation that exists (``print_session``)."""
    from openprogram.interfaces.cli import print_session

    monkeypatch.setattr(print_session, "_exists", lambda session_id: True)


def test_print_turn_prefers_the_worker(monkeypatch, capsys):
    from openprogram.interfaces.cli import chat as chat_module

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    calls: list[tuple] = []

    def fake_run(ep, session_id, text, *, response_format=None, **_kw):
        calls.append((ep, session_id, text, response_format))
        return worker_turn.WorkerTurn(reply="from the worker", session_id=session_id)

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)
    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fake_run)
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)

    _resumable(monkeypatch)
    chat_module.run_cli_chat(oneshot="ping", resume="local_abc", tui=False)

    assert capsys.readouterr().out == "from the worker\n"
    assert calls == [(endpoint, "local_abc", "ping", None)]


def test_print_turn_prints_structured_output_as_one_json_line(monkeypatch, tmp_path, capsys):
    # A new conversation is bound to the directory the command runs in.
    monkeypatch.chdir(tmp_path)
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.models.providers.structured_output import normalize_response_format

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)
    monkeypatch.setattr(
        worker_turn, "run_turn_via_worker",
        lambda *a, **k: worker_turn.WorkerTurn(
            reply='{"ok": true}', structured_output={"ok": True, "n": "é"}),
    )
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)

    chat_module.run_cli_chat(
        oneshot="ping", tui=False,
        response_format=normalize_response_format({"type": "object"}),
    )
    assert capsys.readouterr().out == '{"ok":true,"n":"é"}\n'


def test_print_turn_worker_error_exits_nonzero(monkeypatch, tmp_path, capsys):
    # A new conversation is bound to the directory the command runs in.
    monkeypatch.chdir(tmp_path)
    from openprogram.interfaces.cli import chat as chat_module

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)

    def fail(*a, **k):
        raise WorkerTurnError("a turn is already running", code="session_busy")

    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fail)
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)

    usage = tmp_path / "usage.json"
    with pytest.raises(SystemExit) as info:
        chat_module.run_cli_chat(oneshot="ping", tui=False, usage_file=str(usage))
    assert info.value.code == 1
    assert capsys.readouterr().err.strip() == "[error] a turn is already running"
    summary = json.loads(usage.read_text())
    # Refused before the ack named an execution: the turn never started.
    assert (summary["ended"], summary["phase"]) == ("failed", "not_started")


def test_print_turn_structured_failure_raises_the_cli_error(monkeypatch, tmp_path):
    # A new conversation is bound to the directory the command runs in.
    monkeypatch.chdir(tmp_path)
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.models.providers.structured_output import (
        StructuredOutputSchemaError,
        normalize_response_format,
    )

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)

    def fail(*a, **k):
        raise WorkerTurnError("Structured output request is invalid", code="invalid_schema")

    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fail)
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)

    with pytest.raises(StructuredOutputSchemaError):
        chat_module.run_cli_chat(
            oneshot="ping", tui=False,
            response_format=normalize_response_format({"type": "object"}),
        )


def test_print_turn_falls_back_in_process_without_a_worker(monkeypatch, capsys):
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.interfaces.cli import print_turn
    from openprogram.runtime.agent_registry import builtin_agents

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: None)
    agent = types.SimpleNamespace(id="main")
    monkeypatch.setattr(builtin_agents, "ensure_default_agent", lambda: agent)
    monkeypatch.setattr(print_turn, "chosen_model", lambda agent_id, model: "prov/m")
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)
    calls: list[tuple] = []

    def fake_run(session_id, text, *, agent_id, model=None, response_format=None, run, **_kw):
        calls.append((agent_id, session_id, text, response_format))
        run.ended = print_turn.ENDED_COMPLETED
        run.reply = "in-process reply"
        return run

    monkeypatch.setattr(print_turn, "run_in_process", fake_run)

    _resumable(monkeypatch)
    chat_module.run_cli_chat(oneshot="ping", resume="local_abc", tui=False)

    assert capsys.readouterr().out == "in-process reply\n"
    assert calls == [("main", "local_abc", "ping", None)]


def test_print_turn_sends_model_and_working_directory_to_the_worker(monkeypatch, tmp_path):
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.interfaces.cli import print_turn
    from openprogram.runtime.agent_registry import manager

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    seen: dict = {}

    def fake_run(ep, session_id, text, **kwargs):
        seen.update(kwargs)
        return worker_turn.WorkerTurn(reply="ok", session_id=session_id, tool_calls=2)

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)
    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fake_run)
    monkeypatch.setattr(print_turn, "use_model", lambda model: True)
    monkeypatch.setattr(manager, "get_default", lambda: types.SimpleNamespace(id="main"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    usage = tmp_path / "usage.json"

    chat_module.run_cli_chat(oneshot="ping", tui=False, model="prov/m",
                             usage_file=str(usage))

    from openprogram.state.store.project.project_store import get_project
    project = get_project(seen["project_id"])
    assert project is not None and project.path == str(tmp_path.resolve())
    assert seen["model"] == "prov/m"
    # No one answers a --print run: the worker is told so, terminal or not.
    assert seen["non_interactive"] is True
    summary = json.loads(usage.read_text(encoding="utf-8"))
    assert summary["ended"] == "completed"
    assert summary["tool_calls"] == 2
    assert summary["model"] == "prov/m"


def test_model_and_project_go_in_the_chat_command():
    def script(connection, command):
        assert command["agent_id"] == "main"
        # The model is this turn's own, never the conversation's.
        assert command["turn_model"] == "prov/m-1" and "agent_overrides" not in command
        assert command["project_id"] == "proj1"
        _ack(connection, command)
        _response(connection, SESSION, type="result", content="done", msg_id="u1",
                  blocks=[{"type": "text"}, {"type": "tool"}, {"type": "tool"}])

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", agent_id="main",
                                   model="prov/m-1", project_id="proj1")
    assert turn.reply == "done"
    assert turn.tool_calls == 2


@pytest.mark.parametrize("non_interactive", [True, False])
def test_a_turn_no_one_answers_says_so_in_the_chat_command(non_interactive):
    def script(connection, command):
        if non_interactive:
            assert command["interaction"] == "non-interactive"
        else:
            assert "interaction" not in command
        _ack(connection, command)
        _response(connection, SESSION, type="result", content="done", msg_id="u1")

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", non_interactive=non_interactive)
    assert turn.reply == "done"


@pytest.mark.parametrize("interaction, expected", [
    ("non-interactive", "non-interactive"),
    (None, "interactive"),
    # A client can only narrow the owner's authority.
    ("spawn", "interactive"),
])
def test_the_worker_runs_a_non_interactive_chat_command_as_one_no_one_answers(
        monkeypatch, interaction, expected):
    from openprogram.interfaces.server.ws_actions.chat import turn as chat_turn
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation import surface_context
    from openprogram.runtime.permissions.policy import can_approve, nobody_answers

    monkeypatch.setattr(surface_context, "capture", lambda ref, ws, session_id=None: None)
    server = types.SimpleNamespace(
        _parse_chat_input=lambda text: {"action": "chat", "raw": text},
        _release_run_reservation=lambda session_id, msg_id: None,
    )
    cmd = {"action": "chat", "text": "go", "session_id": SESSION}
    if interaction is not None:
        cmd["interaction"] = interaction
    req = _ChatRequest(
        cmd=cmd, text="go", session_id=SESSION, agent_id=None, thinking_effort=None,
        tools_flag=None, web_search_flag=None, permission_mode=None, sandbox_flag=None,
        surface_ref=None, service_tier=None, response_format=None, attachments=None,
    )

    _parsed, authority, _surface, _release = chat_turn._capture_turn_inputs(
        server, None, req, "m1")

    assert authority["interaction"] == expected
    assert authority["authority_tier"] == "owner"
    request = TurnRequest(session_id=SESSION, user_text="go", agent_id="main",
                          source="web", **authority)
    assert nobody_answers(request) is (expected == "non-interactive")
    assert can_approve(request) is (expected == "interactive")


# --- Ctrl-C or SIGTERM stops the turn on the worker ---


def _interrupt_on_first_text(_text):
    raise KeyboardInterrupt


def _updated(connection, status, version):
    connection.send_frame({"type": "execution.updated", "execution": {
        "execution_id": "exec1", "status": status, "status_version": version,
    }})


def _command_update(connection, command, status, **command_fields):
    connection.send_frame({"type": "execution.command.updated", "command": {
        "command_id": command["command_id"], "execution_id": "exec1",
        "status": status, **command_fields,
    }})


def _stop_command(command):
    """The Web composer's stop, field for field (use-chat-submit.ts)."""
    assert set(command) == {"type", "action", "command_id", "execution_id",
                            "expected_version", "payload"}
    assert command["type"] == "execution.command"
    assert command["action"] == "execution.cancel"
    assert command["execution_id"] == "exec1"
    assert command["payload"] == {}
    assert isinstance(command["command_id"], str) and command["command_id"]
    return command


@pytest.fixture
def quick_stop(monkeypatch):
    # The relay waits as the in-process run does: on the shared clock.
    from openprogram.interfaces.cli import print_stop

    monkeypatch.setattr(print_stop, "CANCEL_GRACE_S", 0.4)
    monkeypatch.setattr(print_stop, "SECOND_SIGNAL_GRACE_S", 0.1)


def test_ctrl_c_sends_the_stop_command_at_the_current_version_and_waits_for_the_end():
    """The ack names version 1; the execution has since moved to 3. The
    stop goes out as the Web UI's command at 3, and the relay returns only
    once the worker says the execution is cancelled."""
    def script(connection, command):
        _ack(connection, command, status_version=1)
        _updated(connection, "queued", 2)
        _updated(connection, "running", 3)
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "partial"})
        stop = _stop_command(connection.recv_json())
        assert stop["expected_version"] == 3
        _command_update(connection, stop, "accepted")
        _updated(connection, "cancelling", 4)
        _response(connection, SESSION, type="result", msg_id="u1", content="",
                  blocks=[{"type": "text", "text": "partial"}])
        _updated(connection, "cancelled", 5)

    with fake_worker(script) as (endpoint, seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text)
    assert info.value.confirmed is True
    assert info.value.status == "cancelled"
    assert len([c for c in seen["commands"] if c.get("action") == "execution.cancel"]) == 1


def test_a_stop_refused_for_a_stale_version_is_sent_again_at_the_version_named():
    def script(connection, command):
        _ack(connection, command, status_version=1)
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "partial"})
        first = _stop_command(connection.recv_json())
        assert first["expected_version"] == 1
        _command_update(connection, first, "rejected", rejection_code="stale_version",
                        latest_snapshot={"execution_id": "exec1", "status": "running",
                                         "status_version": 3})
        second = _stop_command(connection.recv_json())
        assert second["expected_version"] == 3
        assert second["command_id"] == first["command_id"]
        _command_update(connection, second, "accepted")
        _updated(connection, "cancelling", 4)
        _updated(connection, "cancelled", 5)

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text)
    assert (info.value.confirmed, info.value.status) == (True, "cancelled")


def test_a_stop_the_worker_does_not_carry_out_in_time_is_not_confirmed(quick_stop):
    def script(connection, command):
        _ack(connection, command, status_version=3)
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "partial"})
        stop = _stop_command(connection.recv_json())
        _command_update(connection, stop, "accepted")
        _updated(connection, "cancelling", 4)
        try:
            connection.recv_json()  # until the relay leaves
        except Exception:
            pass

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text)
    assert info.value.confirmed is False
    assert "had not ended the turn" in info.value.detail


def test_a_stop_the_worker_refuses_is_not_confirmed():
    def script(connection, command):
        _ack(connection, command, status_version=3)
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "partial"})
        stop = _stop_command(connection.recv_json())
        _command_update(connection, stop, "rejected", rejection_code="unauthorized")

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text)
    assert info.value.confirmed is False
    assert info.value.detail == "the worker refused the stop (unauthorized)"


def test_a_turn_stopped_elsewhere_is_cancelled_not_an_empty_reply():
    """The Web UI's stop button ends the turn with a result frame whose
    content is empty (the partial is in its blocks); the turn did not
    complete, so ``--print`` must not print an empty reply and exit 0."""
    def script(connection, command):
        _ack(connection, command, status_version=1)
        _updated(connection, "running", 3)
        _updated(connection, "cancelling", 4)
        _response(connection, SESSION, type="result", msg_id="u1", content="",
                  blocks=[{"type": "text", "text": "partial"}])
        _updated(connection, "cancelled", 5)

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnCancelled):
            run_turn_via_worker(endpoint, SESSION, "go")


@pytest.mark.parametrize("stop, said", [
    (worker_turn.WorkerTurnStopped(True, "cancelled"), "[cancelled]"),
    (worker_turn.WorkerTurnStopped(False, "cancelling", "the worker had not ended the turn 30 s after the stop"),
     "[cancelled] the worker had not ended the turn 30 s after the stop; "
     "the turn may still be running on the worker, and the Web UI shows it"),
])
def test_print_says_whether_the_worker_stopped_the_turn(monkeypatch, capsys, stop, said):
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.interfaces.cli import print_turn

    monkeypatch.setattr(print_turn, "hold_signals", lambda: threading.Event())
    given: dict = {}

    def stopped(*_a, **kw):
        given.update(kw)
        raise stop

    monkeypatch.setattr(worker_turn, "run_turn_via_worker", stopped)
    run = print_turn.PrintRun(session_id=SESSION)
    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)
    with pytest.raises(SystemExit) as info:
        chat_module._print_oneshot_via_worker(endpoint, SESSION, "go", run=run)
    assert info.value.code == 130
    assert capsys.readouterr().err.strip() == said
    assert run.ended == print_turn.ENDED_CANCELLED
    assert callable(given["on_stop"])
    assert (run.error is None) is stop.confirmed


@pytest.fixture
def interrupt_first_recv(monkeypatch):
    """Ctrl-C lands in the relay's first read, before the ack is read."""
    opened = worker_turn._open

    def open_interrupting(endpoint):
        ws = opened(endpoint)
        recv = ws.recv
        state = {"fired": False}

        def interrupting_recv(*args, **kwargs):
            if not state["fired"]:
                state["fired"] = True
                raise KeyboardInterrupt
            return recv(*args, **kwargs)

        ws.recv = interrupting_recv
        return ws

    monkeypatch.setattr(worker_turn, "_open", open_interrupting)


def test_a_stop_before_an_ack_without_a_version_is_sent_when_the_ack_names_the_turn(
        interrupt_first_recv, quick_stop):
    """A function run's ack names the execution but no version; a stop names
    its execution, so the cancel goes out at once, without reading a version."""
    def script(connection, command):
        connection.send_frame({"type": "chat_ack", "data": {
            "session_id": command["session_id"], "msg_id": "u1",
            "execution_id": "exec1", "function_run": True,
            "client_request_id": command.get("client_request_id"),
        }})
        stop = _stop_command(connection.recv_json())
        assert stop["execution_id"] == "exec1"
        _command_update(connection, stop, "accepted")
        _updated(connection, "cancelled", 5)

    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "/some_function x")
    assert (info.value.confirmed, info.value.status) == (True, "cancelled")


def test_a_stop_before_a_turn_the_worker_refuses_ends_at_the_refusal(
        interrupt_first_recv, monkeypatch):
    """The worker refuses most turns with an error chat_response before any
    ack; nothing runs, so the stop is confirmed at once."""
    from openprogram.interfaces.cli import print_stop

    monkeypatch.setattr(print_stop, "CANCEL_GRACE_S", 5.0)

    def script(connection, command):
        _response(connection, command["session_id"], type="error", msg_id="x",
                  code="run_active", content="a run is currently active",
                  client_request_id=command.get("client_request_id"))
        try:
            connection.recv_json()  # until the relay leaves
        except Exception:
            pass

    import time
    started = time.monotonic()
    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go")
    assert (info.value.confirmed, info.value.status) == (True, "refused")
    assert time.monotonic() - started < 2.0


def test_a_stop_whose_socket_closes_reaches_the_worker_that_comes_back(quick_reattach):
    """A worker restart closes the socket after the cancel went out; the
    execution resumed running on the new worker, so the stop reconnects,
    reads its state and sends the cancel there."""
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command, status_version=3)
            _response(connection, SESSION, type="stream_event", msg_id="u1",
                      event={"type": "text", "text": "partial"})
            _stop_command(connection.recv_json())
            connection.close()
            return
        assert command["action"] == "execution.replay"
        _replay(connection, "running", status_version=6)
        stop = _stop_command(connection.recv_json())
        assert stop["expected_version"] == 6
        _command_update(connection, stop, "accepted")
        _updated(connection, "cancelled", 8)

    with fake_worker(script) as (endpoint, seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text,
                                reconnect=lambda: endpoint)
    assert (info.value.confirmed, info.value.status) == (True, "cancelled")
    assert len(seen["headers"]) == 2


def test_a_stop_during_a_reattach_reaches_the_worker_that_comes_back(quick_reattach):
    """Ctrl-C while the relay looks for the restarted worker: the stop goes
    on looking, and cancels the turn there."""
    def script(connection, command):
        if command["action"] == "chat":
            _ack(connection, command, status_version=3)
            connection.close()
            return
        assert command["action"] == "execution.replay"
        _replay(connection, "running", status_version=6)
        stop = _stop_command(connection.recv_json())
        assert stop["expected_version"] == 6
        _command_update(connection, stop, "accepted")
        _updated(connection, "cancelled", 8)

    with fake_worker(script) as (endpoint, seen):
        calls = {"n": 0}

        def reconnect():
            calls["n"] += 1
            if calls["n"] == 1:
                raise KeyboardInterrupt  # the signal lands while looking
            return endpoint

        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", reconnect=reconnect)
    assert (info.value.confirmed, info.value.status) == (True, "cancelled")
    assert len(seen["headers"]) == 2


def test_the_execution_the_ack_names_reaches_the_caller_however_the_turn_ends():
    """The usage and transcript files name the run's execution, as the
    in-process run's do: the relay hands it on as soon as the ack names it."""
    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="cancelled", content="stopped")

    named: list[str] = []
    with fake_worker(script) as (endpoint, _seen):
        with pytest.raises(WorkerTurnCancelled):
            run_turn_via_worker(endpoint, SESSION, "hi", on_execution=named.append)
    assert named == ["exec1"]


def test_print_turn_usage_names_the_worker_execution(monkeypatch, tmp_path, capsys):
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.interfaces.cli import print_turn

    endpoint = BackendEndpoint(origin="http://127.0.0.1:1", token=TOKEN)

    def fake_run(_ep, session_id, _text, *, on_execution=None, **_kw):
        on_execution("exec_worker")
        return worker_turn.WorkerTurn(reply="done", session_id=session_id, tool_calls=0,
                                      restarted=True)

    monkeypatch.setattr(worker_turn, "resolve_worker", lambda: endpoint)
    monkeypatch.setattr(worker_turn, "run_turn_via_worker", fake_run)
    monkeypatch.setattr(chat_module, "_get_chat_runtime", _no_runtime)
    monkeypatch.setattr(print_turn, "_ledger_rows", lambda run: [])
    usage = tmp_path / "usage.json"

    _resumable(monkeypatch)
    chat_module.run_cli_chat(oneshot="ping", resume="local_abc", tui=False, usage_file=str(usage))

    assert capsys.readouterr().out == "done\n"
    summary = json.loads(usage.read_text())
    assert (summary["ended"], summary["execution_id"]) == ("completed", "exec_worker")
    # The reply came from the turn a Worker restart continued it as.
    assert summary["restarted"] is True


def test_the_state_read_is_an_action_the_worker_serves():
    """The relay reads the execution's state with the action the Worker
    registers; one it does not know is answered with ``unknown_action``."""
    from openprogram.interfaces.server.ws_actions import runtime as ws_runtime

    sent: list[dict] = []
    relay = worker_turn._Relay(SESSION, "r1", None, None)
    relay.execution_id = "exec1"
    relay.query_state(types.SimpleNamespace(send=lambda raw: sent.append(json.loads(raw))))
    assert sent[0]["action"] in ws_runtime.ACTIONS


# --- A Worker that announces what its stop does ---

CURRENT_WORKER = ("cancel_ignores_version", "cancel_by_request")


def test_a_stop_before_the_ack_names_the_send_on_a_worker_that_takes_one(
        interrupt_first_recv, quick_stop):
    """Ctrl-C before the ack: the stop goes out at once naming this send
    (session and client_request_id); the worker keeps it for the send's
    admission, and the turn ends cancelled with no second stop."""
    def script(connection, command):
        stop = connection.recv_json()
        assert set(stop) == {"type", "action", "command_id", "session_id",
                             "client_request_id", "payload"}
        assert stop["action"] == "execution.cancel"
        assert stop["session_id"] == SESSION
        assert stop["client_request_id"] == command["client_request_id"]
        connection.send_frame({"type": "execution.command.updated", "command": {
            "command_id": stop["command_id"], "execution_id": "", "status": "marked",
        }})
        _ack(connection, command, status_version=1)
        _updated(connection, "cancelled", 2)

    with fake_worker(script, CURRENT_WORKER) as (endpoint, seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go")
    assert (info.value.confirmed, info.value.status) == (True, "cancelled")
    assert len([c for c in seen["commands"] if c.get("action") == "execution.cancel"]) == 1


def test_a_worker_that_ignores_versions_is_not_sent_the_stop_again():
    """A stale refusal cannot come from such a worker; should one come, the
    stop is reported refused instead of being resent."""
    def script(connection, command):
        _ack(connection, command, status_version=1)
        _response(connection, SESSION, type="stream_event", msg_id="u1",
                  event={"type": "text", "text": "partial"})
        stop = _stop_command(connection.recv_json())
        _command_update(connection, stop, "rejected", rejection_code="stale_version",
                        latest_snapshot={"execution_id": "exec1", "status": "running",
                                         "status_version": 3})
        try:
            connection.recv_json()  # until the relay leaves
        except Exception:
            pass

    with fake_worker(script, CURRENT_WORKER) as (endpoint, seen):
        with pytest.raises(worker_turn.WorkerTurnStopped) as info:
            run_turn_via_worker(endpoint, SESSION, "go", on_text=_interrupt_on_first_text)
    assert info.value.confirmed is False
    assert info.value.detail == "the worker refused the stop (stale_version)"
    assert len([c for c in seen["commands"] if c.get("action") == "execution.cancel"]) == 1


# -- who is on each side ------------------------------------------------------

def test_the_chat_command_says_who_sent_it():
    from openprogram.interfaces.server.handshake import client_identity

    def script(connection, command):
        assert client_identity(command)["name"] == worker_turn.CLIENT_NAME
        _ack(connection, command)
        _response(connection, SESSION, type="result", content="ok")

    with fake_worker(script) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go")
    # An older Worker says nothing about itself.
    assert (turn.worker_version, turn.worker_capabilities) == (None, frozenset())


def test_the_worker_s_identity_reaches_the_turn_from_connect_and_from_the_ack():
    seen_servers = []

    def script(connection, command):
        _ack(connection, command, server={"name": "openprogram-worker", "version": "9.9.9",
                                          "protocol": 3, "capabilities": ["structured_output"]})
        _response(connection, SESSION, type="result", content="ok")

    server = {"name": "openprogram-worker", "version": "9.9.9", "protocol": 3,
              "capabilities": ["cancel_by_request"]}
    with fake_worker(script, identity=server) as (endpoint, _seen):
        turn = run_turn_via_worker(endpoint, SESSION, "go", on_server=seen_servers.append)
    assert turn.worker_version == "9.9.9"
    assert {"structured_output", "cancel_by_request"} <= turn.worker_capabilities
    assert [s["version"] for s in seen_servers] == ["9.9.9", "9.9.9"]


def test_a_worker_that_has_the_field_and_sends_no_structured_output_fails(monkeypatch, capsys):
    """A Worker that names ``structured_output`` puts the value in its field:
    a reply without it is a failure, not JSON to fish out of the text."""
    from openprogram.interfaces.cli import chat as chat_module
    from openprogram.interfaces.cli import print_turn
    from openprogram.models.providers.structured_output import (
        StructuredOutputError,
        normalize_response_format,
    )

    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    def script(connection, command):
        _ack(connection, command)
        _response(connection, SESSION, type="result", content='{"ok": true}')

    run = print_turn.PrintRun(session_id=SESSION)
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    with fake_worker(script, identity={"version": "9.9.9", "capabilities": ["structured_output"]}) as (
            endpoint, _seen):
        with pytest.raises(StructuredOutputError):
            chat_module._print_oneshot_via_worker(
                endpoint, SESSION, "go", response_format=normalize_response_format(schema),
                run=run)
    assert run.worker_version == "9.9.9"


def test_the_server_identity_names_every_capability_a_client_may_rely_on():
    from openprogram.interfaces.server import handshake
    from openprogram.runtime.execution.control.submission import CONTROL_CAPABILITIES

    identity = handshake.server_identity()
    assert identity["name"] == handshake.SERVER_NAME
    assert identity["protocol"] == handshake.HISTORY_PROTOCOL
    assert set(identity["capabilities"]) == {*CONTROL_CAPABILITIES, *handshake.CHAT_CAPABILITIES}
    assert handshake.client_identity({"client": {"name": "x" * 65}}) is None
    assert handshake.client_identity({"client": {"name": "tui", "version": 3}}) == {"name": "tui"}
    assert handshake.client_identity({}) is None
