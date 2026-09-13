"""Automatic restart uses real Agent checkpoints and a durable deadline."""

from tests.component.agent.execution.test_agent_continuation_real import real_agent_chat, _chat, _wait


def test_ordinary_provider_decision_publishes_checkpoint(real_agent_chat):
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    h.provider.add_response(ScriptedToolCall("first", {}, "first-call"))
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    try:
        _wait(lambda: "first" in h.tools.calls)
        current = h.store.get_execution(execution.execution_id)
        assert current.checkpoint_head_id is not None
        assert current.status.value == "running"
    finally:
        h.tools.release["first"].set()


import pytest
from types import SimpleNamespace


def _runner(h):
    return SimpleNamespace(_execution_store=h.store, _execution_control=h.control)


@pytest.mark.parametrize("elapsed, resumes", [(60, True), (7200, True), (7201, False)])
def test_orderly_restart_has_fixed_two_hour_window(
    real_agent_chat, monkeypatch, elapsed, resumes
):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    h.provider.add_response(
        ScriptedToolCall("first", {}, "first-call"),
        ScriptedToolCall("second", {}, "second-call"),
    )
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    _wait(lambda: "first" in h.tools.calls)
    runner = _runner(h)
    restart.prepare_shutdown(runner)
    h.tools.release["first"].set()
    _wait(
        lambda: h.store.get_execution(execution.execution_id).status.value == "paused"
    )
    _wait(lambda: bool(h.outcomes))
    intent = next(
        e
        for e in h.store.list_events(execution.execution_id)
        if e.kind == restart._REQUEST
    )
    assert intent.payload["resume_before"] - intent.payload["interrupted_at"] == 7200
    monkeypatch.setattr(
        restart, "time", lambda: intent.payload["interrupted_at"] + elapsed
    )
    restart.reconcile(runner)
    assert h.store.get_execution(execution.execution_id).status.value == "paused"
    assert h.tools.calls == ["first"]
    runner = _runner(h)
    restart.reconcile(runner)
    restart.reconcile(runner)
    if resumes:
        _wait(
            lambda: (
                h.store.get_execution(execution.execution_id).status.value
                == "completed"
            ),
            detail=lambda: h.activation_errors,
        )
        assert h.tools.calls == ["first", "second"]
        assert h.provider.call_count == 2
    else:
        assert h.store.get_execution(execution.execution_id).status.value == "paused"
        assert h.tools.calls == ["first"]
        assert h.provider.call_count == 1
        # Another startup never reopens the expired interval.
        restart.reconcile(_runner(h))
        assert h.store.get_execution(execution.execution_id).status.value == "paused"


def test_abandoned_safe_checkpoint_resumes_without_repeating_provider(
    real_agent_chat, monkeypatch
):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    h.provider.add_response(ScriptedToolCall("first", {}, "first-call"))
    h.provider.add_response(ScriptedText("done"))
    original = h.control.commit_agent_safe_point
    recovered = []
    errors = []

    def crash_after_commit(**kwargs):
        result = original(**kwargs)
        if not recovered:
            try:
                recovered.append(
                    h.control.recover_owner_loss(
                        kwargs["execution_id"],
                        attempt_id=kwargs["attempt_id"],
                        generation=kwargs["generation"],
                    )
                )
            except Exception as exc:
                errors.append(repr(exc))
                raise
        return result

    monkeypatch.setattr(h.control, "commit_agent_safe_point", crash_after_commit)
    execution = _chat(h)
    _wait(lambda: bool(h.outcomes), detail=lambda: h.activation_errors)
    assert recovered, errors
    assert recovered[0].execution.status.value == "paused"
    assert h.tools.calls == []
    restart.reconcile(_runner(h))
    _wait(
        lambda: (
            h.store.get_execution(execution.execution_id).status.value == "completed"
        ),
        detail=lambda: h.activation_errors,
    )
    assert h.tools.calls == ["first"]
    assert h.provider.call_count == 2


@pytest.mark.parametrize("crash", [False, True])
def test_job_restart_reacquires_resources_and_completes(
    real_agent_chat, tmp_path, monkeypatch, crash
):
    from openprogram.execution import restart
    from openprogram.agent.job.runner import JobRunner
    from openprogram.agent.resource_governance import ResourceGovernor
    from openprogram.usage.ledger import UsageLedger
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    monkeypatch.setattr("openprogram.execution.store.default_store", lambda: h.store)
    monkeypatch.setattr("openprogram.paths.get_execution_db_path", lambda: h.store.path)
    monkeypatch.setattr("openprogram.worker.lock.is_held_by", lambda _pid: True)
    import openprogram.providers.api_registry as api_registry

    monkeypatch.setattr(h.model, "api", "openai-completions")
    monkeypatch.setitem(api_registry._registry, h.model.api, h.provider)
    monkeypatch.setitem(api_registry._original_registry, h.model.api, h.provider)
    # This deterministic provider reports usage; keep real reservation enforcement.
    monkeypatch.setattr(
        "openprogram.providers.api_registry.has_audited_accounting",
        lambda provider, api: provider is h.provider and api == h.model.api,
    )
    h.provider.add_response(
        ScriptedToolCall("first", {}, "first-call"),
        ScriptedToolCall("second", {}, "second-call"),
    )
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.update({"first", "second"})
    ledger = UsageLedger(tmp_path / "usage.sqlite3")
    runner = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
    restarted = None
    if crash:
        original = runner._execution_control.commit_agent_safe_point
        recovered = []

        def abandon_after_commit(**kwargs):
            result = original(**kwargs)
            if not recovered:
                runner._shutdown_event.set()
                with ledger.immediate() as connection:
                    connection.execute(
                        "UPDATE job_admissions SET lease_expires_at=0 WHERE job_id=?",
                        (kwargs["execution_id"],),
                    )
                monkeypatch.setattr(
                    runner, "_owner_holds_worker_lock", lambda _owner: False
                )
                runner._reconcile_resources()
                recovered.append(h.store.get_execution(kwargs["execution_id"]))
            return result

        monkeypatch.setattr(
            runner._execution_control, "commit_agent_safe_point", abandon_after_commit
        )
    try:
        job_id = runner.spawn_job(
            session_id=h.session_id, prompt="continue safely", agent_id="main"
        )
        if crash:
            _wait(lambda: bool(recovered))
            assert recovered[0].status.value == "paused"
            assert runner.get_job(job_id).status.value != "errored"
            assert h.tools.calls == []
            h.tools.blocked.clear()
        else:
            _wait(lambda: "first" in h.tools.calls)
            restart.prepare_shutdown(runner)
            h.tools.release["first"].set()
            _wait(lambda: h.store.get_execution(job_id).status.value == "paused")
        runner.shutdown()
        restarted = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
        restart.reconcile(restarted)
        if not crash:
            _wait(lambda: "second" in h.tools.calls)
            restart.prepare_shutdown(restarted)
            h.tools.release["second"].set()
            _wait(lambda: h.store.get_execution(job_id).status.value == "paused")
            restarted.shutdown()
            restarted = JobRunner(max_workers=1, governor=ResourceGovernor(ledger))
            restart.reconcile(restarted)
        _wait(
            lambda: h.store.get_execution(job_id).status.value == "completed",
            timeout=8,
            detail=lambda: (
                h.store.get_execution(job_id),
                [
                    dict(r)
                    for r in ledger.connection().execute("SELECT * FROM job_admissions")
                ],
                h.activation_errors,
                [getattr(o, "error", repr(o)) for o in h.outcomes],
            ),
        )
        assert h.tools.calls == ["first", "second"]
        assert h.provider.call_count == 2
        _wait(lambda: restarted.get_job(job_id).status.value == "completed")
    finally:
        for gate in h.tools.release.values():
            gate.set()
        (restarted or runner).shutdown()


@pytest.mark.parametrize(
    "action, expected",
    [("execution.pause", "paused"), ("execution.cancel", "cancelled")],
)
def test_explicit_user_control_is_not_automatically_resumed(
    real_agent_chat, action, expected
):
    from openprogram.execution import restart
    from tests.component.agent.execution.test_agent_continuation_real import _command
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    h.provider.add_response(ScriptedText("done"))
    h.provider.block_calls.add(0)
    execution = _chat(h)
    _wait(h.provider.entered.is_set)
    _command(h, action, h.store.get_execution(execution.execution_id), "user-control")
    restart.prepare_shutdown(_runner(h))
    h.provider.release.set()
    _wait(
        lambda: h.store.get_execution(execution.execution_id).status.value == expected
    )
    restart.reconcile(_runner(h))
    assert h.store.get_execution(execution.execution_id).status.value == expected
    assert not any(
        e.kind == restart._REQUEST for e in h.store.list_events(execution.execution_id)
    )


def test_unconfirmed_tool_effect_is_not_replayed(real_agent_chat):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import ScriptedToolCall

    h = real_agent_chat
    h.provider.add_response(ScriptedToolCall("first", {}, "first-call"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    _wait(lambda: "first" in h.tools.calls)
    current = h.store.get_execution(execution.execution_id)
    try:
        recovered = h.control.recover_owner_loss(
            current.execution_id,
            attempt_id=current.current_attempt_id,
            generation=current.owner_lease["generation"],
        )
        assert recovered.execution.status.value == "reconciliation_required"
        restart.reconcile(_runner(h))
        assert h.tools.calls == ["first"]
        assert h.provider.call_count == 1
    finally:
        h.tools.release["first"].set()


def test_disabled_restart_window_does_not_request_pause(real_agent_chat, monkeypatch):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    monkeypatch.setattr(
        "openprogram.setup._read_config",
        lambda: {"execution": {"auto_resume_window_seconds": 0}},
    )
    h.provider.add_response(ScriptedToolCall("first", {}, "first-call"))
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    try:
        _wait(lambda: "first" in h.tools.calls)
        restart.prepare_shutdown(_runner(h))
        current = h.store.get_execution(execution.execution_id)
        assert current.status.value == "running"
        assert current.checkpoint_head_id is None
        assert not any(
            e.kind == restart._REQUEST
            for e in h.store.list_events(execution.execution_id)
        )
    finally:
        h.tools.release["first"].set()


def test_second_interruption_before_first_new_effect(real_agent_chat):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    h.provider.add_response(
        ScriptedToolCall("first", {}, "first-call"),
        ScriptedToolCall("second", {}, "second-call"),
    )
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.add("first")
    execution = _chat(h)
    _wait(lambda: "first" in h.tools.calls)
    restart.prepare_shutdown(_runner(h))
    h.tools.release["first"].set()
    _wait(
        lambda: h.store.get_execution(execution.execution_id).status.value == "paused"
    )
    _wait(lambda: bool(h.outcomes))
    recovered = []

    async def crash_before_driver(attempt, activation):
        recovered.append(
            h.control.recover_owner_loss(
                attempt.execution_id,
                attempt_id=attempt.attempt_id,
                generation=attempt.generation,
            )
        )
        raise RuntimeError("simulated process interruption before driver activation")

    original_activator = h.control.activator
    h.control.activator = crash_before_driver
    restart.reconcile(_runner(h))
    assert recovered
    assert recovered[0].execution.status.value == "paused"
    h.control.activator = original_activator
    restart.reconcile(_runner(h))
    _wait(
        lambda: (
            h.store.get_execution(execution.execution_id).status.value == "completed"
        )
    )
    assert h.tools.calls == ["first", "second"]
    assert h.provider.call_count == 2


def test_shutdown_pause_race_with_completed_action(real_agent_chat, monkeypatch):
    from openprogram.execution import restart
    from tests.component.providers.scripted_provider import (
        ScriptedToolCall,
        ScriptedText,
    )

    h = real_agent_chat
    h.provider.add_response(
        ScriptedToolCall("first", {}, "first-call"),
        ScriptedToolCall("second", {}, "second-call"),
    )
    h.provider.add_response(ScriptedText("done"))
    h.tools.blocked.update({"first", "second"})
    execution = _chat(h)
    _wait(lambda: "first" in h.tools.calls)
    original = h.control.request_pause
    calls = []

    async def pause_after_checkpoint(**kwargs):
        if not calls:
            calls.append(1)
            h.tools.release["first"].set()
            _wait(lambda: "second" in h.tools.calls)
        return await original(**kwargs)

    monkeypatch.setattr(h.control, "request_pause", pause_after_checkpoint)
    try:
        restart.prepare_shutdown(_runner(h))
        current = h.store.get_execution(execution.execution_id)
        assert current.status.value == "pausing", h.store.list_commands(
            execution.execution_id
        )
    finally:
        h.tools.release["second"].set()

    _wait(
        lambda: h.store.get_execution(execution.execution_id).status.value == "paused"
    )
    _wait(lambda: bool(h.outcomes))
    requests = [
        e
        for e in h.store.list_events(execution.execution_id)
        if e.kind == restart._REQUEST
    ]
    assert len(requests) == 1
    assert (
        requests[0].payload["resume_before"] - requests[0].payload["interrupted_at"]
        == 7200
    )
    restart.reconcile(_runner(h))
    _wait(
        lambda: (
            h.store.get_execution(execution.execution_id).status.value == "completed"
        )
    )
    assert h.tools.calls == ["first", "second"]
    assert h.provider.call_count == 2
