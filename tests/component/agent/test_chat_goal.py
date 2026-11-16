from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    from openprogram.runtime.context.session_db import SessionDB
    from openprogram.runtime.execution import ExecutionStore, AttemptStore
    from openprogram.runtime.execution.driver import DriverRegistry
    from openprogram.runtime.execution.control import RuntimeControlService
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    db = SessionDB(tmp_path / "sessions")
    db.create_session("goal-chat", "main")
    store = ExecutionStore(tmp_path / "executions.db")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr(goals, "_db", lambda: db)
    monkeypatch.setattr(goals, "_emit_goal_update", lambda *a, **k: None)
    monkeypatch.setattr(goals, "goal_usage", lambda *a: {"total_tokens": 0, "cost_usd": 0, "cost_known": True})
    chat.create("goal-chat", "finish the task")
    yield goals, chat, store
    db.close()


@pytest.mark.parametrize("failure", [None, "notification", "admission", "checkpoint"])
@pytest.mark.parametrize("source", ["web", "tui"])
def test_completed_chat_continues_once_with_original_authority(runtime, monkeypatch, failure, source):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    # Disabling restart recovery must not disable live, same-process turns.
    monkeypatch.setattr("openprogram.runtime.execution.recovery.restart.window_seconds", lambda: 0)
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
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)

    real_activate = CanonicalAgentAdapter.activate
    async def activate(self, admission, **kwargs):
        result = await real_activate(self, admission, **kwargs)
        goal = goals.load_goal("goal-chat")
        if goal["status"] == "paused" and goal.get("accounted_execution_id") == admission.execution_id == goal["execution_id"]:
            finished.set()
        return result
    monkeypatch.setattr(CanonicalAgentAdapter, "activate", activate)

    # Keep the real admission, driver, control and continuation; replace only
    # the provider-facing turn runner for the automatically admitted turn.
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
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
    assert finished.wait(30), goals.load_goal("goal-chat")
    assert len(calls) == 2
    assert calls[1].tools_override == ["read", "update_goal"]
    assert calls[1].principal_id == authority["principal_id"]
    assert calls[1].source == source
    assert calls[1].goal_trigger is True
    from openprogram.runtime.execution.run_control import current_token
    from openprogram.runtime.execution.model import ExecutionStatus
    assert store.get_execution(admission.execution_id).status == ExecutionStatus.COMPLETED
    assert current_token("goal-chat", execution_id=admission.execution_id) is None
    chat.after_terminal(store, store.get_execution(admission.execution_id))
    assert len(calls) == 2


@pytest.mark.parametrize("exhausted", [False, True])
def test_terminal_retry_settles_pending_usage_before_continuing(runtime, monkeypatch, exhausted):
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    goal = goals.load_goal("goal-chat")
    goal["execution_id"] = "interrupted-ledger"
    # This compatibility case exercises the pre-request-accounting cursor.
    goal.pop("usage_mode", None)
    goals.reset_goal_usage_cursor("goal-chat", goal)
    goals.save_goal("goal-chat", goal)
    execution = SimpleNamespace(execution_id="interrupted-ledger", session_id="goal-chat",
                                status=ExecutionStatus.COMPLETED)
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _: {"kind": "chat", "request": {"source": "web"}})
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: continuations.append(a))
    monkeypatch.setattr(goals, "goal_usage", lambda *a, **kw: {"available": False})
    with pytest.raises(goals.GoalStateUnavailable):
        chat.after_terminal(store, execution)
    pending = goals.load_goal("goal-chat")["usage_pending_until"]
    assert not continuations
    reads = []
    def recovered(*args, **kwargs):
        reads.append(kwargs.get("until", args[2] if len(args) > 2 else None))
        return {"available": True, "total_tokens": 100, "cost_usd": 1, "cost_known": True}
    monkeypatch.setattr(goals, "goal_usage", recovered)
    monkeypatch.setattr(goals, "budget_exhausted", lambda goal: "cost budget exhausted" if exhausted else None)
    chat.after_terminal(store, execution)
    settled = goals.load_goal("goal-chat")
    assert reads == [pending]
    assert settled["usage"]["total_tokens"] == 100
    assert settled["usage"]["cost_usd"] == 1
    assert "usage_pending_until" not in settled
    assert len(continuations) == (0 if exhausted else 1)
    assert settled["status"] == ("budget_exhausted" if exhausted else "active")


def test_edit_keeps_terminal_usage_without_continuing_old_revision(runtime, monkeypatch):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    # Legacy snapshots keep their cursor semantics until their next request.
    legacy = goals.load_goal("goal-chat")
    legacy.pop("usage_mode", None)
    goals.reset_goal_usage_cursor("goal-chat", legacy)
    goals.save_goal("goal-chat", legacy)
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
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **k: continuations.append(a))
    adapter = CanonicalAgentAdapter(turn_runner=lambda **kw: SimpleNamespace(failed=True))
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="u1", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    assert goals.load_goal("goal-chat")["status"] == "paused_recoverable"
    assert not continuations


@pytest.mark.parametrize("undone_during_the_turn", [False, True])
def test_an_always_on_stop_holds_across_a_real_turn(runtime, monkeypatch, undone_during_the_turn):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    automatic = goals.load_goal("goal-chat")
    automatic["origin"] = "check_in"
    goals.save_goal("goal-chat", automatic)
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **k: continuations.append(a))
    def runner(**kw):
        assert chat.pause_always_on("goal-chat", "conversation_archived")
        if undone_during_the_turn:
            assert chat.restore_always_on("goal-chat")
        return SimpleNamespace(failed=False)
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="u1", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    goal = goals.load_goal("goal-chat")
    if undone_during_the_turn:
        # The turn's end may be replayed; the continuation's admission key makes that one turn.
        assert goal["status"] == "active" and {call[1] for call in continuations} == {goal["execution_id"]}
        return
    assert goal["status"] == "paused_recoverable" and goal["pause_reason"] == "conversation_archived"
    assert not continuations
    assert chat.restore_always_on("goal-chat")
    restored = goals.load_goal("goal-chat")
    assert restored["status"] == "active" and restored["execution_id"] is None
    assert chat.identity(restored) == chat.identity(goal)


def test_an_always_on_stop_reports_a_held_goal_lock(runtime, monkeypatch):
    from openprogram.programs.tools.planning.goal.ownership import goal_owner
    goals, chat, store = runtime
    monkeypatch.setattr(chat, "GOAL_LOCK_WAIT_S", 0.05)
    automatic = goals.load_goal("goal-chat")
    automatic["origin"] = "scheduled"
    goals.save_goal("goal-chat", automatic)
    with goal_owner(goals._db(), "goal-chat") as acquired:
        assert acquired
        with pytest.raises(goals.GoalConflictError):
            chat.pause_always_on("goal-chat", "always_on_off")
    assert goals.load_goal("goal-chat")["status"] == "active"
    assert chat.pause_always_on("goal-chat", "always_on_off")


@pytest.mark.parametrize("action", ["continue", "pause", "clear", "disabled", "expired"])
@pytest.mark.parametrize("abrupt", [False, True])
@pytest.mark.parametrize("creation", ["before_turn", "mid_turn", "legacy"])
def test_shutdown_completion_is_durable_and_replays_goal_once(runtime, monkeypatch, action, abrupt, creation):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.execution.recovery import restart
    from openprogram.runtime.execution import AttemptStore
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    goals, chat, store = runtime
    if creation == "mid_turn":
        goals.apply_goal_action("goal-chat", "clear")
    stopping = [False]
    restarted = [False]
    now = [restart.time()]
    monkeypatch.setattr("openprogram.runtime.conversation.run_control.is_worker_stopping", lambda: stopping[0])
    monkeypatch.setattr(restart, "time", lambda: now[0])
    monkeypatch.setattr(restart, "window_seconds", lambda: 7200 if action == "expired" else -1)
    calls = []
    done = threading.Event()
    real_notify = chat.after_terminal

    def notify(executions, execution):
        if abrupt and not restarted[0]:
            raise RuntimeError("process disappeared after terminal commit, before Goal notification")
        result = real_notify(executions, execution)
        goal = goals.load_goal("goal-chat")
        if goal["status"] == "paused" and goal.get("accounted_execution_id") == execution.execution_id:
            done.set()
        return result

    monkeypatch.setattr(chat, "after_terminal", notify)

    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) == 1:
            if creation == "mid_turn":
                chat.create(request.session_id, "finish the task")
            stopping[0] = not abrupt
        else:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)

    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=runner, **kw))
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    request = TurnRequest("goal-chat", "work", "main", "web", permission_mode="ask",
                          tools_override=["read", "update_goal"])
    admission = adapter.admit(request, trusted_actor={}, user_message_id="shutdown-u1",
                              config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    assert store.get_execution(admission.execution_id).status.value == "completed"
    assert goals.load_goal("goal-chat")["status"] == "active"
    assert len(calls) == 1
    if creation == "legacy":
        saved = goals.load_goal("goal-chat")
        saved.pop("continuation_policy", None)
        saved.pop("continuation_restart", None)
        goals.save_goal("goal-chat", saved)

    stopping[0] = False
    restarted[0] = True
    now[0] = store.get_execution(admission.execution_id).terminal_at
    if abrupt:
        from openprogram.runtime.execution import process_owner
        new_process = dict(process_owner.current_process_owner(), instance="restarted-test-process")
        monkeypatch.setattr(process_owner, "current_process_owner", lambda: dict(new_process))
    if action in {"pause", "clear"}:
        goals.apply_goal_action("goal-chat", action)
    elif action == "disabled":
        monkeypatch.setattr(restart, "window_seconds", lambda: 0)
    elif action == "expired":
        now[0] += 7201
    from openprogram.runtime.execution import ExecutionEventEffects
    fresh = ExecutionEventEffects(store)
    fresh.catch_up()
    continued = action == "continue" and creation != "legacy"
    if continued:
        assert done.wait(30), goals.load_goal("goal-chat")
        assert len(calls) == 2
        assert calls[1].tools_override == request.tools_override
        assert calls[1].permission_mode == "ask"
    else:
        assert len(calls) == 1
        assert goals.load_goal("goal-chat")["status"] in {"paused", "paused_recoverable", "cancelled"}
    fresh.catch_up()
    assert len(calls) == (2 if continued else 1)
    # User pause now ends the fixture's work instead of bypassing completion
    # verification. Its cancel receipt may settle after Goal notification.
    if creation == "legacy" and action == "continue":
        # An explicit owner resume upgrades old data and remains usable.
        resumed = chat.resume("goal-chat")
        chat.start_next(store, admission.execution_id, expected=chat.identity(resumed))
        assert done.wait(30), goals.load_goal("goal-chat")
        assert len(calls) == 2


def _start_goal_chat(goals, chat, monkeypatch, runner, *, until):
    """Run the real admission, driver and continuation path; stop when ``until`` holds."""
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    done = threading.Event()
    real_activate = CanonicalAgentAdapter.activate
    async def activate(self, admission, **kwargs):
        result = await real_activate(self, admission, **kwargs)
        if until(goals.load_goal("goal-chat")):
            done.set()
        return result
    monkeypatch.setattr(CanonicalAgentAdapter, "activate", activate)
    monkeypatch.setattr("openprogram.runtime.conversation.production_driver.CanonicalAgentAdapter",
                        lambda **kw: CanonicalAgentAdapter(turn_runner=runner, **kw))
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    request = TurnRequest("goal-chat", "work", "main", "web", permission_mode="ask")
    admission = adapter.admit(request, trusted_actor={}, user_message_id="u1",
                              assistant_message_id="u1_reply", config_snapshot_ref="session:goal-chat")
    asyncio.run(adapter.activate(admission))
    return adapter, done


def test_automatic_turns_without_tools_stop_after_one_warning(runtime, monkeypatch):
    goals, chat, store = runtime
    calls = []
    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) > 4:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    _, done = _start_goal_chat(goals, chat, monkeypatch, runner,
                               until=lambda goal: goal["status"] != "active")
    assert done.wait(30), goals.load_goal("goal-chat")
    goal = goals.load_goal("goal-chat")
    # The user's turn, one idle automatic turn, then the warned one that stops.
    assert len(calls) == 3
    assert calls[0].goal_trigger is not True
    assert chat.IDLE_WARNING not in calls[1].user_text
    assert chat.IDLE_WARNING in calls[2].user_text
    assert goal["status"] == "stalled"
    assert goal["pause_reason"] == "idle"
    assert goal["status"] in goals.RESUMABLE_STATUSES
    assert chat.resume("goal-chat")["idle_turns"] == 0


def test_a_background_exit_during_a_goal_reaches_its_next_turn(runtime, monkeypatch, tmp_path):
    """A running Goal registers its next turn before the last one ends, so the
    conversation is never idle for the notice sweep; the next Goal turn
    carries the notice instead, once."""
    from openprogram.host.processes.store import ProcessStore
    from openprogram.runtime.conversation import process_notices
    goals, chat, store = runtime
    processes = ProcessStore(tmp_path / "processes.db")
    monkeypatch.setattr(process_notices, "ProcessStore", lambda: processes)
    other = processes.create(session_id="another", execution_id=None, tool_call_id=None,
                             command="sleep 1", cwd="/work", backend_id="local")
    processes.update(other["id"], status="exited", exit_code=0, ended_at=other["started_at"] + 1,
                     background=True)
    calls = []

    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) == 1:
            record = processes.create(session_id="goal-chat", execution_id=None, tool_call_id=None,
                                      command="npm run build", cwd="/work", backend_id="local")
            processes.append_output(record["id"], b"compiled 12 files\n")
            processes.update(record["id"], status="exited", exit_code=0,
                             ended_at=record["started_at"] + 25, background=True)
        if len(calls) >= 3:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)

    monkeypatch.setattr(chat, "used_tools", lambda _store, eid: True)
    _, done = _start_goal_chat(goals, chat, monkeypatch, runner,
                               until=lambda goal: goal["status"] != "active")
    assert done.wait(30), goals.load_goal("goal-chat")
    assert len(calls) == 3
    assert "A command you left running in the background has ended." in calls[1].user_text
    assert "npm run build" in calls[1].user_text and "compiled 12 files" in calls[1].user_text
    assert calls[1].user_text.startswith("Continue the active Goal.")
    assert "npm run build" not in calls[2].user_text, "a notice is delivered once"
    assert [r["session_id"] for r in processes.unreported_exits()] == ["another"]


def test_a_released_claim_goes_back_to_the_sweep(tmp_path):
    from openprogram.host.processes.store import ProcessStore
    from openprogram.runtime.conversation import process_notices
    processes = ProcessStore(tmp_path / "processes.db")
    record = processes.create(session_id="goal-chat", execution_id=None, tool_call_id=None,
                              command="make", cwd="/work", backend_id="local")
    processes.update(record["id"], status="exited", exit_code=2, ended_at=record["started_at"] + 3,
                     background=True)
    ids, text = process_notices.claim_for_turn("goal-chat", processes)
    assert ids == [record["id"]] and "exited with code 2" in text
    assert processes.unreported_exits() == []
    process_notices.release_claim(ids, processes)
    assert [r["id"] for r in processes.unreported_exits()] == [record["id"]]


def test_a_turn_with_tool_calls_resets_the_idle_count(runtime, monkeypatch):
    goals, chat, store = runtime
    calls = []
    # Automatic turns: idle, tools, idle, idle -> stops on the fifth call.
    tools = iter([False, True, False, False, False])
    seen = {}
    monkeypatch.setattr(chat, "used_tools", lambda _store, eid: seen.setdefault(eid, next(tools)))
    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) > 6:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    _, done = _start_goal_chat(goals, chat, monkeypatch, runner,
                               until=lambda goal: goal["status"] != "active")
    assert done.wait(30), goals.load_goal("goal-chat")
    assert len(calls) == 5
    assert [chat.IDLE_WARNING in call.user_text for call in calls[1:]] == [False, True, False, True]
    assert goals.load_goal("goal-chat")["status"] == "stalled"


def test_a_queued_user_message_goes_before_the_next_automatic_turn(runtime, monkeypatch):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.agent.turn_request import TurnRequest
    goals, chat, store = runtime
    monkeypatch.setattr(chat, "used_tools", lambda *_: True)
    calls = []
    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) == 1:
            # The user queues a message while the first turn runs.
            chat.note_user_waiting("goal-chat", True)
        if len(calls) == 3:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    _, done = _start_goal_chat(goals, chat, monkeypatch, runner,
                               until=lambda goal: goal["status"] == "paused")
    # The continuation yields; the queued message is sent once the turn ends.
    import time
    time.sleep(1.2)
    assert len(calls) == 1
    adapter = CanonicalAgentAdapter(turn_runner=runner)
    admission = adapter.admit(TurnRequest("goal-chat", "queued", "main", "web", permission_mode="ask"),
                              trusted_actor={}, user_message_id="u2",
                              assistant_message_id="u2_reply", config_snapshot_ref="session:goal-chat")
    assert not chat.user_waiting("goal-chat")
    asyncio.run(adapter.activate(admission))
    assert done.wait(30), goals.load_goal("goal-chat")
    assert [call.user_text for call in calls[:2]] == ["work", "queued"]
    assert calls[2].goal_trigger is True
    assert len(calls) == 3


def test_an_unsent_queued_message_holds_the_goal_only_briefly(runtime, monkeypatch):
    goals, chat, store = runtime
    monkeypatch.setattr(chat, "used_tools", lambda *_: True)
    monkeypatch.setattr(chat, "USER_TURN_GRACE_S", 0.6)
    calls = []
    def runner(*, request, cancel_event):
        calls.append(request)
        if len(calls) == 1:
            chat.note_user_waiting("goal-chat", True)
        if len(calls) == 2:
            goals.apply_goal_action(request.session_id, "pause")
        return SimpleNamespace(failed=False)
    _, done = _start_goal_chat(goals, chat, monkeypatch, runner,
                               until=lambda goal: goal["status"] == "paused")
    assert done.wait(30), goals.load_goal("goal-chat")
    assert calls[1].goal_trigger is True
    assert not chat.user_waiting("goal-chat")

