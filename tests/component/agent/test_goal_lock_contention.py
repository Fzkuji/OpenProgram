"""Two writers of one conversation's Goal meet: the later one waits its turn,
and a reply never fails because the Goal's books were busy.

The case behind these: a conversation with a 731 MB log, where a Goal read
under the lock folded the log for about six seconds while another turn of the
same conversation asked for the lock once and failed its reply.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixture, bound at module level so pytest collects it here.
runtime = _test_chat_goal.runtime

# Stands in for the measured hold; long against the old single try, short for a test.
HOLD_S = 0.4


def _hold(goals, seconds):
    """Hold the Goal lock on another thread, as a slow writer does."""
    from openprogram.programs.tools.planning.goal.ownership import goal_owner
    held, release = threading.Event(), threading.Event()

    def holder():
        with goal_owner(goals._db(), "goal-chat") as acquired:
            assert acquired
            held.set()
            release.wait(seconds)

    thread = threading.Thread(target=holder)
    thread.start()
    assert held.wait(30)
    return thread, release


def _lock_is_free(goals) -> bool:
    from openprogram.programs.tools.planning.goal.ownership import goal_owner
    with goal_owner(goals._db(), "goal-chat") as acquired:
        return acquired


def test_a_writer_that_meets_a_slow_holder_waits_its_turn(runtime):
    from openprogram.models.usage import goal_port
    goals, chat, _store = runtime
    thread, release = _hold(goals, HOLD_S)
    try:
        started = time.monotonic()
        chat.refresh_usage("goal-chat")  # after every provider request
        with goal_port.locked("goal-chat"):  # before every provider request
            pass
        assert chat.pause_always_on("goal-chat", "always_on_off") is False  # a control
        assert time.monotonic() - started >= HOLD_S * 0.5
    finally:
        release.set()
        thread.join(30)


def test_waiters_of_one_process_queue_instead_of_failing(runtime):
    goals, chat, _store = runtime
    inside, errors = [], []

    def writer(index):
        try:
            with chat.locked("goal-chat"):
                inside.append(index)
                assert len(inside) - len(done) == 1  # one at a time
                time.sleep(0.02)
                done.append(index)
        except Exception as exc:
            errors.append(exc)

    done: list[int] = []
    threads = [threading.Thread(target=writer, args=(index,)) for index in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert not errors
    assert sorted(done) == list(range(12))
    assert not chat._queues


def test_a_running_turn_goes_on_when_the_lock_stays_held(runtime, monkeypatch):
    from openprogram.models.usage import request as usage_request
    goals, chat, store = runtime
    goal = goals.load_goal("goal-chat")
    goal["execution_id"] = "exec-running"
    goals.save_goal("goal-chat", goal)
    monkeypatch.setattr(store, "get_execution", lambda _id: SimpleNamespace(
        execution_id="exec-running", session_id="goal-chat", current_attempt_id="attempt-1"))
    monkeypatch.setattr(chat, "GOAL_LOCK_WAIT_S", 0.05)
    thread, release = _hold(goals, 30)
    try:
        with pytest.raises(goals.GoalBusy):
            with chat.locked("goal-chat"):
                pass
        # The provider request's check runs; the request is not refused.
        with usage_request._goal_gate("goal-chat"):
            assert goals.load_goal("goal-chat")["status"] == "active"
        # The turn's opening and closing Goal steps do not fail the turn.
        with chat.turn_context("goal-chat", "exec-running", chat.identity(goal)):
            assert goals.load_goal("goal-chat")["phase"] == "working"
    finally:
        release.set()
        thread.join(30)
    assert goals.load_goal("goal-chat")["active_started_at"] is None


def test_turn_end_reads_the_log_before_taking_the_lock(runtime, monkeypatch):
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    goal = goals.load_goal("goal-chat")
    goal["execution_id"] = "exec-auto"
    goals.save_goal("goal-chat", goal)
    execution = SimpleNamespace(execution_id="exec-auto", session_id="goal-chat",
                                status=ExecutionStatus.COMPLETED, reason_code=None)
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_trigger": True,
                                    "goal_context": chat.identity(goal)}})
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: None)
    free = []

    def used_tools(_store, _execution_id):
        free.append(_lock_is_free(goals))  # the read of the whole log
        return False

    monkeypatch.setattr(chat, "used_tools", used_tools)
    chat.after_terminal(store, execution)
    assert free == [True]
    settled = goals.load_goal("goal-chat")
    assert settled["accounted_execution_id"] == "exec-auto" and settled["idle_turns"] == 1


def test_a_busy_goal_keeps_the_completion_notice(runtime, monkeypatch):
    goals, chat, store = runtime
    monkeypatch.setattr(chat, "SETTLE_BACKGROUND_DELAYS", (0.01, 0.01, 0.01))
    calls, delivered = [], threading.Event()

    def after_terminal(_store, _execution):
        calls.append(1)
        if len(calls) < 3:
            raise goals.GoalStateUnavailable("Goal usage settlement is pending")
        if len(calls) < 5:
            raise goals.GoalBusy("Goal admission or mutation is already in progress")
        delivered.set()

    monkeypatch.setattr(chat, "SETTLE_INLINE_DELAYS", (0.0, 0.0, 0.0, 0.0))
    monkeypatch.setattr(chat, "after_terminal", after_terminal)
    chat.settle_terminal(store, SimpleNamespace(execution_id="exec-busy", session_id="goal-chat"))
    # Unavailable usage is retried at once; a lock that stayed held for its
    # whole wait is not waited for again on the turn's own thread.
    assert len(calls) == 3
    assert delivered.wait(30)
    deadline = time.monotonic() + 30
    while chat._settling and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not chat._settling


def test_a_goal_left_on_an_ended_turn_is_settled_when_read(runtime, monkeypatch):
    """What the incident left: an active Goal showing "working" on a turn that had failed."""
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    goal = goals.load_goal("goal-chat")
    goal.update(execution_id="exec-failed", phase="working")
    goals.save_goal("goal-chat", goal)
    execution = SimpleNamespace(execution_id="exec-failed", session_id="goal-chat",
                                status=ExecutionStatus.FAILED, reason_code="agent_runner_error")
    monkeypatch.setattr(store, "get_execution", lambda _id: execution)
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_trigger": True,
                                    "goal_context": chat.identity(goal)}})
    continuations = []
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: continuations.append(a))

    assert chat.recover_unsettled("goal-chat", goals.load_goal("goal-chat"))
    deadline = time.monotonic() + 30
    while goals.load_goal("goal-chat")["status"] == "active" and time.monotonic() < deadline:
        time.sleep(0.01)
    settled = goals.load_goal("goal-chat")
    assert settled["status"] == "paused_recoverable" and settled["pause_reason"] == "execution_failed"
    assert settled["accounted_execution_id"] == "exec-failed"
    assert not continuations
    # Nothing is left to settle, and a Goal whose turn still runs is left alone.
    assert not chat.recover_unsettled("goal-chat", settled)
    execution.status = ExecutionStatus.RUNNING
    assert not chat.recover_unsettled("goal-chat", dict(settled, status="active"))


def test_evidence_files_are_hashed_before_the_lock(tmp_path):
    from openprogram.programs.tools.planning.goal import verification
    kept = tmp_path / "kept.txt"
    kept.write_text("evidence")
    goal = {"verification": {"evidence": {
        "read:1": {"kind": "file", "path": str(kept)},
        "read:2": {"kind": "file", "path": str(tmp_path / "gone.txt")},
        "message:1": {"kind": "message", "message_id": "m"},
    }}}
    assert verification.evidence_digests(goal) == {
        str(kept): verification._file_digest(str(kept)), str(tmp_path / "gone.txt"): None}
