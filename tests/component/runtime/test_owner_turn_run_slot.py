"""A turn the runtime starts on its own takes the conversation's run slot."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.support.thread_scope import fake_threads_started_by


@pytest.fixture
def owner_turn_env(monkeypatch):
    import openprogram.runtime.agent.turn_request as agent_turn_request
    import openprogram.runtime.conversation.owner_turn as owner_turn
    from openprogram.interfaces.server import server as _s

    admitted: list[str] = []
    failed: list[str] = []
    started: list[object] = []

    class Adapter:
        def __init__(self, **_kw):
            pass

        def admit(self, request, **_kw):
            admitted.append(request.session_id)
            return SimpleNamespace(execution_id="e-owner", status_version=1)

        def fail_admission(self, admission, *, reason_code):
            failed.append(reason_code)

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_config.load_session_run_config",
        lambda sid: SimpleNamespace(permission_mode=None, thinking_effort=None,
                                    additional_working_dirs=[]))
    monkeypatch.setattr("openprogram.runtime.permissions.rule.load_merged_rules", lambda sid: None)
    monkeypatch.setattr("openprogram.runtime.permissions.authority.local_owner_authority", lambda: {})
    monkeypatch.setattr(agent_turn_request, "TurnRequest", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(_s, "_emit_running_task_event", lambda *a, **kw: None)

    class Thread:
        def __init__(self, *, target, **_kw):
            self.target = target

        def start(self):
            started.append(self)

    fake_threads_started_by(monkeypatch, owner_turn, Thread)
    return SimpleNamespace(
        owner_turn=owner_turn, server=_s, admitted=admitted, failed=failed, started=started,
    )


def _clear(server, session_id):
    with server._running_tasks_lock:
        server._running_tasks.pop(session_id, None)
    server._unregister_active_runtime(session_id)


def test_owner_turn_is_refused_while_a_chat_turn_holds_the_slot(owner_turn_env):
    from openprogram.runtime.conversation.run_control.run_slot import RunSlotBusy

    env = owner_turn_env
    session_id = "owner-busy"
    _clear(env.server, session_id)
    assert env.server._try_reserve_run(session_id, "typed")
    try:
        with pytest.raises(RunSlotBusy):
            env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake")
    finally:
        _clear(env.server, session_id)

    assert env.admitted == []
    assert env.started == []


def test_owner_turn_holds_the_slot_until_it_ends(owner_turn_env):
    env = owner_turn_env
    session_id = "owner-runs"
    _clear(env.server, session_id)
    try:
        execution_id = env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake")

        assert execution_id == "e-owner"
        assert env.server._is_run_active(session_id)
        assert not env.server._try_reserve_run(session_id, "typed")
        with env.server._running_tasks_lock:
            assert env.server._running_tasks[session_id]["execution_id"] == "e-owner"

        # The turn ends (activation fails here): the slot is free again.
        env.started[0].target()
        assert env.failed == ["owner_turn_activation_failed"]
        assert not env.server._is_run_active(session_id)
    finally:
        _clear(env.server, session_id)


def test_an_owner_turn_stopped_before_it_starts_ends_without_a_result(owner_turn_env, monkeypatch):
    """Activation returning ``None`` (a stop won the race) frees the slot and fails nothing."""
    env = owner_turn_env
    session_id = "owner-stopped"
    done = []

    async def stopped(self, admission):
        return None

    monkeypatch.setattr(
        "openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter.activate",
        stopped, raising=False)
    _clear(env.server, session_id)
    try:
        env.owner_turn.start_owner_turn(session_id, "main", "⏰ wake", on_done=done.append)
        env.started[0].target()
        assert env.failed == []
        assert done == [None]
        assert not env.server._is_run_active(session_id)
    finally:
        _clear(env.server, session_id)
