"""driver recovery tests."""
from __future__ import annotations
import pytest
import time
from openprogram.runtime.execution.model import ExecutionStatus
from ._support import (
    AttemptStore,
    ExecutionStore,
    SimpleNamespace,
    _admitted,
    _hold_canonical_agent_process,
    asyncio,
    threading,
)


@pytest.mark.parametrize("stale_observation", [False, True])
def test_second_controller_startup_preserves_a_live_canonical_agent(tmp_path, monkeypatch, stale_observation):
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver, CanonicalAgentEntry
    from openprogram.runtime.execution import DriverRegistry, RuntimeControlService

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, release = threading.Event(), threading.Event()

    def work(*, request, cancel_event):
        entered.set()
        assert release.wait(30)
        return SimpleNamespace(failed=False)

    driver = AgentProductionDriver(store, turn_runner=work)
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="live-goal-owner",
        turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    before_activation = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    assert before_activation.recover_startup() == ()
    original_activate = entry.control.attempts.activate
    def activate_after_startup_check(*args, **kwargs):
        # The lease exists, but the physical driver has not started yet.
        assert before_activation.recover_startup() == ()
        return original_activate(*args, **kwargs)
    monkeypatch.setattr(entry.control.attempts, "activate", activate_after_startup_check)

    async def run():
        active = await entry.activate(admission)
        handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
        try:
            assert await asyncio.to_thread(entered.wait, 30)
            second_store = ExecutionStore(store.path)
            second = RuntimeControlService(second_store, AttemptStore(second_store), DriverRegistry())
            if stale_observation:
                from openprogram.runtime.execution import process_owner
                original = process_owner.process_owner_may_be_alive
                observations = []
                def stale_once(*args, **kwargs):
                    observations.append(True)
                    return False if len(observations) == 1 else original(*args, **kwargs)
                monkeypatch.setattr(process_owner, "process_owner_may_be_alive", stale_once)
            recovered = second.recover_startup()
            assert all(item.execution.execution_id != admission.execution_id for item in recovered)
            assert second_store.get_execution(admission.execution_id).status is ExecutionStatus.RUNNING
        finally:
            release.set()
            # A ceiling, not a speed check: under a loaded suite the settle takes seconds.
            await asyncio.wait_for(handle.done, 30)
        assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED

    asyncio.run(run())



def test_startup_recovers_the_turn_of_a_process_that_is_gone(tmp_path):
    import multiprocessing
    from openprogram.runtime.execution import DriverRegistry, RuntimeControlService

    context = multiprocessing.get_context("spawn")
    receive, send = context.Pipe(duplex=False)
    db_path = tmp_path / "process.sqlite3"
    process = context.Process(target=_hold_canonical_agent_process, args=(db_path, send))
    process.start()
    send.close()
    try:
        assert receive.poll(10), "child Agent did not enter work"
        execution_id = receive.recv()
        store = ExecutionStore(db_path)
        control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
        process.terminate()
        process.join(30)
        assert not process.is_alive()
        recovered = control.recover_startup()
        assert [item.execution.execution_id for item in recovered] == [execution_id]
        assert store.get_execution(execution_id).status is ExecutionStatus.PAUSED
        assert store.get_execution(execution_id).reason_code == "restart_pending"
    finally:
        if process.is_alive():
            process.terminate()
            process.join(5)
        receive.close()
        process.close()



def test_a_live_owner_is_this_process_or_one_that_recorded_how_to_find_it():
    import os
    from openprogram.host._platform.processes import process_start_token
    from openprogram.runtime.execution import process_owner

    assert process_owner.process_owner_may_be_alive({"process_owner": process_owner.current_process_owner()})
    parent = {"instance": "another-process", "pid": os.getppid()}
    assert process_owner.process_owner_may_be_alive(
        {"process_owner": {**parent, "started": process_start_token(os.getppid())}})
    assert not process_owner.process_owner_may_be_alive({"process_owner": {**parent, "started": "an earlier start"}})
    assert not process_owner.process_owner_may_be_alive({"process_owner": {"instance": "an-earlier-process"}})
    assert not process_owner.process_owner_may_be_alive({})


def test_startup_resumes_admitted_agent_without_attempt(tmp_path):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-unstarted-agent")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())

    recoveries = service.recover_startup()

    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.PAUSED
    assert current.reason_code == "restart_pending"
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]



def test_startup_recovery_reloads_after_concurrent_transition_conflict(
    tmp_path, monkeypatch,
):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.store import ExecutionConflict

    store, execution = _admitted(tmp_path, execution_id="exec-recovery-race")
    service = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    original = store._transition_execution
    calls = 0

    def concurrent_transition(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ExecutionConflict("status_conflict", "another recovery won")
        return original(*args, **kwargs)

    # Recovery checks owner evidence and transitions in the same transaction.
    monkeypatch.setattr(store, "_transition_execution", concurrent_transition)
    recoveries = service.recover_startup()

    assert calls == 1
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.QUEUED
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]



def test_late_owner_loss_cannot_recover_a_new_attempt(tmp_path):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.attempts import AttemptConflict

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    attempt_a, _recovered_before_activation = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-a",
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())

    # A loses ownership before activation. Recovery keeps the queued
    # execution reusable, but clears A's exact lease.
    recovered = service.recover_owner_loss(
        execution.execution_id,
        attempt_id=attempt_a.attempt_id,
        generation=attempt_a.generation,
    )
    attempt_b, running = attempts.lease(
        execution.execution_id,
        expected_version=recovered.execution.status_version,
        owner_id="owner-b",
    )
    assert attempt_b.generation > attempt_a.generation
    before = store.get_execution(execution.execution_id)
    assert before is not None

    with pytest.raises(AttemptConflict) as stale:
        service.recover_owner_loss(
            execution.execution_id,
            attempt_id=attempt_a.attempt_id,
            generation=attempt_a.generation,
        )

    after = store.get_execution(execution.execution_id)
    assert stale.value.code == "stale_owner"
    assert after == before
    assert after.current_attempt_id == attempt_b.attempt_id
    assert after.status is ExecutionStatus.QUEUED



def test_a_process_without_the_worker_lock_recovers_nothing(tmp_path, monkeypatch):
    from openprogram.runtime.execution import DriverRegistry, RuntimeControlService, recover_execution_startup
    from openprogram.runtime.execution.store import ownership

    store = ExecutionStore(tmp_path / "state.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr(control, "recover_startup", lambda: pytest.fail("recovered without the lock"))
    monkeypatch.setattr(ownership, "owns_store_schema", lambda _path: False)
    assert recover_execution_startup(control_service=control).canonical == ()


def test_a_run_stopped_while_pausing_recovers_at_startup(tmp_path):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-pausing-owner-loss")
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version, owner_id="gone",
    )
    _, running = attempts.activate(
        leased.attempt_id, generation=leased.generation, expected_execution_version=reserved.status_version,
    )
    with store._transaction() as connection:
        store._transition_execution(
            connection, execution.execution_id, expected_version=running.status_version,
            target=ExecutionStatus.PAUSING, reason_code="pause.user",
        )
    from openprogram.runtime.execution.control import recovery as recovery_module
    original = recovery_module.journal_head
    recovery_module.journal_head = lambda *a, **k: (SimpleNamespace(has_decision=True), ())
    original_publish = recovery_module.publish_position
    recovery_module.publish_position = lambda *a, **k: False
    try:
        RuntimeControlService(store, attempts, DriverRegistry()).recover_startup()
    finally:
        recovery_module.journal_head = original
        recovery_module.publish_position = original_publish
    assert store.get_execution(execution.execution_id).status.value in {"interrupted", "paused"}
