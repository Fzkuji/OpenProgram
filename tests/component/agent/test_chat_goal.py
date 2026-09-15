from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from openprogram.agent.session_db import SessionDB
    from openprogram.execution import ExecutionStore, AttemptStore
    from openprogram.execution.driver import DriverRegistry
    from openprogram.execution.control import RuntimeControlService
    import openprogram.programs.workflow.goal as goals
    from openprogram.programs.workflow.goal import chat
    db = SessionDB(tmp_path / "sessions")
    db.create_session("goal-chat", "main")
    store = ExecutionStore(tmp_path / "executions.db")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.execution.default_control_service", lambda: control)
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a, **k: None)
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"total_tokens": 0, "cost_usd": 0, "cost_known": True})
    chat.create("goal-chat", "finish the task")
    yield goals, chat, store
    db.close()


@pytest.mark.parametrize("failure", [None, "notification", "admission", "checkpoint"])
@pytest.mark.parametrize("source", ["web", "tui"])
def test_completed_chat_continues_once_with_original_authority(runtime, monkeypatch, failure, source):
    from openprogram.agent.production_driver import CanonicalAgentAdapter
    from openprogram.agent.dispatcher.types import TurnRequest
    goals, chat, store = runtime
    goal = goals.load_goal("goal-chat")
    goal["turns_used"] = 150
    goals.save_goal("goal-chat", goal)
    calls = []
    finished = threading.Event()
    real_notify = chat.after_terminal
    failures = [failure == "notification"]
    def notify(*args):
        if failures and failures.pop():
            raise goals.GoalConflictError("simulated concurrent Goal mutation")
        return real_notify(*args)
    monkeypatch.setattr(chat, "after_terminal", notify)
    real_admit = chat.admit
    admission_failures = [failure == "admission"]
    def admit(entry, **kwargs):
        if kwargs["turn_payload"].get("request", {}).get("goal_trigger") and admission_failures and admission_failures.pop():
            raise goals.GoalConflictError("simulated temporary admission lock contention")
        return real_admit(entry, **kwargs)
    monkeypatch.setattr(chat, "admit", admit)
    real_publish = chat.publish
    checkpoint_failures = [failure == "checkpoint"]
    def publish(sid, goal):
        if goal.get("phase") == "working" and goal.get("active_started_at") is None and checkpoint_failures and checkpoint_failures.pop():
            raise goals.GoalConflictError("simulated elapsed checkpoint failure")
        return real_publish(sid, goal)
    monkeypatch.setattr(chat, "publish", publish)

    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) == 2:
            chat.update(request.session_id, "complete", expected=chat.current_identity())
        return SimpleNamespace(failed=False)

    real_activate = CanonicalAgentAdapter.activate
    async def activate(self, admission, **kwargs):
        result = await real_activate(self, admission, **kwargs)
        goal = goals.load_goal("goal-chat")
        if goal["status"] == "achieved" and goal.get("accounted_execution_id") == admission.execution_id == goal["execution_id"]:
            finished.set()
        return result
    monkeypatch.setattr(CanonicalAgentAdapter, "activate", activate)

    # Keep the real admission, driver, control and continuation; replace only
    # the provider-facing turn runner for the automatically admitted turn.
    monkeypatch.setattr("openprogram.agent.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=runner, **kw))
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    authority = {"speaker_kind": "owner", "speaker_id": "owner/local",
                 "principal_id": "owner/install/0123456789abcdef", "authority_tier": "owner",
                 "interaction": "interactive"}
    request = TurnRequest("goal-chat", "work", "main", source, permission_mode="ask",
                          tools_override=["read", "update_goal"], **authority)
    admission = adapter.admit(request, trusted_actor=authority, user_message_id="u1",
                              assistant_message_id="u1_reply", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    assert finished.wait(5), goals.load_goal("goal-chat")
    assert len(calls) == 2
    assert calls[1].tools_override == ["read", "update_goal"]
    assert calls[1].principal_id == authority["principal_id"]
    assert calls[1].source == source
    assert calls[1].goal_trigger is True
    from openprogram.agent.run_control import current_token
    from openprogram.execution.model import ExecutionStatus
    assert store.get_execution(admission.execution_id).status == ExecutionStatus.COMPLETED
    assert current_token("goal-chat", execution_id=admission.execution_id) is None
    chat.after_terminal(store, store.get_execution(admission.execution_id))
    assert len(calls) == 2


def test_edit_keeps_terminal_usage_without_continuing_old_revision(runtime, monkeypatch):
    from openprogram.agent.production_driver import CanonicalAgentAdapter
    from openprogram.agent.dispatcher.types import TurnRequest
    goals, chat, store = runtime
    total = [0]
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"total_tokens": total[0], "cost_usd": 0, "cost_known": True})
    monkeypatch.setattr(goals, "request_goal_stop", lambda *a: None)
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **k: continuations.append(a))
    def runner(**kw):
        total[0] = 40
        goals.apply_goal_action("goal-chat", "edit", prompt="revised task")
        total[0] = 50
        return SimpleNamespace(failed=False)
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="u1", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    goal = goals.load_goal("goal-chat")
    assert goal["usage"]["total_tokens"] == 50
    assert goal["status"] == "paused"
    assert not continuations
    assert chat.resume("goal-chat")["usage"]["total_tokens"] == 50


def test_failed_chat_stops_goal_without_continuation(runtime, monkeypatch):
    from openprogram.agent.production_driver import CanonicalAgentAdapter
    from openprogram.agent.dispatcher.types import TurnRequest
    goals, chat, store = runtime
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **k: continuations.append(a))
    adapter = CanonicalAgentAdapter(turn_runner=lambda **kw: SimpleNamespace(failed=True))
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="u1", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    assert goals.load_goal("goal-chat")["status"] == "paused_recoverable"
    assert not continuations
