"""Real Agent update pause and fresh planning after a tool contract change."""

from tests.component.agent.test_agent_continuation_real import (
    real_agent_chat,
    _chat,
    _wait,
)  # noqa: F401


import pytest


@pytest.mark.parametrize("elapsed,enabled", [(60, True), (7201, True), (60, False)])
def test_exact_update_replans(real_agent_chat, tmp_path, monkeypatch, elapsed, enabled):
    from tests.component.providers.scripted_provider import ScriptedText
    from openprogram.self_update import SelfUpdateStore, UpdatePhase, restart
    from openprogram.self_update.maintenance import enter_maintenance, leave_maintenance
    from openprogram.agent.job.runner import JobRunner
    from tests.unit.self_update.test_store import _request

    monkeypatch.setattr("openprogram.worker.lock.is_held_by", lambda _pid: True)
    h = real_agent_chat
    # This scripted provider has deterministic usage and no external billing.
    # Job dispatch requires an audited identity even in an in-process test.
    from openprogram.providers import api_registry, budget

    monkeypatch.setitem(api_registry._audited_accounting, id(h.provider), {h.model.api})
    monkeypatch.setattr(
        budget, "_BYTE_BOUNDED_APIS", budget._BYTE_BOUNDED_APIS | {h.model.api}
    )
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr("openprogram.execution.store.default_store", lambda: h.store)
    monkeypatch.setattr(
        "openprogram.execution.control.default_control_service", lambda: h.control
    )
    monkeypatch.setattr("openprogram.store.default_store", lambda: h.sessions)
    h.provider.add_response(ScriptedText("old answer"))
    h.provider.add_response(ScriptedText("replanned answer"))
    h.provider.block_calls.add(0)
    ex = _chat(h)
    _wait(h.provider.entered.is_set)
    updates = SelfUpdateStore()
    updates.create(_request())
    updates.transition("su_test", UpdatePhase.STAGING)
    updates.transition("su_test", UpdatePhase.READY)
    runner = JobRunner(max_workers=1)
    monkeypatch.setattr("openprogram.agent.job.runner._runner", runner)
    try:
        assert runner._execution_store is h.store
        enter_maintenance("su_test")
        restart.reconcile(runner)
        h.provider.release.set()
        _wait(lambda: h.store.get_execution(ex.execution_id).status.value == "paused")
        h.tools.implementation_variant = "changed"
        updates.transition("su_test", UpdatePhase.ABORTED)
        from openprogram.execution import restart as policy
        stopped = updates.load("su_test").state.updated_at
        monkeypatch.setattr(policy, "time", lambda: stopped + elapsed)
        if not enabled:
            monkeypatch.setattr(policy, "window_seconds", lambda: 0)
        leave_maintenance("su_test")
        restart.reconcile(runner)
        restart.reconcile(runner)
        jid = restart._command_id("su_test", ex.execution_id, "replan")
        if elapsed > 7200 or not enabled:
            assert runner.get_job(jid) is None
            assert h.store.get_execution(ex.execution_id).status.value == "paused"
            assert h.provider.call_count == 1
            return
        assert runner.get_job(jid) is not None
        _wait(
            lambda: h.store.get_execution(jid).status.value in {"completed", "failed"},
            detail=lambda: {
                "execution": h.store.get_execution(jid).status.value,
                "job_error": runner.get_job(jid).error,
                "calls": h.provider.call_count,
                "outcomes": h.outcomes,
                "activation_errors": h.activation_errors,
            },
        )
        assert h.store.get_execution(jid).status.value == "completed"
        assert h.provider.call_count == 2
        restart.reconcile(runner)
        assert h.provider.call_count == 2
        # A later update in the same conversation must still wake its origin.
        from dataclasses import replace
        from openprogram.self_update.continuation import (
            config_evidence,
            reconcile as followup,
        )
        from openprogram.agent.authority import owner_authority, owner_principal_id

        authority = owner_authority(owner_principal_id())
        authority.update(speaker_kind="agent", interaction="background")
        config = dict(
            schema=1,
            agent_id=ex.agent_id if hasattr(ex, "agent_id") else "main",
            authority=authority,
            profile_snapshot={},
            model_override=None,
            tools_override=[],
            permission=dict(mode="ask", rules=None),
        )
        source = h.store.get_execution_input(ex.execution_id)
        req = replace(
            _request(),
            update_id="su_second",
            session_id=ex.session_id,
            origin_assistant_id=source.assistant_message_id,
            agent_id=config["agent_id"],
            pre_update_evidence=(config_evidence(config),),
        )
        import time
        monkeypatch.setattr(policy, "time", time.time)
        updates.create(req, continuation_config=config)
        updates.transition("su_second", UpdatePhase.ABORTED)
        followup(runner)
        assert runner.get_job("self-update:su_second:continue:1") is not None
    finally:
        runner.shutdown()
