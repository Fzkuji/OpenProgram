"""A public stop names its execution, not a version.

A chat turn's version moves twice in its first tenth of a second (admitted,
leased, running), so the version a page or a terminal knows when the user
presses Stop is often already old. A stop's intent does not depend on it:
the store takes the cancel at the execution's current version in one
transaction and answers by state: a new stop, the stop already in force
(delivered to the owner again), or ``already_terminal``.
"""
from __future__ import annotations

import asyncio

import pytest


ACTOR = {
    "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
    "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
    "interaction": "interactive",
}


def _turn(tmp_path, monkeypatch, *, start: bool):
    from openprogram.runtime.execution import (
        AttemptStore,
        CapabilitySet,
        DriverRegistry,
        ExecutionStore,
        RuntimeControlService,
    )

    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    admitted = store.admit_execution(
        execution_id="turn", run_id="run", session_id="session-1",
        revision_id=revision.revision_id, input_ref="blob:input",
        input_hash="input-hash", entrypoint="chat", trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config", user_message_id="user", assistant_message_id="assistant",
        capabilities=CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    if start:
        lease, reserved = attempts.lease(admitted.execution_id, expected_version=admitted.status_version, owner_id="owner")
        attempts.activate(lease.attempt_id, generation=lease.generation, expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.runner_for_execution_store", lambda _store: None)
    return store, admitted


def _cancel(execution_id: str, version: int, command_id: str = "stop-1") -> dict:
    return {
        "type": "execution.command", "action": "execution.cancel",
        "command_id": command_id, "execution_id": execution_id,
        "expected_version": version, "payload": {},
    }


def _submit(cmd: dict):
    from openprogram.runtime.execution.control.submission import submit_execution_control

    return asyncio.run(submit_execution_control(cmd, "cancel", actor=ACTOR, bound_session="session-1", surface="ws"))


def test_a_stop_sent_with_the_admission_version_stops_the_running_turn(tmp_path, monkeypatch) -> None:
    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    running = store.get_execution(admitted.execution_id)
    assert running.status.value == "running"
    assert running.status_version > admitted.status_version

    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version))

    assert not isinstance(command, dict), command
    assert command.command_id == "stop-1"
    assert command.status.value in {"accepted", "applying", "applied"}
    assert command.expected_version == running.status_version
    assert latest.status.value in {"cancelling", "cancelled"}


def test_a_stop_for_an_ended_turn_answers_already_terminal(tmp_path, monkeypatch) -> None:
    store, admitted = _turn(tmp_path, monkeypatch, start=False)
    _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-first"))
    ended = store.get_execution(admitted.execution_id)
    assert ended.status.value == "cancelled"

    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-late"))

    assert isinstance(command, dict) and command["status"] == "already_terminal"
    assert command["command_id"] == "stop-late"
    assert command["result_version"] == ended.status_version
    assert latest.status.value == "cancelled"
    assert store.get_command("stop-late") is None


@pytest.mark.parametrize("operation", ["pause", "steer"])
def test_other_commands_still_need_the_current_version(tmp_path, monkeypatch, operation) -> None:
    from openprogram.runtime.execution.control.submission import submit_execution_control

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    cmd = {
        **_cancel(admitted.execution_id, admitted.status_version), "action": f"execution.{operation}",
        "payload": {"message": "also this"} if operation == "steer" else {},
    }

    command, _latest = asyncio.run(submit_execution_control(cmd, operation, actor=ACTOR, bound_session="session-1", surface="ws"))

    assert isinstance(command, dict) and command["rejection_code"] == "stale_version"


class _Driver:
    """A driver that records every cancel it is handed."""

    def __init__(self) -> None:
        self.cancels: list[str] = []

    def capabilities(self):
        from openprogram.runtime.execution import CapabilitySet

        return CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",))

    async def request_cancel(self, handle, command_id):
        from openprogram.runtime.execution.driver import DriverAck

        self.cancels.append(command_id)
        return DriverAck(command_id=command_id, attempt_id=handle)


def _bind(store, execution_id: str, driver: _Driver | None = None) -> _Driver:
    import openprogram.runtime.execution as ex
    from openprogram.runtime.execution.driver import DriverBinding

    record = store.get_execution(execution_id)
    driver = driver or _Driver()
    ex.default_control_service().registry.bind(DriverBinding(
        execution_id=execution_id, attempt_id=record.current_attempt_id,
        generation=record.owner_lease["generation"], driver=driver,
        handle=record.current_attempt_id,
    ))
    return driver


def test_a_second_stop_while_cancelling_returns_the_first_without_delivering_it_twice(tmp_path, monkeypatch) -> None:
    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    driver = _bind(store, admitted.execution_id)
    first, cancelling = _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-a"))
    assert not isinstance(first, dict) and cancelling.status.value == "cancelling"
    assert driver.cancels == ["stop-a"]

    # A second Stop (another tab, the Activity panel) still holding an old version.
    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-b"))

    assert not isinstance(command, dict), command
    assert command.command_id == "stop-a"
    assert latest.status.value == "cancelling"
    assert store.get_command("stop-b") is None
    # The owner already took this stop; it is not handed over again.
    assert driver.cancels == ["stop-a"]


def test_a_repeated_internal_stop_of_a_released_owner_is_already_delivered(tmp_path, monkeypatch) -> None:
    """The turn took its stop and let go of its handle, but its ending is
    not written yet. A later stop (a Goal stop, a time limit) is the same
    stop, already delivered, not a driver failure."""
    from openprogram.runtime.execution.run_control.cancellation import cancel_execution

    class _Released(_Driver):
        async def request_cancel(self, handle, command_id):
            if self.cancels:
                raise RuntimeError("stale_handle")
            return await super().request_cancel(handle, command_id)

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    driver = _bind(store, admitted.execution_id, _Released())
    first = cancel_execution(admitted.execution_id)
    assert first["status"] == "cancelling" and driver.cancels == ["execution-cancel:turn"]

    again = cancel_execution(admitted.execution_id)

    assert again["status"] == "cancelling"
    assert again.get("issue_code") == "already_delivered"
    assert driver.cancels == ["execution-cancel:turn"]


def test_a_repeated_internal_stop_after_the_end_is_already_terminal(tmp_path, monkeypatch) -> None:
    """Internal stops share one command id, so a later one (a time limit
    after a Goal stop) replays the stop that ended the turn; it reports
    that it ended nothing."""
    from openprogram.runtime.execution.run_control.cancellation import cancel_execution

    store, admitted = _turn(tmp_path, monkeypatch, start=False)
    first = cancel_execution(admitted.execution_id)
    assert first["status"] == "cancelled" and "issue_code" not in first

    again = cancel_execution(admitted.execution_id)

    assert again["status"] == "cancelled"
    assert again["issue_code"] == "already_terminal"
    assert len(store.list_commands(admitted.execution_id)) == 1


def test_an_internal_stop_of_a_turn_that_ended_otherwise_is_not_cancellable(tmp_path, monkeypatch) -> None:
    """``run_control``'s answer for a run that ended other than by a stop is
    :class:`ExecutionNotCancellable`, on the canonical path as on the old
    one, so a caller that listed it before it ended (a time limit) skips it."""
    import openprogram.runtime.execution as ex
    from openprogram.runtime.execution.run_control import ExecutionNotCancellable, cancel_execution

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    running = store.get_execution(admitted.execution_id)
    ex.AttemptStore(store).finish(
        running.current_attempt_id, generation=running.owner_lease["generation"],
        expected_execution_version=running.status_version,
        target=ex.ExecutionStatus.FAILED, outcome="failed",
    )

    with pytest.raises(ExecutionNotCancellable):
        cancel_execution(admitted.execution_id)
    assert store.list_commands(admitted.execution_id) == []


def test_ten_concurrent_stops_write_one_command(tmp_path, monkeypatch) -> None:
    import threading

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    driver = _bind(store, admitted.execution_id)
    answers: list = []
    barrier = threading.Barrier(10)

    def press(index: int) -> None:
        barrier.wait()
        answers.append(_submit(_cancel(admitted.execution_id, admitted.status_version, f"stop-{index}")))

    threads = [threading.Thread(target=press, args=(index,)) for index in range(10)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    commands = store.list_commands(admitted.execution_id)
    assert len(commands) == 1
    assert {command.command_id for command, _ in answers} == {commands[0].command_id}
    assert len(driver.cancels) >= 1
    assert store.get_execution(admitted.execution_id).status.value == "cancelling"


def test_a_repeated_stop_delivers_a_first_signal_that_was_lost(tmp_path, monkeypatch) -> None:
    """The first delivery did not reach the owner (its driver failed); the
    next stop, from any surface, delivers the stop in force."""

    class _Flaky(_Driver):
        async def request_cancel(self, handle, command_id):
            if not self.cancels:
                self.cancels.append("lost")
                raise RuntimeError("owner busy")
            return await super().request_cancel(handle, command_id)

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    driver = _bind(store, admitted.execution_id, _Flaky())
    first, _ = _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-a"))
    assert first.command_id == "stop-a" and driver.cancels == ["lost"]

    _submit(_cancel(admitted.execution_id, admitted.status_version, "stop-b"))

    assert driver.cancels == ["lost", "stop-a"]


def test_a_stale_stop_for_a_queued_job_is_taken_at_the_current_version(tmp_path, monkeypatch) -> None:
    import openprogram.runtime.execution as ex

    store, admitted = _turn(tmp_path, monkeypatch, start=False)
    # The Job was leased and handed back before it ran: still queued with no
    # attempt, at a version the caller has not seen.
    attempts = ex.AttemptStore(store)
    lease, reserved = attempts.lease(admitted.execution_id, expected_version=admitted.status_version, owner_id="owner")
    attempts.finish(lease.attempt_id, generation=lease.generation, expected_execution_version=reserved.status_version, target=ex.ExecutionStatus.QUEUED, outcome="released")
    service = ex.default_control_service()
    prepared, recovered = [], []
    service.set_terminal_preparer(lambda _e, cid: prepared.append(cid) or True)
    service.set_terminal_recovery(lambda _e, cid: recovered.append(cid) or True)
    queued = store.get_execution(admitted.execution_id)
    assert queued.status.value == "queued" and queued.current_attempt_id is None
    assert queued.status_version > admitted.status_version

    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version))

    assert not isinstance(command, dict), command
    assert latest.status.value == "cancelled"
    assert prepared == ["stop-1"] and recovered == []


def _child(store, parent, name: str, **extra):
    from openprogram.runtime.execution import CapabilitySet

    return store.admit_execution(
        execution_id=name, run_id=parent.run_id, session_id=parent.session_id,
        revision_id=parent.revision_id, input_ref=f"blob:{name}", input_hash=f"hash-{name}",
        entrypoint="chat", trusted_actor={"subject": "owner"}, config_snapshot_ref="blob:config",
        user_message_id=f"u-{name}", assistant_message_id=f"a-{name}",
        capabilities=CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",)),
        parent_execution_id=parent.execution_id, **extra,
    )


def _family(tmp_path, monkeypatch):
    """A running turn with a synchronous sub-agent (which has a nested turn of
    its own), a background sub-agent and a branch made from a saved point."""
    from types import SimpleNamespace

    import openprogram.runtime.execution.run_control.cancellation as run_cancel

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    parent = store.get_execution(admitted.execution_id)
    sync_agent = _child(store, parent, "sync-agent")
    nested = _child(store, sync_agent, "nested")
    background = _child(store, parent, "background-agent")
    branch = _child(store, parent, "branch")
    monkeypatch.setattr(store, "list_nonterminal", lambda session_id=None, _orig=store.list_nonterminal: [
        record if record.execution_id != "branch" else SimpleNamespace(**{**record.__dict__, "source_checkpoint_id": "cp-1"})
        for record in _orig(session_id=session_id)
    ])
    jobs = {"sync-agent": SimpleNamespace(wait=True), "background-agent": SimpleNamespace(wait=False)}
    monkeypatch.setattr(store, "get_job_agent_input", lambda execution_id: {} if execution_id in jobs else None)
    monkeypatch.setattr(run_cancel, "_find_job", lambda execution_id, session_id=None: jobs.get(execution_id))
    return store, parent, {"sync": sync_agent, "nested": nested, "background": background, "branch": branch}


def test_a_page_stop_stops_what_the_turn_waits_on(tmp_path, monkeypatch) -> None:
    store, parent, children = _family(tmp_path, monkeypatch)

    command, latest = _submit(_cancel(parent.execution_id, parent.status_version))

    assert latest.status.value == "cancelling"
    for name in ("sync", "nested"):
        record = store.get_execution(children[name].execution_id)
        assert record.status.value == "cancelled", name
        assert record.reason_code == "cancel.parent"
        assert len(store.list_commands(record.execution_id)) == 1
    for name in ("background", "branch"):
        assert store.get_execution(children[name].execution_id).status.value == "queued", name


def test_the_internal_stop_takes_every_descendant_through_the_same_finder(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.execution.run_control.cancellation import cancel_execution

    store, parent, children = _family(tmp_path, monkeypatch)

    cancel_execution(parent.execution_id)

    for name, child in children.items():
        record = store.get_execution(child.execution_id)
        assert record.status.value == "cancelled", name
        assert record.reason_code == "cancel.parent"
        assert len(store.list_commands(record.execution_id)) == 1


def _job_input(caller: str | None = None, parent_job: str | None = None, caller_session: str = "session-1") -> dict:
    """The part of an immutable Job input that names who started the Job."""
    return {"job_context": {
        "parent_execution_id": parent_job,
        "caller": None if caller is None else {
            "execution_id": caller, "session_id": caller_session, "msg_id": "u", "node_id": "u",
        },
    }}


def _job(store, parent, name: str, *, session_id: str):
    """A sub-agent Job admitted in ``session_id`` with no canonical parent,
    as a Job delegated to another conversation is."""
    from openprogram.runtime.execution import CapabilitySet

    return store.admit_execution(
        execution_id=name, run_id=f"job-run-{name}", session_id=session_id,
        revision_id=parent.revision_id, input_ref=f"blob:{name}", input_hash=f"hash-{name}",
        entrypoint="chat", trusted_actor={"subject": "owner"}, config_snapshot_ref="blob:config",
        user_message_id=f"u-{name}", assistant_message_id=f"a-{name}",
        capabilities=CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",)),
    )


def _delegated(tmp_path, monkeypatch):
    """A running turn whose synchronous sub-agent runs in another
    conversation, beside a background one there, and a Job started by a
    sub-agent Job that has already ended."""
    from types import SimpleNamespace

    import openprogram.runtime.execution as ex
    import openprogram.runtime.execution.run_control.cancellation as run_cancel

    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    parent = store.get_execution(admitted.execution_id)
    sync_agent = _job(store, parent, "sync-elsewhere", session_id="session-2")
    background = _job(store, parent, "background-elsewhere", session_id="session-2")
    ended = _job(store, parent, "ended-agent", session_id="session-1")
    attempts = ex.AttemptStore(store)
    lease, reserved = attempts.lease(ended.execution_id, expected_version=ended.status_version, owner_id="owner")
    _attempt, running = attempts.activate(lease.attempt_id, generation=lease.generation,
                                          expected_execution_version=reserved.status_version)
    attempts.finish(lease.attempt_id, generation=lease.generation, expected_execution_version=running.status_version,
                    target=ex.ExecutionStatus.COMPLETED, outcome="completed")
    grandchild = _job(store, parent, "grandchild", session_id="session-1")
    inputs = {
        "sync-elsewhere": _job_input(caller=parent.execution_id),
        "background-elsewhere": _job_input(caller=parent.execution_id),
        "ended-agent": _job_input(caller=parent.execution_id),
        "grandchild": _job_input(caller="ended-agent", parent_job="ended-agent"),
    }
    jobs = {key: SimpleNamespace(wait=key != "background-elsewhere") for key in inputs}
    monkeypatch.setattr(store, "get_job_agent_input", lambda execution_id: inputs.get(execution_id))
    monkeypatch.setattr(run_cancel, "_find_job", lambda execution_id, session_id=None: jobs.get(execution_id))
    return store, parent, {"sync": sync_agent, "background": background, "grandchild": grandchild}


def test_a_page_stop_stops_a_synchronous_sub_agent_running_in_another_conversation(tmp_path, monkeypatch) -> None:
    store, parent, children = _delegated(tmp_path, monkeypatch)

    _command, latest = _submit(_cancel(parent.execution_id, parent.status_version))

    assert latest.status.value == "cancelling"
    sync = store.get_execution("sync-elsewhere")
    assert sync.status.value == "cancelled" and sync.reason_code == "cancel.parent"
    assert store.get_execution("background-elsewhere").status.value == "queued"


def test_the_internal_stop_reaches_a_job_behind_one_that_ended(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.execution.run_control.cancellation import cancel_execution

    store, parent, children = _delegated(tmp_path, monkeypatch)

    cancel_execution(parent.execution_id)

    for name, child in children.items():
        record = store.get_execution(child.execution_id)
        assert record.status.value == "cancelled", name
        assert record.reason_code == "cancel.parent"


def _record_stop_order(monkeypatch) -> list[str]:
    import openprogram.runtime.execution as ex

    service = ex.default_control_service()
    order: list[str] = []
    original = service.request_cancel

    async def recording(**kwargs):
        order.append(kwargs["execution_id"])
        return await original(**kwargs)

    monkeypatch.setattr(service, "request_cancel", recording)
    return order


def test_every_stop_stops_the_children_before_the_turn(tmp_path, monkeypatch) -> None:
    """A queued child stopped after its parent could take the pool slot the
    parent frees; children go first on every path."""
    from openprogram.runtime.execution.run_control.cancellation import cancel_execution

    store, parent, _children = _family(tmp_path, monkeypatch)
    order = _record_stop_order(monkeypatch)
    _submit(_cancel(parent.execution_id, parent.status_version))
    assert order[-1] == parent.execution_id, order
    assert order.index("nested") < order.index("sync-agent"), order

    store, parent, _children = _family(tmp_path / "internal", monkeypatch)
    order = _record_stop_order(monkeypatch)
    cancel_execution(parent.execution_id)
    assert order[-1] == parent.execution_id, order


def test_a_protocol_stop_stops_what_the_turn_waits_on(tmp_path, monkeypatch) -> None:
    """ACP and MCP stop through ``submit_observed_cancel``; their stop takes
    the same scope as the page's."""
    import openprogram.runtime.execution as ex
    from openprogram.runtime.execution.control.shared import submit_observed_cancel

    store, parent, children = _family(tmp_path, monkeypatch)

    submitted = asyncio.run(submit_observed_cancel(
        ex.default_control_service(), command_id="acp-stop", execution_id=parent.execution_id,
        expected_version=parent.status_version, actor={"source": "acp"}, reason_code="cancel.user",
    ))

    assert submitted.accepted
    for name in ("sync", "nested"):
        assert store.get_execution(children[name].execution_id).status.value == "cancelled", name
    for name in ("background", "branch"):
        assert store.get_execution(children[name].execution_id).status.value == "queued", name


# A stop replaces every kind of waiting (Codex clears pending approvals and
# questions on interrupt; Claude Code empties its permission queue): the
# person pressed Stop to stop waiting.

def _to(store, execution_id: str, *targets: str) -> None:
    from openprogram.runtime.execution import ExecutionStatus

    for target in targets:
        current = store.get_execution(execution_id)
        store.transition_execution(execution_id, expected_version=current.status_version,
                                   target=ExecutionStatus(target))


@pytest.mark.parametrize("path", [("pausing", "paused"), ("reconciliation_required",)])
def test_a_stop_with_an_old_version_replaces_a_pause_or_a_reconciliation(tmp_path, monkeypatch, path) -> None:
    store, admitted = _turn(tmp_path, monkeypatch, start=True)
    _to(store, admitted.execution_id, *path)
    assert store.get_execution(admitted.execution_id).status.value == path[-1]

    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version))

    assert not isinstance(command, dict), command
    assert latest.status.value in {"cancelling", "cancelled"}


def test_a_stop_with_an_old_version_closes_an_open_question(tmp_path, monkeypatch) -> None:
    from openprogram.runtime.execution.waits import DurableWaitStore, WaitStatus

    from openprogram.runtime.execution import AttemptStore

    store, admitted = _turn(tmp_path, monkeypatch, start=False)
    attempts = AttemptStore(store)
    lease, reserved = attempts.lease(admitted.execution_id, expected_version=admitted.status_version, owner_id="owner")
    attempt, _running = attempts.activate(lease.attempt_id, generation=lease.generation,
                                          expected_execution_version=reserved.status_version)
    waits = DurableWaitStore(store)
    wait = waits.open_wait(
        execution_id=admitted.execution_id, attempt_id=attempt.attempt_id,
        generation=attempt.generation, kind="ask", request={"prompt": "Which file?"},
        policy_snapshot={"version": 1}, expires_at=9_999_999_999,
    )

    command, latest = _submit(_cancel(admitted.execution_id, admitted.status_version))

    assert not isinstance(command, dict), command
    assert latest.status.value in {"cancelling", "cancelled"}
    assert waits.get_wait(wait.wait_id).status is WaitStatus.CANCELLED
