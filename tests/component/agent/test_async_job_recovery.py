"""Async Job recovery component tests."""
from __future__ import annotations

import threading

import pytest

from tests.component.agent.async_job_support import (
    _FakeMonotonic,
    fake_worker,
    store_fixture,
)
from tests.support.waiting import wait_until


def test_runner_spawn_completes(store_fixture, fake_worker, monkeypatch):
    # Silence ws broadcasts inside tests (no real server running).
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    tid = runner.spawn_job(
        session_id="p1", prompt="do thing", agent_id="main",
        parent_msg_id="a1", label="alpha",
    )
    barrier.set()
    final = runner.await_job(tid, timeout=5.0)
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert final.result_text == "hello"
    assert final.head_id == "head_ok"
    assert len(calls) == 1
    assert calls[0]["prompt"] == "do thing"
    assert calls[0]["branch_from"] == "a1"

def test_runner_resumes_deferred_job_at_current_target_head(
    store_fixture, fake_worker, monkeypatch,
):
    """A busy-target Job keeps one admission while its target advances."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    job_id = runner.spawn_job(
        job_id="t_deferred",
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a1",
        caller_msg_id="caller",
        creates_agent=False,
        defer_dispatch=True,
    )
    assert not fake_worker[3].wait(0.8), "deferred job ran before inbox delivery"

    resumed = runner.spawn_job(
        job_id=job_id,
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a2",
        caller_msg_id="caller",
        creates_agent=False,
        resume_deferred=True,
    )
    barrier.set()
    final = runner.await_job(resumed, timeout=5.0)

    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert calls[-1]["branch_from"] == "a2"

def test_deferred_resume_recovers_after_ready_publish_crash(
    store_fixture, fake_worker, monkeypatch,
):
    """Retry finishes the same fenced resume after Job save succeeds."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    calls, barrier, _, _ = fake_worker
    from openprogram.agent.job import get_runner, JobStatus
    runner = get_runner()
    job_id = runner.spawn_job(
        job_id="t_resume_crash",
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a1",
        caller_msg_id="caller",
        creates_agent=False,
        defer_dispatch=True,
    )
    real_mark_ready = runner._governor.mark_dispatch_ready
    attempts = 0

    def crash_once(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise SystemExit("crash after Job save")
        return real_mark_ready(*args, **kwargs)

    monkeypatch.setattr(runner._governor, "mark_dispatch_ready", crash_once)
    with pytest.raises(SystemExit, match="crash after Job save"):
        runner.spawn_job(
            job_id=job_id,
            session_id="p1",
            prompt="queued delivery",
            agent_id="main",
            parent_msg_id="a2",
            caller_msg_id="caller",
            creates_agent=False,
            resume_deferred=True,
        )

    resumed = runner.spawn_job(
        job_id=job_id,
        session_id="p1",
        prompt="queued delivery",
        agent_id="main",
        parent_msg_id="a2",
        caller_msg_id="caller",
        creates_agent=False,
        resume_deferred=True,
    )
    barrier.set()
    final = runner.await_job(resumed, timeout=5.0)

    assert attempts == 2
    assert final is not None
    assert final.status == JobStatus.COMPLETED
    assert calls[-1]["branch_from"] == "a2"
    admission = runner._governor.ledger.connection().execute(
        "SELECT COUNT(*), state, resume_parent_msg_id "
        "FROM job_admissions WHERE job_id = ?",
        (job_id,),
    ).fetchone()
    assert tuple(admission) == (1, "released", None)

def test_runner_recovers_deferred_inbox_after_process_crash(
    store_fixture, monkeypatch,
):
    """A crash after admission but before inbox publish is recoverable."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import inbox
    from openprogram.agent.job import get_runner
    import openprogram.agent.job.runner as runner_mod
    runner = get_runner()
    intent = {
        "message": "recover me",
        "sender_session_id": "p1",
        "sender_msg_id": "a1",
        "sender_agent_id": "main",
        "agent_id": "main",
        "chain_messages": 0,
        "chain_generations": 0,
        "target_head_id": "a1",
        "job_id": "t_crash_deferred",
        "tracked_job": False,
    }

    with pytest.raises(SystemExit):
        runner.spawn_job(
            job_id="t_crash_deferred",
            session_id="p1",
            prompt="[message from p1:a1] recover me",
            agent_id="main",
            parent_msg_id="a1",
            caller_msg_id="a1",
            caller_session_id="p1",
            creates_agent=False,
            defer_dispatch=True,
            deferred_inbox=intent,
            on_accepted=lambda _job: (_ for _ in ()).throw(SystemExit()),
        )
    assert inbox.pending_count("p1") == 0

    governor = runner._governor
    runner_mod.shutdown_runner()
    recovered = runner_mod.JobRunner(max_workers=1, governor=governor)
    try:
        assert inbox.pending_count("p1") == 1
    finally:
        recovered.shutdown()

def test_sync_child_inside_governed_worker_borrows_parent_live_claim(
    store_fixture, monkeypatch,
):
    """A same-session sync child completes with one worker/live slot."""
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger
    ledger = UsageLedger(store_fixture.root_path.parent / "borrow.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    calls: list[str] = []

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        calls.append(prompt)
        if prompt == "parent":
            child = run_agent_turn(
                session_id=session_id,
                prompt="child",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(
                head_id="parent_head",
                final_text=f"parent saw {child.final_text}",
                failed=child.failed,
                error=child.error,
            )
        return AgentTurnResult(head_id="child_head", final_text="child done")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent", agent_id="main", parent_msg_id="a1",
    )
    try:
        final = runner.await_job(parent_id, timeout=2.0)
        assert final is not None
        assert final.status == JobStatus.COMPLETED
        assert final.result_text == "parent saw child done"
        assert calls == ["parent", "child"]
    finally:
        for job in runner.list_jobs("p1"):
            if job.status not in {
                JobStatus.COMPLETED, JobStatus.CANCELLED, JobStatus.ERRORED,
            }:
                runner.cancel_execution(job.id, reason="test cleanup")
        runner.shutdown(wait=False)

def test_borrowed_child_system_exit_finalizes_and_cleans_child_ownership(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    class FatalProcess:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout=None):
            return 0

    class FatalRuntime:
        def __init__(self, process):
            self._proc = process

    ledger = UsageLedger(tmp_path / "borrow-system-exit.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)
    process = FatalProcess()
    observed: dict = {}

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        if prompt == "parent-fatal":
            try:
                run_agent_turn(
                    session_id=session_id,
                    prompt="child-fatal",
                    agent_id=agent_id,
                    branch_from="a1",
                    caller_msg_id="a1",
                    chain_generations=1,
                )
            except SystemExit as exc:
                observed["exception"] = exc
            finally:
                child_id = observed["child_id"]
                observed["child"] = runner.get_job(child_id)
                observed["child_admission"] = ledger.connection().execute(
                    "SELECT state, reason_code FROM job_admissions "
                    "WHERE job_id = ?", (child_id,),
                ).fetchone()
                observed["parent_admission"] = ledger.connection().execute(
                    "SELECT state, owner_instance_id, lease_generation "
                    "FROM job_admissions WHERE job_id = ?",
                    (observed["parent_id"],),
                ).fetchone()
                observed["child_token"] = run_control.current_token(
                    session_id, execution_id=child_id,
                )
                observed["current_execution_id"] = (
                    run_control.get_current_execution_id()
                )
                with runner._lock:
                    observed["child_in_jobs"] = child_id in runner._jobs
                    observed["child_in_done"] = child_id in runner._done_events
                observed["lease_alive"] = any(
                    thread.name == f"op-job-borrowed-lease-{child_id}"
                    and thread.is_alive()
                    for thread in threading.enumerate()
                )
                run_control.kill_active_runtime(
                    session_id, execution_id=child_id,
                )
            return AgentTurnResult(
                head_id="parent-head", final_text="parent recovered",
            )
        child_id = run_control.get_current_execution_id()
        observed["child_id"] = child_id
        run_control.register_active_runtime(
            session_id, FatalRuntime(process), execution_id=child_id,
        )
        raise SystemExit("child fatal")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent-fatal", agent_id="main",
        parent_msg_id="a1",
    )
    observed["parent_id"] = parent_id
    try:
        parent = runner.await_job(parent_id, timeout=2.0)

        assert parent is not None
        assert parent.status == JobStatus.COMPLETED
        assert "exception" not in observed
        child = observed["child"]
        assert child.status == JobStatus.ERRORED
        assert child.reason_code == "owner_lost"
        assert child.error is None
        assert tuple(observed["child_admission"]) == (
            "released", "owner_lost",
        )
        parent_admission = observed["parent_admission"]
        assert parent_admission[0] == "live"
        assert parent_admission[1] == runner._instance_id
        assert parent_admission[2] > 0
        assert observed["child_token"] is None
        assert observed["current_execution_id"] == parent_id
        assert observed["child_in_jobs"] is False
        assert observed["child_in_done"] is False
        assert observed["lease_alive"] is False
        assert process.terminated is False
    finally:
        child_id = observed.get("child_id")
        if child_id is not None:
            run_control.unregister_active_runtime(
                "p1", execution_id=child_id,
            )
            token = run_control.current_token("p1", execution_id=child_id)
            if token is not None:
                run_control.unregister_cancel_event(
                    "p1", token.event, execution_id=child_id,
                )
        runner.shutdown(wait=False)

def test_durable_worker_baseexception_persists_terminal_before_release(
    store_fixture, monkeypatch, tmp_path,
):
    broadcasts = []
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", broadcasts.append,
    )
    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(lambda **_kwargs: (_ for _ in ()).throw(SystemExit("fatal"))),
    )
    ledger = UsageLedger(tmp_path / "fatal-worker.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    job_id = runner.spawn_job(
        session_id="p1", prompt="fatal", agent_id="main", parent_msg_id="a1",
    )
    try:
        job = runner.await_job(job_id, timeout=2)
        admission = ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()
        assert job is not None and job.status == JobStatus.ERRORED
        assert admission[0] == "released"
        def find_terminal():
            return next(
                (
                    event for event in reversed(broadcasts)
                    if event.get("type") == "job_status"
                    and event["data"]["status"] == "errored"
                ),
                None,
            )

        assert wait_until(lambda: find_terminal() is not None)
        terminal = find_terminal()
        assert terminal is not None
        assert "resource_state" not in terminal["data"]["resource"]
        assert terminal["data"]["resource"]["resource"]["resource_state"] == "released"
    finally:
        runner.shutdown(wait=False)

def test_running_status_write_failure_reconciles_terminal_and_releases(
    store_fixture, monkeypatch, tmp_path,
):
    import openprogram.agent.job.runner as runner_mod
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.usage.ledger import UsageLedger

    broadcasts = []
    monkeypatch.setattr(runner_mod, "_broadcast", lambda payload: broadcasts.append(payload))
    real_update = runner_mod._store_update_status

    failed = True

    def fail_running(session_id, job_id, status, **fields):
        if failed and status in {JobStatus.RUNNING, JobStatus.ERRORED}:
            raise OSError("job store unavailable")
        return real_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(runner_mod, "_store_update_status", fail_running)
    ledger = UsageLedger(tmp_path / "running-write.db")
    drivers = []

    def make_driver(store, service):
        driver = AgentProductionDriver(store, control_service=service)
        drivers.append(driver)
        return driver

    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(ledger),
        agent_driver_factory=make_driver,
    )
    job_id = runner.spawn_job(
        session_id="p1", prompt="x", agent_id="main", parent_msg_id="a1",
    )
    try:
        runner.await_job(job_id, timeout=1)
        job = runner.get_job(job_id)
        admission = ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()
        assert job is not None and job.status == JobStatus.QUEUED
        assert admission[0] == "released"
        assert ledger.connection().execute(
            "SELECT state FROM job_finalizations WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "pending"
        assert len(drivers) == 1
        assert wait_until(lambda: not drivers[0]._handles, timeout=2)
        assert not drivers[0]._continuation_start_gates
        failed = False
        broadcasts.clear()
        runner._reconcile_resources()
        assert runner.get_job(job_id).status == JobStatus.ERRORED
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
        ).fetchone()[0] == "released"
        terminal = [
            item for item in broadcasts
            if item.get("type") == "job_status"
            and item["data"].get("job_id") == job_id
        ]
        assert len(terminal) == 1
        assert terminal[0]["data"]["status"] == "errored"
        assert "resource_state" not in terminal[0]["data"]["resource"]
        assert terminal[0]["data"]["resource"]["resource"]["resource_state"] == "released"
        reloads = [item for item in broadcasts if item.get("type") == "session_reload"]
        assert len(reloads) == 1
        assert reloads[0]["data"]["reason"] == "job_errored"
        broadcasts.clear()
        runner._reconcile_resources()
        assert broadcasts == []
        from openprogram.agent.sub_agent_run import AgentTurnResult
        monkeypatch.setattr(
            "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
            staticmethod(lambda **_kwargs: AgentTurnResult(final_text="ok")),
        )
        next_id = runner.spawn_job(
            session_id="p1", prompt="next", agent_id="main", parent_msg_id="a1",
        )
        assert runner.await_job(next_id, timeout=2).status == JobStatus.COMPLETED
    finally:
        runner.shutdown(wait=False)

def test_vanished_job_row_releases_admission_instead_of_leaking_it(
    store_fixture, monkeypatch, tmp_path,
):
    """A job whose store row vanishes never reaches a terminal state.

    Its admission must still be released — otherwise the 'live' row
    permanently consumes max_live_per_session and no further job can be
    admitted for that session.
    """
    import openprogram.agent.job.runner as runner_mod
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    monkeypatch.setattr(runner_mod, "_broadcast", lambda *a, **k: None)
    real_update = runner_mod._store_update_status
    vanish = True

    def vanish_on_running(session_id, job_id, status, **fields):
        # Mimic "job entity vanished": pending → running finds no row.
        if vanish and status is JobStatus.RUNNING:
            return None
        return real_update(session_id, job_id, status, **fields)

    monkeypatch.setattr(runner_mod, "_store_update_status", vanish_on_running)
    ledger = UsageLedger(tmp_path / "vanished-row.db")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    try:
        job_id = runner.spawn_job(
            session_id="p1", prompt="x", agent_id="main", parent_msg_id="a1",
        )
        def admission_state():
            row = ledger.connection().execute(
                "SELECT state FROM job_admissions WHERE job_id = ?", (job_id,),
            ).fetchone()
            return row[0] if row else None

        assert wait_until(
            lambda: admission_state() == "released", timeout=3.0,
        )
        state = admission_state()
        assert state == "released"
        assert ledger.connection().execute(
            "SELECT COUNT(*) FROM job_admissions "
            "WHERE session_id = ? AND state = 'live'", ("p1",),
        ).fetchone()[0] == 0

        # The session is admissible again.
        vanish = False
        from openprogram.agent.sub_agent_run import AgentTurnResult
        monkeypatch.setattr(
            "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
            staticmethod(lambda **_kwargs: AgentTurnResult(final_text="ok")),
        )
        next_id = runner.spawn_job(
            session_id="p1", prompt="next", agent_id="main", parent_msg_id="a1",
        )
        assert runner.await_job(next_id, timeout=3).status == JobStatus.COMPLETED
    finally:
        runner.shutdown(wait=False)

def test_borrowed_child_runtime_budget_cancels_child_and_cleans_runtime(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    child_entered = threading.Event()
    release_child = threading.Event()
    cancel_intent_staged = threading.Event()
    release_cancel_signal = threading.Event()
    concurrent_cancel_done = threading.Event()
    child_ids: list[str] = []
    concurrent_cancel_errors: list[BaseException] = []
    concurrent_cancel: threading.Thread | None = None

    def limits(_session_id, job):
        seconds = 1 if job.prompt == "child-runtime" else 10
        return resolve_resource_limits(
            ResourceLimits(max_runtime_seconds=seconds), scheduler_capacity=1,
        )

    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-runtime.db"), limit_resolver=limits,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        if prompt == "parent-runtime":
            child = run_agent_turn(
                session_id=session_id,
                prompt="child-runtime",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(
                head_id="parent-head", final_text=child.error or "child ended",
            )
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        while not release_child.wait(0.01):
            if run_control.is_cancelled(session_id, execution_id=child_id):
                return AgentTurnResult(failed=True, error="stopped")
        return AgentTurnResult(head_id="child-head", final_text="released")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )

    original_request_cancel = runner._execution_control.request_cancel

    async def observe_canonical_cancel(**kwargs):
        dispatch = await original_request_cancel(**kwargs)
        if (
            child_ids
            and kwargs.get("execution_id") == child_ids[0]
            and kwargs.get("reason_code") == "budget.runtime_exhausted"
            and not cancel_intent_staged.is_set()
        ):
            # The canonical command is durable before the competing user
            # cancellation is released.  Both callers use the same command
            # identity and must converge on the first reason.
            cancel_intent_staged.set()
            release_cancel_signal.wait(2.0)
        return dispatch

    monkeypatch.setattr(
        runner._execution_control, "request_cancel", observe_canonical_cancel,
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent-runtime", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        runtime_row = runner._governor.ledger.connection().execute(
            "SELECT state, owner_instance_id, lease_generation, started_at, "
            "lease_expires_at FROM job_admissions WHERE job_id = ?",
            (child_id,),
        ).fetchone()
        assert runtime_row[0] == "queued"
        assert runtime_row[1] == runner._instance_id
        assert runtime_row[2] > 0
        assert runtime_row[3] is not None
        assert runtime_row[4] is not None
        with runner._lock:
            assert runner._jobs[child_id]["time_limits"] == (1, None)
            assert runner._jobs[child_id]["lease_generation"] == runtime_row[2]
        live_count = runner._governor.ledger.connection().execute(
            "SELECT COUNT(*) FROM job_admissions "
            "WHERE state IN ('live','stopping')",
        ).fetchone()[0]
        assert live_count == 1
        clock.advance(1.1)
        assert cancel_intent_staged.wait(1.0)
        staged_child = runner.get_job(child_id)
        assert staged_child is not None
        # The canonical command is durable first; the JobStore reason is
        # projected only when the worker reaches its terminal transition.
        assert staged_child.reason_code is None
        canonical = runner._execution_store.get_execution(child_id)
        assert canonical is not None
        assert canonical.status.value == "cancelling"

        def cancel_again_as_user() -> None:
            try:
                runner.cancel_execution(child_id, reason="concurrent user cancel")
            except BaseException as exc:  # noqa: BLE001
                concurrent_cancel_errors.append(exc)
            finally:
                concurrent_cancel_done.set()

        concurrent_cancel = threading.Thread(target=cancel_again_as_user)
        concurrent_cancel.start()
        release_cancel_signal.set()
        assert concurrent_cancel_done.wait(2.0)
        concurrent_cancel.join(timeout=1.0)
        assert not concurrent_cancel.is_alive()
        assert concurrent_cancel_errors == []
        assert wait_until(
            lambda: (
                (child := runner.get_job(child_id)) is not None
                and child.status == JobStatus.CANCELLED
            ),
        )
        child = runner.get_job(child_id)
        assert child is not None
        assert child.status == JobStatus.CANCELLED
        assert child.reason_code == "budget.runtime_exhausted"
        assert runner.await_job(parent_id, timeout=2.0).status == JobStatus.COMPLETED
        with runner._lock:
            assert child_id not in runner._jobs
            assert child_id not in runner._done_events
        assert run_control.current_token("p1", execution_id=child_id) is None
        commands = runner._execution_store.list_commands(child_id)
        assert [command.kind.value for command in commands] == [
            "execution.cancel",
        ]
        assert commands[0].payload["reason_code"] == "budget.runtime_exhausted"
        assert len(child_ids) == 1
        row = runner._governor.ledger.connection().execute(
            "SELECT state, reason_code FROM job_admissions WHERE job_id = ?",
            (child_id,),
        ).fetchone()
        assert row[0] == "released"
        assert row[1] == "budget.runtime_exhausted"
    finally:
        release_cancel_signal.set()
        release_child.set()
        if concurrent_cancel is not None:
            concurrent_cancel.join(timeout=2.0)
        runner.shutdown(wait=False)

def test_borrowed_child_activity_refreshes_child_and_parent_idle(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    budget_polled = threading.Event()

    def observed_clock():
        budget_polled.set()
        return clock()

    child_entered = threading.Event()
    record_activity = threading.Event()
    activity_recorded = threading.Event()
    release_child = threading.Event()
    child_ids: list[str] = []
    activity_results: list[bool] = []
    resolved = resolve_resource_limits(
        ResourceLimits(max_runtime_seconds=10, idle_timeout_seconds=1),
        scheduler_capacity=1,
    )
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-idle.db"),
            limit_resolver=lambda _sid, _job: resolved,
        ),
        monotonic_clock=observed_clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        if prompt == "parent-idle":
            run_agent_turn(
                session_id=session_id,
                prompt="child-idle",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        while not release_child.wait(0.01):
            if record_activity.is_set() and not activity_recorded.is_set():
                from openprogram.agent.job.runner import (
                    record_current_job_activity,
                )
                activity_results.append(
                    record_current_job_activity("provider_data")
                )
                activity_recorded.set()
        return AgentTurnResult(head_id="child-head", final_text="child done")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent-idle", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        clock.advance(0.75)
        record_activity.set()
        assert activity_recorded.wait(1.0)
        assert activity_results == [True]
        activity_rows = runner._governor.ledger.connection().execute(
            "SELECT job_id, last_activity_at FROM job_admissions "
            "WHERE job_id IN (?, ?)",
            (child_id, parent_id),
        ).fetchall()
        assert len(activity_rows) == 2
        assert activity_rows[0][1] == activity_rows[1][1]
        budget_polled.clear()
        clock.advance(0.5)
        assert budget_polled.wait(1.0)
        assert runner._execution_store.get_execution(child_id).status.value == "running"
        assert runner._execution_store.get_execution(parent_id).status.value == "running"
        with runner._lock:
            assert runner._jobs[child_id]["last_activity_monotonic"] == 0.75
            assert runner._jobs[parent_id]["last_activity_monotonic"] == 0.75
        release_child.set()
        assert runner.await_job(parent_id, timeout=2.0).status == JobStatus.COMPLETED
    finally:
        release_child.set()
        runner.shutdown(wait=False)

def test_borrowed_child_timeout_uses_child_and_ancestor_remaining_budget(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor, ResourceLimits, resolve_resource_limits,
    )
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import (
        JobRunner, current_job_operation_timeout,
    )
    from openprogram.usage.ledger import UsageLedger

    clock = _FakeMonotonic()
    observed: list[float | None] = []

    def limits(_session_id, job):
        seconds = 8 if job.prompt == "child-timeout" else 10
        return resolve_resource_limits(
            ResourceLimits(max_runtime_seconds=seconds), scheduler_capacity=1,
        )

    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(
            UsageLedger(tmp_path / "borrow-timeout.db"), limit_resolver=limits,
        ),
        monotonic_clock=clock,
        budget_poll_seconds=0.01,
    )
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        if prompt == "parent-timeout":
            clock.advance(7.0)
            run_agent_turn(
                session_id=session_id,
                prompt="child-timeout",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        observed.append(current_job_operation_timeout(None))
        clock.advance(2.0)
        observed.append(current_job_operation_timeout(5.0))
        return AgentTurnResult(head_id="child-head", final_text="done")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent-timeout", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        runner.await_job(parent_id, timeout=2.0)
        assert observed == [3.0, 1.0]
    finally:
        runner.shutdown(wait=False)

def test_explicit_cancel_of_borrowed_child_is_job_keyed_and_cleans_up(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent import run_control
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.agent.sub_agent_run import AgentTurnResult, run_agent_turn
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.types import JobStatus
    from openprogram.usage.ledger import UsageLedger

    child_entered = threading.Event()
    release_child = threading.Event()
    child_ids: list[str] = []
    runner = JobRunner(
        max_workers=1,
        governor=ResourceGovernor(UsageLedger(tmp_path / "borrow-cancel.db")),
    )
    monkeypatch.setattr("openprogram.agent.job.get_runner", lambda: runner)

    def fake_execute(*, request, **_kwargs):
        session_id = request.session_id
        prompt = request.user_text
        agent_id = request.agent_id
        if prompt == "parent-cancel":
            run_agent_turn(
                session_id=session_id,
                prompt="child-cancel",
                agent_id=agent_id,
                branch_from="a1",
                caller_msg_id="a1",
                chain_generations=1,
            )
            return AgentTurnResult(head_id="parent-head", final_text="done")
        child_id = run_control.get_current_execution_id()
        assert child_id is not None
        child_ids.append(child_id)
        child_entered.set()
        release_child.wait(2.0)
        return AgentTurnResult(head_id="child-head", final_text="late")

    monkeypatch.setattr(
        "openprogram.agent.production_driver.AgentProductionDriver._default_turn_runner",
        staticmethod(fake_execute),
    )
    parent_id = runner.spawn_job(
        session_id="p1", prompt="parent-cancel", agent_id="main",
        parent_msg_id="a1",
    )
    try:
        assert child_entered.wait(1.0)
        child_id = child_ids[0]
        assert child_id != parent_id
        child_token = run_control.current_token("p1", execution_id=child_id)
        parent_token = run_control.current_token("p1", execution_id=parent_id)
        assert child_token is not None
        assert parent_token is not None
        runner.cancel_execution(child_id, reason="cancel child only")
        assert child_token.event.is_set()
        assert not parent_token.event.is_set()
        release_child.set()
        assert runner.await_job(parent_id, timeout=2.0).status == JobStatus.COMPLETED
        child = runner.get_job(child_id)
        assert child.status == JobStatus.CANCELLED
        assert child.reason_code == "cancel.user"
        with runner._lock:
            assert child_id not in runner._jobs
            assert child_id not in runner._done_events
        assert run_control.current_token("p1", execution_id=child_id) is None
    finally:
        release_child.set()
        runner.shutdown(wait=False)

def test_runner_spawn_persists_durable_admission(
    store_fixture, fake_worker, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import ResourceGovernor, ResourceLimits, resolve_resource_limits
    from openprogram.agent.job.runner import JobRunner
    from openprogram.usage.ledger import UsageLedger

    resolved = resolve_resource_limits(ResourceLimits(), scheduler_capacity=1)
    ledger = UsageLedger(tmp_path / "governance.db")
    governor = ResourceGovernor(ledger, limit_resolver=lambda _sid, _job: resolved)
    renewals = []
    original_renew = governor.renew_lease

    def record_renewal(*args, **kwargs):
        renewals.append(args[0])
        return original_renew(*args, **kwargs)

    monkeypatch.setattr(governor, "renew_lease", record_renewal)
    monkeypatch.setattr("openprogram.agent.job.runner._LEASE_RENEW_SECS", 0.02)
    runner = JobRunner(
        max_workers=1,
        governor=governor,
    )
    try:
        tid = runner.spawn_job(
            session_id="p1", prompt="governed", agent_id="main", parent_msg_id="a1",
        )
        job = runner.get_job(tid)
        assert job is not None
        assert job.admission_id
        assert job.budget_scope_id
        assert job.effective_limits["max_live_per_session"] == 1
        assert fake_worker[3].wait(2)
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (tid,),
        ).fetchone()[0] == "live"
        assert wait_until(lambda: bool(renewals))
        assert renewals and set(renewals) == {tid}
        fake_worker[1].set()
        assert runner.await_job(tid, timeout=5).status.value == "completed"
        assert ledger.connection().execute(
            "SELECT state FROM job_admissions WHERE job_id = ?", (tid,),
        ).fetchone()[0] == "released"
    finally:
        fake_worker[1].set()
        runner.shutdown()

def test_spawned_child_resource_view_uses_persisted_ancestor_limits(
    store_fixture, monkeypatch, tmp_path,
):
    monkeypatch.setattr(
        "openprogram.agent.job.runner._broadcast", lambda *a, **k: None,
    )
    from openprogram.agent.resource_governance import (
        ResourceGovernor,
        ResourceLimits,
        resolve_resource_limits,
    )
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.job.store import load_job
    from openprogram.usage.ledger import UsageLedger

    shared = ResourceLimits(
        max_total_tokens=1_000,
        max_cost_usd="10.00",
        max_runtime_seconds=100,
        idle_timeout_seconds=50,
    )
    session_limits = resolve_resource_limits(shared, scheduler_capacity=1)
    parent_limits = resolve_resource_limits(
        shared,
        job=ResourceLimits(
            max_total_tokens=100,
            max_cost_usd="1.00",
            max_runtime_seconds=10,
            idle_timeout_seconds=5,
        ),
        scheduler_capacity=1,
    )

    def limits(_session_id, job):
        return parent_limits if job.prompt == "parent-limited" else session_limits

    governor = ResourceGovernor(
        UsageLedger(tmp_path / "ancestor-view.db"),
        limit_resolver=limits,
        session_limit_resolver=lambda _session_id: session_limits,
    )
    runner = JobRunner(max_workers=1, governor=governor)
    try:
        parent_id = runner.spawn_job(
            session_id="p1",
            prompt="parent-limited",
            agent_id="main",
            parent_msg_id="a1",
            defer_dispatch=True,
        )
        child_id = runner.spawn_job(
            session_id="p1",
            prompt="child",
            agent_id="main",
            parent_msg_id="a1",
            parent_job_id=parent_id,
            defer_dispatch=True,
        )

        child = load_job("p1", child_id)
        assert child is not None
        view = runner.get_job_resource_view(child_id)
        assert view is not None
        expected = {
            "max_total_tokens": 100,
            "max_cost_usd": "1.000000",
            "max_runtime_seconds": 10,
            "idle_timeout_seconds": 5,
        }
        for name, value in expected.items():
            assert child.resolved_limits_snapshot["limits"][name] == {
                "configured": value,
                "effective": value,
                "source": "parent",
            }
            assert view.resource["limits"]["limits"][name] == {
                "configured": value,
                "effective": value,
                "source": "parent",
            }
        assert governor.job_time_limits(child_id) == (10, 5)
        assert governor.reserve_tokens(child_id, 101).reason_code == (
            "quota.token_exhausted"
        )
        assert governor.reserve_cost(
            child_id, 1_000_001, price_known=True,
        ).reason_code == "quota.cost_exhausted"
    finally:
        runner.shutdown()
