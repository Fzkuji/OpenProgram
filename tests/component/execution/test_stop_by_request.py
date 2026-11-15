"""A stop can name the send that starts a turn, before the execution is known.

A client learns its turn's execution id from ``chat_ack``. A stop sent
before that names the send instead: its session and ``client_request_id``.
One store transaction decides: when the send admitted an execution, that
execution is stopped like any other; before that, a stop mark is written to
the session's log, the send's admission takes it, and the turn ends as
stopped when it activates, before any owner is leased.
"""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest


ACTOR = {
    "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
    "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
    "interaction": "interactive",
}
SESSION = "session-1"


def _world(tmp_path, monkeypatch):
    from openprogram.runtime.execution import (
        AttemptStore,
        DriverRegistry,
        ExecutionStore,
        RuntimeControlService,
    )

    store = ExecutionStore(tmp_path / "executions.db")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.runner_for_execution_store", lambda _store: None)
    return store, service


def _admit(store, request_id: str | None, *, execution_id: str = "turn"):
    from openprogram.runtime.execution import CapabilitySet

    revision = store.create_revision(manifest={"entrypoint": "chat"})
    return store.admit_execution(
        execution_id=execution_id, session_id=SESSION,
        revision_id=revision.revision_id, input_ref="blob:input",
        input_hash="input-hash", entrypoint="chat", trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config", user_message_id="user", assistant_message_id="assistant",
        capabilities=CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",)),
        client_request_id=request_id,
    )


def _stop(request_id: str = "req-1", command_id: str = "stop-1", **extra) -> dict:
    return {
        "type": "execution.command", "action": "execution.cancel",
        "command_id": command_id, "session_id": SESSION,
        "client_request_id": request_id, "payload": {}, **extra,
    }


def _submit(cmd: dict, *, bound_session: str | None = SESSION):
    from openprogram.runtime.execution.control.submission import submit_execution_control

    return asyncio.run(submit_execution_control(
        cmd, "cancel", actor=ACTOR, bound_session=bound_session, surface="ws",
    ))


def _activate(store, service, execution_id: str) -> bool:
    """The first step of ``CanonicalAgentEntry.activate``: a taken mark ends it."""
    from openprogram.runtime.conversation.production_driver.shared import CanonicalAgentEntry

    entry = SimpleNamespace(store=store, control=service)
    return asyncio.run(CanonicalAgentEntry._stopped_by_mark(entry, execution_id))


def test_a_stop_before_admission_is_kept_and_the_admission_ends_stopped(tmp_path, monkeypatch) -> None:
    store, service = _world(tmp_path, monkeypatch)

    command, placeholder = _submit(_stop())

    assert command["status"] == "marked"
    assert command["execution_id"] == ""
    assert (command["session_id"], command["client_request_id"]) == (SESSION, "req-1")
    assert placeholder == {"execution_id": "", "status_version": None, "session_id": SESSION}

    admitted = _admit(store, "req-1")
    mark = store.stop_mark_for(admitted.execution_id)
    assert mark is not None and mark["command_id"] == "stop-1"
    assert json.loads(mark["actor_json"])["surface"] == "ws"

    assert _activate(store, service, admitted.execution_id) is True
    ended = store.get_execution(admitted.execution_id)
    assert ended.status.value == "cancelled"
    assert ended.reason_code == "cancel.user"
    applied = store.get_command("stop-1")
    assert applied.execution_id == admitted.execution_id
    assert applied.status.value == "applied"
    assert store.stop_mark_for(admitted.execution_id) is None


def test_a_send_with_no_stop_activates_as_usual(tmp_path, monkeypatch) -> None:
    store, service = _world(tmp_path, monkeypatch)
    admitted = _admit(store, "req-1")

    assert _activate(store, service, admitted.execution_id) is False
    assert store.get_execution(admitted.execution_id).status.value == "queued"


def test_a_stop_after_admission_stops_that_execution(tmp_path, monkeypatch) -> None:
    store, _service = _world(tmp_path, monkeypatch)
    admitted = _admit(store, "req-1")

    command, latest = _submit(_stop())

    assert not isinstance(command, dict), command
    assert command.execution_id == admitted.execution_id
    assert command.command_id == "stop-1"
    assert latest.status.value == "cancelled"
    assert store.stop_mark_for(admitted.execution_id) is None


def test_a_repeated_stop_before_admission_keeps_the_first_mark(tmp_path, monkeypatch) -> None:
    store, service = _world(tmp_path, monkeypatch)
    _submit(_stop(command_id="stop-1"))
    command, _ = _submit(_stop(command_id="stop-2"))
    assert command["status"] == "marked"

    admitted = _admit(store, "req-1")
    _activate(store, service, admitted.execution_id)

    assert store.get_command("stop-1") is not None
    assert store.get_command("stop-2") is None


def test_a_mark_outlives_a_restart(tmp_path, monkeypatch) -> None:
    """The mark is a line in the session's log: a new process reading the
    logs (a Worker restart between the stop and the send) still takes it."""
    store, _service = _world(tmp_path, monkeypatch)
    _submit(_stop())

    from openprogram.runtime.execution import AttemptStore, DriverRegistry, RuntimeControlService

    restarted = store.fresh_view()
    service = RuntimeControlService(restarted, AttemptStore(restarted), DriverRegistry())
    admitted = _admit(restarted, "req-1")

    assert _activate(restarted, service, admitted.execution_id) is True
    assert restarted.get_execution(admitted.execution_id).status.value == "cancelled"


def test_a_lapsed_mark_does_not_stop_a_later_send(tmp_path, monkeypatch) -> None:
    store, service = _world(tmp_path, monkeypatch)
    store.stop_by_request(
        session_id=SESSION, client_request_id="req-1", command_id="stop-old",
        actor=ACTOR, reason_code="cancel.user", now=1.0,
    )

    admitted = _admit(store, "req-1")

    assert store.stop_mark_for(admitted.execution_id) is None
    assert _activate(store, service, admitted.execution_id) is False


def test_one_send_admits_one_execution(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.execution.store import ExecutionConflict

    store, _service = _world(tmp_path, monkeypatch)
    _admit(store, "req-1", execution_id="turn-1")
    _admit(store, None, execution_id="turn-2")
    _admit(store, None, execution_id="turn-3")

    with pytest.raises(ExecutionConflict) as info:
        _admit(store, "req-1", execution_id="turn-4")
    assert info.value.code == "request_admitted"
    assert store.execution_for_request(SESSION, "req-1") == "turn-1"
    assert store.get_execution("turn-4") is None


@pytest.mark.parametrize("cmd, code", [
    (_stop(request_id="x" * 129), "invalid_command"),
    (_stop(extra="field"), "invalid_command"),
    (_stop(payload={"reason": "no"}), "invalid_payload"),
    ({**_stop(), "action": "execution.pause"}, "invalid_command"),
])
def test_a_malformed_stop_by_request_is_refused(tmp_path, monkeypatch, cmd, code) -> None:
    store, _service = _world(tmp_path, monkeypatch)

    command, _ = _submit(cmd)

    assert command["status"] == "rejected" and command["rejection_code"] == code
    assert store.execution_for_request(SESSION, "req-1") is None


def test_a_stop_by_request_for_another_session_is_not_found(tmp_path, monkeypatch) -> None:
    store, _service = _world(tmp_path, monkeypatch)
    _admit(store, "req-1")

    command, _ = _submit(_stop(), bound_session="session-2")

    assert command["status"] == "rejected" and command["rejection_code"] == "not_found"
    assert store.get_command("stop-1") is None


def test_the_worker_announces_what_its_stop_does() -> None:
    from openprogram.runtime.execution.control.submission import CONTROL_CAPABILITIES

    assert set(CONTROL_CAPABILITIES) == {"cancel_ignores_version", "cancel_by_request"}


def test_a_function_run_and_its_retry_are_admitted_under_the_request_id() -> None:
    """A REST function run (and the Retry of one) answers with its execution
    id, and is admitted under the id its caller made, so a stop sent before
    that answer arrives names it (Codex returns the turn id at once; the
    request id covers the time until then)."""
    import inspect

    from openprogram.interfaces.server.routes import chat
    from openprogram.interfaces.server.ws_actions.chat import retry

    run = inspect.getsource(chat.run_agent_method_call)
    admit = run[run.index("_adapter.admit_payload("):]
    admit = admit[:admit.index("except BaseException")]
    assert "client_request_id=client_request_id" in admit
    assert '"execution_id": execution_id' in run
    assert 'options["client_request_id"] = request_id' in inspect.getsource(retry.handle_retry_function)
