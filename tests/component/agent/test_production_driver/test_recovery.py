"""driver recovery tests."""
from __future__ import annotations
from ._support import (
    AttemptStore,
    ExecutionStatus,
    ExecutionStore,
    SimpleNamespace,
    _admitted,
    _hold_canonical_agent_process,
    asyncio,
    pytest,
    threading,
    time,
)


@pytest.mark.parametrize("stale_observation", [False, True])
def test_second_controller_startup_preserves_a_live_canonical_agent(tmp_path, monkeypatch, stale_observation):
    from openprogram.agent.production_driver import AgentProductionDriver, CanonicalAgentEntry
    from openprogram.execution import DriverRegistry, RuntimeControlService

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, release = threading.Event(), threading.Event()

    def work(*, request, cancel_event):
        entered.set()
        assert release.wait(5)
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
            assert await asyncio.to_thread(entered.wait, 2)
            second_store = ExecutionStore(store.path)
            second = RuntimeControlService(second_store, AttemptStore(second_store), DriverRegistry())
            if stale_observation:
                from openprogram.execution import process_owner
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
            await asyncio.wait_for(handle.done, 3)
        assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED

    asyncio.run(run())



def test_startup_distinguishes_live_and_exited_owner_process(tmp_path):
    import multiprocessing
    from openprogram.execution import DriverRegistry, RuntimeControlService

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
        assert control.recover_startup() == ()
        assert store.get_execution(execution_id).status is ExecutionStatus.RUNNING
        process.terminate()
        process.join(5)
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



def test_process_identity_rejects_pid_reuse_and_preserves_unknown(monkeypatch):
    from openprogram.execution import process_owner

    lease = {"process_owner": process_owner.current_process_owner()}
    monkeypatch.setattr(process_owner, "process_start_identity", lambda _pid: "different-start")
    assert not process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() + 30)
    monkeypatch.setattr(process_owner, "process_start_identity", lambda _pid: None)
    assert process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() + 30)
    assert not process_owner.process_owner_may_be_alive(lease, lease_expires_at=time.time() - 1)



@pytest.mark.parametrize("cause", ["heartbeat_error", "owner_fenced", "cancel"])
def test_canonical_owner_renewal_stops_on_loss_or_cancel(tmp_path, monkeypatch, cause):
    from openprogram.agent import production_driver as module

    monkeypatch.setattr(module.shared, "AGENT_LEASE_SECONDS", 0.3)
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, observed_cancel, release = threading.Event(), threading.Event(), threading.Event()
    def run_turn(*, request, cancel_event):
        entered.set()
        try:
            if cancel_event.wait(2):
                observed_cancel.set()
            assert release.wait(2)
            return SimpleNamespace(failed=False)
        finally:
            release.set()
    driver = module.AgentProductionDriver(store, turn_runner=run_turn)
    entry = module.CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="renewal-loss", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "cancel work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        try:
            active = await entry.activate(admission)
            assert await asyncio.to_thread(entered.wait, 2)
            handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
            if cause == "heartbeat_error":
                def fail(*a, **kw):
                    raise OSError("storage unavailable")
                monkeypatch.setattr(entry.control.attempts, "heartbeat", fail)
            elif cause == "owner_fenced":
                entry.control.recover_owner_loss(admission.execution_id,
                    attempt_id=active.attempt_id, generation=active.generation)
            else:
                current = store.get_execution(admission.execution_id)
                await entry.control.request_cancel(command_id="cancel-renewal",
                    execution_id=admission.execution_id, expected_version=current.status_version,
                    actor={"surface": "test"}, reason_code="user_cancelled")
            assert await asyncio.to_thread(observed_cancel.wait, 2)
            release.set()
            await asyncio.wait_for(handle.done, 3)
            final = store.get_execution(admission.execution_id)
            assert final.status is not ExecutionStatus.COMPLETED
            assert final.status is not ExecutionStatus.RUNNING
        finally:
            release.set()
    asyncio.run(run())



def test_renewal_loss_never_terminates_replacement_by_execution_id(tmp_path, monkeypatch):
    from openprogram.agent import production_driver as module

    monkeypatch.setattr(module.shared, "AGENT_LEASE_SECONDS", 0.03)
    store, execution = _admitted(tmp_path)
    driver = module.AgentProductionDriver(store)
    service = driver._control_service()
    attempt, leased = service.attempts.lease(execution.execution_id,
        expected_version=execution.status_version, owner_id="old", ttl_seconds=30)
    active, _ = service.attempts.activate(attempt.attempt_id, generation=attempt.generation,
        expected_execution_version=leased.status_version)
    handle = module.AgentDriverHandle(execution_id=execution.execution_id,
        attempt_id=active.attempt_id, generation=active.generation,
        session_id=execution.session_id, cancel_event=threading.Event(),
        done=module._ThreadResultFuture())
    physical_kills, recoveries = [], []
    def lose_lease(*a, **kw):
        raise OSError("renewal failed")
    monkeypatch.setattr(service.attempts, "heartbeat", lose_lease)
    def stale_read(_attempt_id):
        # The read can race a handoff. Its stale ACTIVE value must not
        # authorize a kill against the shared execution's replacement.
        return active
    monkeypatch.setattr(service.attempts, "get", stale_read)
    async def terminate(*a, **kw):
        physical_kills.append("replacement")
    monkeypatch.setattr(driver, "terminate", terminate)
    monkeypatch.setattr(service, "recover_owner_loss", lambda execution_id, **kw:
        recoveries.append((execution_id, kw)))
    driver._maintain_owner(active, handle)
    assert handle.cancel_event.is_set()
    assert not physical_kills
    assert recoveries == [(execution.execution_id, {
        "attempt_id": active.attempt_id, "generation": active.generation})]



def test_startup_resumes_admitted_agent_without_attempt(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

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
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.store import ExecutionConflict

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
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.attempts import AttemptConflict

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    attempt_a, _recovered_before_activation = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-a",
        ttl_seconds=30,
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
        ttl_seconds=30,
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

