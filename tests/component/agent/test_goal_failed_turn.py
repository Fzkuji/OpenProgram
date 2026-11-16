"""A Goal whose turn failed: which failures it goes on from by itself, how
long it waits first, and what the next turn is.

A failure the next turn would meet again at once (no credit, a rejected
key, a spent quota or budget) keeps the pause and its reason; a transient
one is continued after a short, growing wait, at most three times in a row.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixture, bound at module level so pytest collects it here.
runtime = _test_chat_goal.runtime

SID = "goal-chat"


def _failed_goal(goals, chat, monkeypatch, *, failure=None, execution_status="failed", **fields):
    """The Goal as a failed turn's accounting leaves it, with the execution
    reads resume makes answered from memory."""
    goal = goals.load_goal(SID)
    goal.update(status="paused_recoverable", phase="paused", pause_reason="execution_failed",
                last_reason="agent_runner_error", execution_id="exec-failed",
                accounted_execution_id="exec-failed", active_started_at=None,
                failure=failure or {})
    goal.update(fields)
    goals.save_goal(SID, goal)
    observed = {"execution_id": "exec-failed", "status": execution_status, "finished": True,
                "status_version": 3, "active_children": []}
    monkeypatch.setattr(goals, "goal_execution_state", lambda goal, session_id="": dict(observed))
    monkeypatch.setattr(goals, "require_goal_execution_finished", lambda goal, session_id: None)
    monkeypatch.setattr(goals, "goal_control_state", lambda goal, session_id, observed=None: {
        "can_resume": True, "reasons": {"resume": None}})
    return goals.load_goal(SID)


def _wait_until(predicate, seconds=30.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def _continuations(chat, monkeypatch):
    """Record the continuation turns instead of admitting them."""
    started, seen = [], threading.Event()

    def start_next(store, previous_execution_id, *, expected):
        started.append((previous_execution_id, dict(expected)))
        seen.set()

    monkeypatch.setattr(chat, "start_next", start_next)
    monkeypatch.setattr(chat, "FAILURE_RESUME_DELAYS_S", (0.0, 0.0, 0.0))
    return started, seen


@pytest.mark.parametrize("failure, repeats", [
    ({}, False),                                            # the worker's own fault
    ({"reason": "transport"}, False),
    ({"reason": "provider"}, False),
    ({"reason": "timeout"}, False),
    ({"reason": "rate_limit", "retry_after_s": 20}, False),
    ({"reason": "rate_limit", "retry_after_s": 3600}, True),  # a wait a person decides about
    ({"reason": "auth"}, True),
    ({"reason": "authz"}, True),
    ({"reason": "blocked"}, True),
    ({"reason": "invalid"}, True),
    ({"reason": "context"}, True),
    ({"reason": "policy"}, True),
    ({"reason": "rate_limit", "cause": "quota_exhausted"}, True),  # no credit, a spent plan
    ({"reason": "unknown", "cause": "invalid_key"}, True),
    ({"reason": "unknown", "cause": "not_available"}, True),
    ({"reason": "transport", "cause": "unreachable"}, False),
    ({"type": "QuotaExceeded"}, True),                      # the Goal's or the Job's budget
])
def test_only_a_failure_that_would_not_repeat_at_once_is_continued(failure, repeats):
    from openprogram.programs.tools.planning.goal import chat
    assert chat.failure_repeats(failure) is repeats


def test_a_turn_the_user_stopped_pauses_the_goal_as_theirs(runtime, monkeypatch):
    """Stop on a Goal turn is the person pausing it: the pause is recorded as
    theirs, so neither the failure retry nor an always-on wake takes it up."""
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    goal = goals.load_goal(SID)
    goal["execution_id"] = "exec-stopped"
    goals.save_goal(SID, goal)
    execution = SimpleNamespace(execution_id="exec-stopped", session_id=SID,
                                status=ExecutionStatus.CANCELLED, reason_code="cancel.user")
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_trigger": True,
                                    "goal_context": chat.identity(goal)}})
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: pytest.fail("a stopped turn starts nothing"))

    chat.after_terminal(store, execution)

    settled = goals.load_goal(SID)
    assert settled["status"] == "paused_recoverable"
    assert settled["pause_reason"] == "user"
    assert settled["last_reason"] == "Goal paused by the user."
    assert "user" not in chat.AUTOMATIC_RESUME_REASONS
    monkeypatch.setattr(goals, "goal_execution_state",
                        lambda goal, session_id="": {"status": "cancelled", "finished": True})
    assert not chat.continues_after_failure(SID, settled)


def test_the_wait_grows_with_each_failure_in_a_row_and_respects_the_provider():
    from openprogram.programs.tools.planning.goal import chat
    waits = [chat.failure_resume_delay({"automatic_resumes": count}) for count in range(4)]
    assert waits[:3] == list(chat.FAILURE_RESUME_DELAYS_S)
    assert waits[0] < waits[1] < waits[2] == waits[3]
    assert waits[0] >= 1.0  # never at once
    asked = chat.failure_resume_delay({"automatic_resumes": 0, "failure": {"retry_after_s": 45}})
    assert asked == 45.0
    assert chat.failure_resume_delay({"automatic_resumes": 2, "failure": {"retry_after_s": 1}}) == waits[2]


def test_a_failed_turn_records_what_stopped_it(runtime, monkeypatch):
    from openprogram.runtime.execution.model import ExecutionStatus
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call
    goals, chat, store = runtime
    goal = goals.load_goal(SID)
    goal["execution_id"] = "exec-failed"
    goals.save_goal(SID, goal)
    writer = SessionNodeWriter(goals._db(), SID)
    writer.append(Call(id="u1", role="user", output="work"))
    writer.append(Call(id="u1_reply", role="llm", output="[error] LLMError: no credit", predecessor="u1",
                       metadata={"error_reason": "rate_limit", "error_retry_after_s": None,
                                 "error_cause": {"cause": "quota_exhausted", "provider": "openai"},
                                 "error_type": "LLMError"}))
    execution = SimpleNamespace(execution_id="exec-failed", session_id=SID,
                                status=ExecutionStatus.FAILED, reason_code="agent_runner_error")
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_trigger": True,
                                    "goal_context": chat.identity(goal)}})
    monkeypatch.setattr(store, "get_execution_input",
                        lambda _id: SimpleNamespace(assistant_message_id="u1_reply", user_message_id="u1"))
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: pytest.fail("a failed turn starts nothing"))

    chat.after_terminal(store, execution)

    settled = goals.load_goal(SID)
    assert settled["status"] == "paused_recoverable" and settled["pause_reason"] == "execution_failed"
    assert settled["last_reason"] == "agent_runner_error"  # the reason text is the turn's own
    assert settled["failure"] == {"reason": "rate_limit", "cause": "quota_exhausted", "type": "LLMError"}
    # No credit: the pause waits for a person, with that reason.
    monkeypatch.setattr(goals, "goal_execution_state",
                        lambda goal, session_id="": {"status": "failed", "finished": True})
    assert not chat.continues_after_failure(SID, settled)
    assert not chat.continue_after_failure(SID, settled)
    assert goals.load_goal(SID)["last_reason"] == "agent_runner_error"


def test_a_turn_that_left_no_reply_row_counts_as_transient(runtime, monkeypatch):
    goals, chat, store = runtime
    execution = SimpleNamespace(execution_id="exec-gone", session_id=SID)
    monkeypatch.setattr(store, "get_execution_input", lambda _id: None)
    assert chat.turn_failure(store, execution) == {}

    def unreadable(_id):
        raise RuntimeError("store closed")

    monkeypatch.setattr(store, "get_execution_input", unreadable)
    assert chat.turn_failure(store, execution) == {}


def test_a_transient_failure_is_continued_from_the_failed_turn(runtime, monkeypatch):
    goals, chat, store = runtime
    paused = _failed_goal(goals, chat, monkeypatch, failure={"reason": "transport"})
    started, seen = _continuations(chat, monkeypatch)

    assert chat.continue_after_failure(SID, paused)
    assert seen.wait(30)
    assert _wait_until(lambda: not chat._continuing)

    goal = goals.load_goal(SID)
    assert goal["status"] == "active" and goal["phase"] == "idle"
    assert goal["automatic_resumes"] == 1 and "failure" not in goal
    # The next turn follows the failed one, as it would have a completed one.
    assert goal["execution_id"] == "exec-failed"
    assert started == [("exec-failed", chat.identity(goal))]
    assert goal["run_id"] != paused["run_id"]


def test_the_wait_is_not_skipped(runtime, monkeypatch):
    goals, chat, _store = runtime
    paused = _failed_goal(goals, chat, monkeypatch, failure={"reason": "transport"})
    started, seen = _continuations(chat, monkeypatch)
    monkeypatch.setattr(chat, "FAILURE_RESUME_DELAYS_S", (1.0, 1.0, 1.0))

    begun = time.monotonic()
    assert chat.continue_after_failure(SID, paused)
    # A second read of the Goal while it waits starts nothing more.
    assert chat.continue_after_failure(SID, goals.load_goal(SID))
    assert goals.load_goal(SID)["status"] == "paused_recoverable"
    assert seen.wait(30)
    assert time.monotonic() - begun >= 1.0
    assert _wait_until(lambda: not chat._continuing)
    assert len(started) == 1


@pytest.mark.parametrize("fields, execution_status", [
    ({"failure": {"reason": "auth"}}, "failed"),
    ({"failure": {"cause": "quota_exhausted"}}, "failed"),
    ({"failure": {"type": "QuotaExceeded"}}, "failed"),
    ({"automatic_resumes": 3}, "failed"),                   # three failures in a row
    ({}, "cancelled"),                                      # the user stopped the turn
    ({}, "interrupted"),                                    # a restart's cut is the restart's to continue
    ({"origin": "always_on"}, "failed"),                    # its Agent's wake restarts it
    ({"origin": "scheduled"}, "failed"),
    ({"pause_reason": "plan_mode"}, "failed"),
])
def test_the_pause_and_its_reason_stay(runtime, monkeypatch, fields, execution_status):
    goals, chat, _store = runtime
    fields = dict(fields)
    failure = fields.pop("failure", None)
    paused = _failed_goal(goals, chat, monkeypatch, failure=failure,
                          execution_status=execution_status, **fields)
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: pytest.fail("nothing continues"))
    monkeypatch.setattr(chat, "FAILURE_RESUME_DELAYS_S", (0.0, 0.0, 0.0))

    assert not chat.continue_after_failure(SID, paused)
    assert not chat._continuing
    goal = goals.load_goal(SID)
    assert goal["status"] == "paused_recoverable"
    assert goal["last_reason"] == "agent_runner_error"
    assert goal["version"] == paused["version"]


def test_a_spent_budget_keeps_the_pause(runtime, monkeypatch):
    goals, chat, _store = runtime
    paused = _failed_goal(goals, chat, monkeypatch, failure={"reason": "transport"})
    monkeypatch.setattr(goals, "budget_exhausted", lambda goal, **kw: "tokens")
    assert not chat.continue_after_failure(SID, paused)


def test_a_goal_someone_changed_during_the_wait_is_left_alone(runtime, monkeypatch):
    goals, chat, _store = runtime
    paused = _failed_goal(goals, chat, monkeypatch, failure={"reason": "transport"})
    # Someone pressed Continue and that turn failed too: one more in a row.
    changed = goals.load_goal(SID)
    changed["automatic_resumes"] = 1
    goals.save_goal(SID, changed)
    assert chat._resume_failed(SID, "exec-failed", int(paused.get("automatic_resumes") or 0)) is None
    # Another turn became the Goal's.
    assert chat._resume_failed(SID, "exec-other", 1) is None
    goal = goals.load_goal(SID)
    assert goal["status"] == "paused_recoverable" and goal["automatic_resumes"] == 1


def test_a_failed_verification_turn_is_verified_again(runtime, monkeypatch):
    """The candidate is submitted again from the failed verification turn;
    the Goal does not go back to a work turn, and a repeated notice of the
    failed turn judges nothing."""
    from openprogram.programs.tools.planning.goal import verification
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    paused = _failed_goal(
        goals, chat, monkeypatch, failure={"reason": "provider"},
        verification={"id": "candidate-1", "status": "pending", "execution_id": "exec-failed",
                      "previous_execution_id": "exec-work"})
    execution = SimpleNamespace(execution_id="exec-failed", session_id=SID,
                                status=ExecutionStatus.FAILED, reason_code="agent_runner_error")
    monkeypatch.setattr(store, "get_execution", lambda _id: execution)
    prepared = []

    def prepare(_store, previous, goal):
        prepared.append((previous.execution_id, goal.get("completion_requested")))
        goal.pop("completion_requested", None)
        goal["verification"] = {"id": "candidate-2", "status": "pending",
                                "previous_execution_id": previous.execution_id}
        goal["phase"] = "verifying"

    monkeypatch.setattr(verification, "prepare", prepare)
    started, seen = _continuations(chat, monkeypatch)

    assert chat.continue_after_failure(SID, paused)
    assert seen.wait(30)
    assert _wait_until(lambda: not chat._continuing)

    goal = goals.load_goal(SID)
    assert goal["status"] == "active" and goal["phase"] == "verifying"
    assert goal["verification"]["id"] == "candidate-2"
    assert prepared == [("exec-failed", chat.identity(goal))]
    assert started == [("exec-failed", chat.identity(goal))]
    assert goal["automatic_resumes"] == 1

    # The failed verification turn's notice arrives again (a worker start
    # replays recent ones): the new candidate is neither judged nor lost.
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_trigger": True,
                                    "goal_verification": "candidate-1",
                                    "goal_context": chat.identity(goal)}})
    monkeypatch.setattr(verification, "finish",
                        lambda *a, **kw: pytest.fail("a replaced candidate is not judged by its failed turn"))
    monkeypatch.setattr(verification, "evidence_digests", lambda goal: {})
    chat.after_terminal(store, execution)
    again = goals.load_goal(SID)
    assert again["verification"]["id"] == "candidate-2" and again["verification"]["status"] == "pending"
    assert started[-1] == ("exec-failed", chat.identity(again))


def test_a_verification_that_cannot_run_again_keeps_its_own_pause(runtime, monkeypatch):
    from openprogram.programs.tools.planning.goal import verification
    goals, chat, store = runtime
    paused = _failed_goal(
        goals, chat, monkeypatch, failure={},
        verification={"id": "candidate-1", "status": "pending", "execution_id": "exec-failed"})
    monkeypatch.setattr(store, "get_execution",
                        lambda _id: SimpleNamespace(execution_id="exec-failed", session_id=SID))

    def blocked(_store, _previous, goal):
        goal.update(status="paused_recoverable", phase="paused", pause_reason="verification_blocked",
                    last_reason="Completion cannot be verified: managed_work_unfinished")

    monkeypatch.setattr(verification, "prepare", blocked)
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: pytest.fail("nothing continues"))
    assert chat._resume_failed(SID, "exec-failed", int(paused.get("automatic_resumes") or 0)) is None
    goal = goals.load_goal(SID)
    assert goal["pause_reason"] == "verification_blocked"
    assert not chat.continues_after_failure(SID, goal)


def test_a_completed_turn_starts_the_count_over(runtime, monkeypatch):
    from openprogram.runtime.execution.model import ExecutionStatus
    goals, chat, store = runtime
    goal = goals.load_goal(SID)
    goal.update(execution_id="exec-ok", automatic_resumes=2, failure={"reason": "transport"})
    goals.save_goal(SID, goal)
    monkeypatch.setattr(store, "get_agent_turn_input", lambda _id: {
        "kind": "chat", "request": {"source": "web", "goal_context": chat.identity(goal)}})
    monkeypatch.setattr(chat, "start_next", lambda *a, **kw: None)
    chat.after_terminal(store, SimpleNamespace(execution_id="exec-ok", session_id=SID,
                                               status=ExecutionStatus.COMPLETED, reason_code=None))
    settled = goals.load_goal(SID)
    assert "automatic_resumes" not in settled and "failure" not in settled
