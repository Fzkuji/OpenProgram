"""driver finalization tests."""
from __future__ import annotations
from ._support import (
    AttemptStore,
    CommandKind,
    CommandStatus,
    ExecutionStatus,
    ExecutionStore,
    SCHEMA_VERSION,
    SimpleNamespace,
    _admitted,
    asyncio,
    pytest,
    sqlite3,
    threading,
    time,
)


def test_v7_migration_backfills_agent_finish_slots_idempotently(tmp_path):
    store, execution = _admitted(tmp_path, execution_id="exec-v7-slot-backfill")
    with sqlite3.connect(store.path) as connection:
        connection.execute("DROP TABLE execution_finish_repair_slots")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()

    migrated = ExecutionStore(store.path)
    with sqlite3.connect(migrated.path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        rows = connection.execute(
            "SELECT execution_id FROM execution_finish_repair_slots"
        ).fetchall()
    assert version == SCHEMA_VERSION
    assert rows == [(execution.execution_id,)]

    reopened = ExecutionStore(store.path)
    with sqlite3.connect(reopened.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots"
        ).fetchone()[0] == 1



def test_canonical_entry_renews_owner_until_long_work_finishes(tmp_path, monkeypatch):
    from openprogram.agent import production_driver as module

    # Use the real public admission/activation and persistence. Only shorten
    # the production lease duration; no direct lifecycle-row mutations.
    monkeypatch.setattr(module.shared, "AGENT_LEASE_SECONDS", 0.3, raising=False)
    store = ExecutionStore(tmp_path / "executions.sqlite3")
    entered, release = threading.Event(), threading.Event()
    def run_turn(*, request, cancel_event):
        entered.set()
        assert release.wait(3)
        return SimpleNamespace(failed=False)
    driver = module.AgentProductionDriver(store, turn_runner=run_turn)
    entry = module.CanonicalAgentEntry(store, driver)
    original_lease = entry.control.attempts.lease
    monkeypatch.setattr(entry.control.attempts, "lease", lambda *a, **kw: original_lease(
        *a, **{**kw, "ttl_seconds": 0.3},
    ))
    admission = entry.admit(
        session_id="renewal", turn_payload={"version": 1, "kind": "chat", "request": {
            "user_text": "long work", "agent_id": "default", "source": "test"}},
        trusted_actor={"subject": "test"}, config_snapshot_ref="config:test",
        user_message_id="u", assistant_message_id="a",
    )
    async def run():
        try:
            active = await entry.activate(admission)
            assert await asyncio.to_thread(entered.wait, 2)
            handle = driver._handles[(admission.execution_id, active.attempt_id, active.generation)]
            before = entry.control.attempts.get(active.attempt_id)
            await asyncio.sleep(0.65)
            renewed = entry.control.attempts.get(active.attempt_id)
            assert renewed.lease_expires_at > before.lease_expires_at
            release.set()
            await asyncio.wait_for(handle.done, 3)
            assert store.get_execution(admission.execution_id).status is ExecutionStatus.COMPLETED
            assert store.list_finish_repairs(limit=10) == []
        finally:
            release.set()
    asyncio.run(run())



def test_runner_exception_finishes_as_failed(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path)

    def run_turn(*, request, cancel_event):
        del request, cancel_event
        raise RuntimeError("owner process lost")

    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "run",
                "agent_id": "default",
                "source": "canonical-agent",
            },
        },
        turn_runner=run_turn,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )

    async def run():
        binding = await driver.activate(active, activation=None)
        driver.activation_committed(binding)
        handle = binding.handle
        await handle.done
        return handle

    asyncio.run(run())
    recovered = store.get_execution(execution.execution_id)
    assert recovered is not None
    assert recovered.status is ExecutionStatus.FAILED
    assert recovered.reason_code == "agent_runner_error"



def test_finish_transient_failure_retries_after_handle_release(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path, execution_id="exec-finish-retry")
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
        turn_runner=lambda **_kwargs: None,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    control = driver._control_service()
    real_finish = control.finish_attempt
    calls = 0

    def flaky_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("temporary sqlite failure")
        return real_finish(*args, **kwargs)

    control.finish_attempt = flaky_finish
    driver._finish_attempt(
        active,
        type("Result", (), {"failed": False, "error": None})(),
        threading.Event(),
    )
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status is ExecutionStatus.COMPLETED:
            break
        time.sleep(0.02)
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.COMPLETED
    assert calls >= 2
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and driver._pending_finishes:
        time.sleep(0.01)
    assert driver._pending_finishes == {}



def test_finish_retry_re_reads_cancellation_state(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver

    store, execution = _admitted(tmp_path, execution_id="exec-finish-cancel-race")
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
        turn_runner=lambda **_kwargs: None,
    )
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    control = driver._control_service()
    real_finish = control.finish_attempt
    first_called = threading.Event()
    release = threading.Event()
    calls = 0

    def flaky_finish(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            first_called.set()
            assert release.wait(3)
            raise OSError("temporary sqlite failure")
        return real_finish(*args, **kwargs)

    control.finish_attempt = flaky_finish
    worker = threading.Thread(
        target=driver._finish_attempt,
        args=(active, type("Result", (), {"failed": False, "error": None})(), threading.Event()),
        daemon=True,
    )
    worker.start()
    assert first_called.wait(3)
    store.accept_command_with_transition(
        command_id="cancel-finish-race",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        kind=CommandKind.CANCEL,
        target=ExecutionStatus.CANCELLING,
        payload={"reason_code": "cancel.user"},
        actor={"surface": "test"},
        reason_code="cancel.user",
    )
    release.set()
    worker.join(3)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        current = store.get_execution(execution.execution_id)
        if current is not None and current.status is ExecutionStatus.CANCELLED:
            break
        time.sleep(0.02)
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.CANCELLED
    assert current.reason_code == "cancel.user"
    command = store.get_command("cancel-finish-race")
    assert command is not None
    assert command.status is CommandStatus.APPLIED
    assert calls >= 2
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and driver._pending_finishes:
        time.sleep(0.01)
    assert driver._pending_finishes == {}



def test_finish_repair_intent_replays_after_driver_restart(tmp_path):
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-finish-replay")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )

    # A fresh process's startup control service replays the durable repair
    # intent before handling any new turn.
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {},
        turn_runner=lambda **_kwargs: None,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    assert driver._pending_finishes == {}
    assert service.replay_finish_repairs() == 1
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.COMPLETED
    assert store.list_finish_repairs() == []



def test_finish_repair_replay_binds_current_cancel_command(tmp_path):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-finish-cancel-replay")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-owner",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    _command, cancelling, duplicate = store.accept_command_with_transition(
        command_id="cancel-replay",
        execution_id=execution.execution_id,
        expected_version=running.status_version,
        kind=CommandKind.CANCEL,
        target=ExecutionStatus.CANCELLING,
        payload={"reason_code": "cancel.user"},
        actor={"surface": "test"},
        reason_code="cancel.user",
    )
    assert not duplicate
    store.upsert_finish_repair(
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_version=cancelling.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )

    service = RuntimeControlService(store, attempts, DriverRegistry())
    assert service.replay_finish_repairs() == 1
    current = store.get_execution(execution.execution_id)
    command = store.get_command("cancel-replay")
    assert current is not None and current.status is ExecutionStatus.CANCELLED
    assert command is not None and command.status is CommandStatus.APPLIED
    assert store.list_finish_repairs() == []



def test_finish_repair_capacity_preserves_actionable_rows(tmp_path, monkeypatch):
    from openprogram.execution.store import ExecutionConflict

    monkeypatch.setattr("openprogram.execution.store.store._FINISH_REPAIR_HIGH_WATERMARK", 1)
    store, first = _admitted(tmp_path, execution_id="exec-repair-capacity-1")
    attempts = AttemptStore(store)
    first_attempt, first_leased = attempts.lease(
        first.execution_id,
        expected_version=first.status_version,
        owner_id="owner-1",
        ttl_seconds=30,
    )
    first_active, first_running = attempts.activate(
        first_attempt.attempt_id,
        generation=first_attempt.generation,
        expected_execution_version=first_leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=first.execution_id,
        attempt_id=first_active.attempt_id,
        generation=first_active.generation,
        expected_version=first_running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )
    with pytest.raises(ExecutionConflict) as rejected:
        store.admit_execution(
            execution_id="exec-repair-capacity-agent",
            run_id="run-repair-capacity-agent",
            session_id="session-repair-capacity-agent",
            revision_id=first.revision_id,
            input_ref="input:repair-capacity-agent",
            input_hash="input-hash-agent",
            entrypoint="openprogram.agent.dispatcher:process_user_turn",
            trusted_actor={"subject": "user-2"},
            config_snapshot_ref="config:repair-capacity-agent",
            agent_turn_payload={
                "version": 1,
                "kind": "chat",
                "request": {"user_text": "run", "agent_id": "default", "source": "test"},
            },
        )
    assert rejected.value.code == "finish_repair_capacity"
    revision = store.create_revision(
        revision_id="revision-repair-capacity-2", manifest={"entrypoint": "agent", "slot": 2}
    )
    second = store.admit_execution(
        execution_id="exec-repair-capacity-2",
        run_id="run-repair-capacity-2",
        session_id="session-repair-capacity-2",
        revision_id=revision.revision_id,
        input_ref="input:repair-capacity-2",
        input_hash="input-hash-2",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-2"},
        config_snapshot_ref="config:repair-capacity-2",
        agent_turn_payload=None,
    )
    second_attempt, second_leased = attempts.lease(
        second.execution_id,
        expected_version=second.status_version,
        owner_id="owner-2",
        ttl_seconds=30,
    )
    second_active, second_running = attempts.activate(
        second_attempt.attempt_id,
        generation=second_attempt.generation,
        expected_execution_version=second_leased.status_version,
    )
    store.upsert_finish_repair(
        execution_id=second.execution_id,
        attempt_id=second_active.attempt_id,
        generation=second_active.generation,
        expected_version=second_running.status_version,
        target=ExecutionStatus.COMPLETED.value,
        outcome="completed",
        reason_code=None,
    )
    rows = store.list_finish_repairs(limit=2)
    assert {row["execution_id"] for row in rows} == {
        first.execution_id, second.execution_id,
    }
    attempts.finish(
        first_active.attempt_id,
        generation=first_active.generation,
        expected_execution_version=first_running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    attempts.finish(
        second_active.attempt_id,
        generation=second_active.generation,
        expected_execution_version=second_running.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    recovered = store.admit_execution(
        execution_id="exec-repair-capacity-recovered",
        run_id="run-repair-capacity-recovered",
        session_id="session-repair-capacity-recovered",
        revision_id=first.revision_id,
        input_ref="input:repair-capacity-recovered",
        input_hash="input-hash-recovered",
        entrypoint="openprogram.agent.dispatcher:process_user_turn",
        trusted_actor={"subject": "user-3"},
        config_snapshot_ref="config:repair-capacity-recovered",
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {"user_text": "run", "agent_id": "default", "source": "test"},
        },
    )
    assert recovered.status is ExecutionStatus.QUEUED



def test_finish_repair_replay_processes_more_than_one_page(tmp_path):
    from openprogram.execution.attempts import AttemptConflict
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    revision = store.create_revision(
        revision_id="revision-many-repairs", manifest={"entrypoint": "agent"}
    )
    attempts = AttemptStore(store)
    blocked_attempt_ids = set()
    for index in range(260):
        execution = store.admit_execution(
            execution_id=f"exec-many-repairs-{index:03d}",
            run_id=f"run-many-repairs-{index:03d}",
            session_id=f"session-many-repairs-{index:03d}",
            revision_id=revision.revision_id,
            input_ref=f"input:many-repairs-{index}",
            input_hash=f"input-hash-{index}",
            entrypoint="openprogram.agent.dispatcher:process_user_turn",
            trusted_actor={"subject": "test"},
            config_snapshot_ref="config:many-repairs",
            agent_turn_payload={
                "version": 1,
                "kind": "chat",
                "request": {"user_text": "run", "agent_id": "default", "source": "test"},
            },
        )
        leased_attempt, leased_execution = attempts.lease(
            execution.execution_id,
            expected_version=execution.status_version,
            owner_id=f"owner-{index}",
            ttl_seconds=30,
        )
        active_attempt, running_execution = attempts.activate(
            leased_attempt.attempt_id,
            generation=leased_attempt.generation,
            expected_execution_version=leased_execution.status_version,
        )
        if index < 256:
            blocked_attempt_ids.add(active_attempt.attempt_id)
        store.upsert_finish_repair(
            execution_id=execution.execution_id,
            attempt_id=active_attempt.attempt_id,
            generation=active_attempt.generation,
            expected_version=running_execution.status_version,
            target=ExecutionStatus.COMPLETED.value,
            outcome="completed",
            reason_code=None,
        )
    service = RuntimeControlService(store, attempts, DriverRegistry())
    original_finish = service.finish_attempt

    def block_first_page(*args, **kwargs):
        if kwargs["attempt_id"] in blocked_attempt_ids:
            raise AttemptConflict("blocked", "test blocked head")
        return original_finish(*args, **kwargs)

    service.finish_attempt = block_first_page
    assert service.replay_finish_repairs() == 4
    assert len(store.list_finish_repairs()) == 256



def test_finish_repair_stalls_after_bounded_attempts_until_manual_reconcile(
    tmp_path, monkeypatch,
):
    from openprogram.agent.production_driver import AgentProductionDriver

    monkeypatch.setattr(
        "openprogram.agent.production_driver.shared.FINISH_RETRY_LIMIT", 0,
    )
    store, execution = _admitted(tmp_path, execution_id="exec-repair-stalled")
    attempts = AttemptStore(store)
    attempt, leased = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="owner-stalled",
        ttl_seconds=30,
    )
    active, _running = attempts.activate(
        attempt.attempt_id,
        generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    driver = AgentProductionDriver(
        executions=store,
        input_resolver=lambda _record: {},
        turn_runner=lambda **_kwargs: None,
    )
    service = driver._control_service()
    original_finish = service.finish_attempt

    def fail_finish(*_args, **_kwargs):
        raise OSError("persistent failure")

    service.finish_attempt = fail_finish
    driver._finish_attempt(
        active,
        type("Result", (), {"failed": False, "error": None})(),
        threading.Event(),
    )
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        rows = store.list_finish_repairs()
        if (
            rows
            and rows[0]["reason_code"] == "finish_repair_stalled"
            and driver._finish_retry_timer is not None
        ):
            break
        time.sleep(0.01)
    rows = store.list_finish_repairs()
    assert rows and rows[0]["reason_code"] == "finish_repair_stalled"
    assert driver._pending_finishes == {}
    assert driver._finish_retry_timer is not None
    driver._finish_retry_timer.cancel()
    service.finish_attempt = original_finish
    assert service.replay_finish_repairs(include_stalled=True) == 1
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.COMPLETED
    assert store.list_finish_repairs() == []



def test_startup_recovers_active_agent_owner_loss_retains_completion_repair_capacity(
    tmp_path,
):
    from openprogram.execution.control import RuntimeControlService
    from openprogram.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id="exec-active-owner-loss")
    attempts = AttemptStore(store)
    leased, leased_execution = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="crashed-agent",
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=leased_execution.status_version,
    )
    service = RuntimeControlService(store, attempts, DriverRegistry())

    recoveries = service.recover_startup()

    current = store.get_execution(execution.execution_id)
    ended = attempts.get(active.attempt_id)
    assert current is not None
    assert current.status is ExecutionStatus.PAUSED
    assert current.reason_code == "restart_pending"
    assert current.current_attempt_id is None
    assert ended is not None and ended.status.value == "ended"
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM execution_finish_repair_slots "
            "WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()[0] == 1
    assert [item.execution.execution_id for item in recoveries] == [execution.execution_id]



def test_iteration_exhaustion_finishes_canonical_execution_as_failed(tmp_path):
    from openprogram.agent.agent_loop import agent_loop
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.agent.types import AgentContext, AgentLoopConfig, AgentTool, AgentToolResult
    from openprogram.providers.types import AssistantMessage, EventDone, Model, TextContent, ToolCall, UserMessage

    store, execution = _admitted(tmp_path)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(execution.execution_id,
        expected_version=execution.status_version, owner_id="limit-owner", ttl_seconds=30)
    active, _running = attempts.activate(leased.attempt_id,
        generation=leased.generation, expected_execution_version=reserved.status_version)

    def run_turn(**kwargs):
        async def run():
            message = AssistantMessage(content=[ToolCall(id="again", name="again", arguments={})],
                api="fake", provider="fake", model="fake", stop_reason="toolUse", timestamp=1)
            async def stream(*_):
                yield EventDone(reason="toolUse", message=message)
            async def execute(*_):
                return AgentToolResult(content=[TextContent(text="pending")])
            tool = AgentTool(name="again", label="again", description="again",
                parameters={"type": "object", "properties": {}}, execute=execute)
            events = agent_loop([UserMessage(content="continue", timestamp=0)],
                AgentContext(tools=[tool]), AgentLoopConfig(
                    model=Model(id="fake", name="fake", api="fake", provider="fake", base_url="https://example.invalid"),
                    convert_to_llm=lambda messages: messages, max_iterations=1),
                stream_fn=stream)
            return await events.result()
        return asyncio.run(run())

    driver = AgentProductionDriver(store, turn_runner=run_turn)
    result = asyncio.run(driver._run_attempt(active,
        TurnRequest(session_id=execution.session_id, agent_id="default",
            user_text="continue", source="component"), threading.Event()))
    assert result.failed
    assert "iteration limit" in result.error
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.FAILED

