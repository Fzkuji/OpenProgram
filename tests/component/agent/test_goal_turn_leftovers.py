"""What a Goal's turn must not get wrong at its edges.

* A stopped turn's cancelled reply and resource release run even when the
  Goal's accounting could not land.
* A provider request's Goal check, made without the lock after its wait ran
  out, redoes a Goal write that met a newer Goal instead of failing the request.
* A turn continued after a worker restart is held back only by a foreground
  turn on its own branch, and a Goal on another branch does not concern it.
* A reply that paused and went on counts the usage of the whole turn.
"""
from __future__ import annotations

import time
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixture, bound at module level so pytest collects it here.
runtime = _test_chat_goal.runtime

SID = "goal-chat"


# -- a turn's end ----------------------------------------------------------


@pytest.mark.parametrize("error", ["busy", "usage_pending"])
def test_goal_accounting_that_could_not_land_does_not_fail_the_settlement(monkeypatch, error):
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs._host import services
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.conversation import execution_hooks
    closed = []
    browser = SimpleNamespace(tabs=SimpleNamespace(close_run_tabs_soon=closed.append))
    monkeypatch.setattr(services, "lookup", lambda name: browser if name == "browser" else None)

    def after_terminal(_store, _execution):
        if error == "busy":
            raise goals.GoalBusy("Goal admission or mutation is already in progress")
        raise goals.GoalStateUnavailable("Goal usage settlement is pending")

    monkeypatch.setattr(chat, "after_terminal", after_terminal)
    execution_hooks._settle_terminal(None, SimpleNamespace(execution_id="exec_9", session_id=SID))
    assert closed == ["exec_9"]  # the run's pages still close


def test_a_stopped_turn_is_presented_and_released_whatever_its_settlement_does(monkeypatch):
    """``finish_attempt`` on a cancelled turn: the cancel presentation and the
    resource release follow even when the engine's settlement raises."""
    from openprogram.runtime.execution import hooks
    from openprogram.runtime.execution.control.safe_points import SafePointsOperations
    from openprogram.runtime.execution.model import ExecutionStatus

    execution = SimpleNamespace(execution_id="exec-stopped", status=ExecutionStatus.CANCELLED)
    attempt = SimpleNamespace(execution_id="exec-stopped", attempt_id="attempt-1")
    observed, forgotten = [], []
    service = SimpleNamespace(
        attempts=SimpleNamespace(get=lambda _id: attempt,
                                 finish=lambda *a, **kw: (attempt, execution)),
        executions=SimpleNamespace(get_command=lambda _id: None),
        effects=SimpleNamespace(list_unresolved=lambda _id: []),
        registry=SimpleNamespace(unbind=lambda *a, **kw: None),
        _forget_cancel_delivery=forgotten.append,
        _observe_terminal=observed.append,
    )

    def settlement(_executions, _execution):
        raise RuntimeError("settlement failed")

    monkeypatch.setattr(hooks, "settle_terminal", settlement)
    with pytest.raises(RuntimeError, match="settlement failed"):
        SafePointsOperations.finish_attempt(
            service, attempt_id="attempt-1", generation=1, expected_execution_version=4,
            target=ExecutionStatus.CANCELLED, outcome="cancelled", reason_code="cancelled")
    assert observed == [execution]
    assert forgotten == ["exec-stopped"]


# -- a provider request's Goal check without the lock -----------------------


def _registered():
    from openprogram.programs._host import lower_layers
    lower_layers.register()


def _concurrent_write(goals, chat, monkeypatch, *, times=1):
    """Make the next ``times`` Goal writes meet a Goal someone else just
    wrote, as an unlocked writer does."""
    real_publish = chat.publish
    left = [times]

    def publish(session_id, goal):
        if left[0] > 0:
            left[0] -= 1
            other = goals.load_goal(session_id)
            other["checklist"] = [*(other.get("checklist") or []), {"text": "from elsewhere", "done": False}]
            goals.save_goal(session_id, other)
        return real_publish(session_id, goal)

    monkeypatch.setattr(chat, "publish", publish)
    return left


@pytest.mark.parametrize("reason, status", [
    ("quota.token_exhausted", "budget_exhausted"),
    ("quota.accounting_unavailable", "paused_recoverable"),
])
def test_a_budget_refusal_is_recorded_on_the_goal_it_meets(runtime, monkeypatch, reason, status):
    from openprogram.models.usage import request as usage_request
    goals, chat, _store = runtime
    _registered()
    read = goals.load_goal(SID)
    _concurrent_write(goals, chat, monkeypatch)

    usage_request._record_denial(SID, read, SimpleNamespace(reason_code=reason))

    goal = goals.load_goal(SID)
    assert goal["status"] == status and goal["last_reason"] == reason
    assert goal["checklist"] == [{"text": "from elsewhere", "done": False}]  # nothing unseen overwritten


def test_a_refusal_leaves_a_goal_someone_paused_meanwhile(runtime, monkeypatch):
    from openprogram.models.usage import request as usage_request
    goals, chat, _store = runtime
    _registered()
    read = goals.load_goal(SID)
    real_publish = chat.publish
    first = [True]

    def publish(session_id, goal):
        if first[0]:
            first[0] = False
            other = goals.load_goal(session_id)
            other.update(status="paused", phase="paused", last_reason="paused by the user")
            goals.save_goal(session_id, other)
        return real_publish(session_id, goal)

    monkeypatch.setattr(chat, "publish", publish)
    usage_request._record_denial(SID, read, SimpleNamespace(reason_code="quota.token_exhausted"))
    goal = goals.load_goal(SID)
    assert goal["status"] == "paused" and goal["last_reason"] == "paused by the user"


def test_a_refusal_that_never_lands_still_lets_the_request_be_refused(runtime, monkeypatch):
    from openprogram.models.usage import request as usage_request
    goals, chat, _store = runtime
    _registered()
    read = goals.load_goal(SID)
    left = _concurrent_write(goals, chat, monkeypatch, times=99)
    # No version conflict reaches the caller, which raises the refusal itself.
    usage_request._record_denial(SID, read, SimpleNamespace(reason_code="quota.token_exhausted"))
    assert left[0] == 99 - usage_request.GOAL_WRITE_TRIES
    assert goals.load_goal(SID)["status"] == "active"


def test_old_usage_is_converted_again_when_its_write_meets_a_newer_goal(runtime, monkeypatch):
    from openprogram.models.usage import request as usage_request
    goals, chat, _store = runtime
    _registered()
    goal = goals.load_goal(SID)
    goal.update(usage_mode="session", usage={"total_tokens": 40, "cost_usd": 0.5, "cost_known": True,
                                             "active_elapsed_s": 0.0})
    goals.save_goal(SID, goal)
    _concurrent_write(goals, chat, monkeypatch)

    admitted = usage_request._admitted_goal(SID, chat.identity(goal), "request")

    stored = goals.load_goal(SID)
    assert admitted["usage_mode"] == "requests" and stored["usage_mode"] == "requests"
    assert stored["legacy_usage"]["total_tokens"] == 40
    assert stored["checklist"] == [{"text": "from elsewhere", "done": False}]
    assert admitted["version"] == stored["version"]


def test_a_request_of_a_goal_that_changed_is_refused(runtime):
    from openprogram.models.usage import request as usage_request
    goals, chat, _store = runtime
    _registered()
    goal = goals.load_goal(SID)
    identity = chat.identity(goal)
    assert usage_request._admitted_goal(SID, identity, "request")["goal_id"] == goal["goal_id"]
    with pytest.raises(goals.GoalConflictError, match="Goal changed before provider request"):
        usage_request._admitted_goal(SID, dict(identity, run_id="another-run"), "request")
    goal.update(status="paused", phase="paused")
    goals.save_goal(SID, goal)
    with pytest.raises(goals.GoalConflictError, match="no longer permits"):
        usage_request._admitted_goal(SID, identity, "request")
    assert not usage_request._permits_requests(goals.load_goal(SID), identity)


# -- a turn continued after a worker restart --------------------------------


class _Executions:
    """The execution store as ``start_recovery_turn`` reads it."""

    def __init__(self, interrupted, running, requests):
        self.records = {interrupted.execution_id: interrupted}
        self.running = running
        self.requests = requests
        self.records.update({record.execution_id: record for record in running})

    def get_execution(self, execution_id):
        from openprogram.runtime.execution.model import ExecutionStatus
        return self.records.get(execution_id) or SimpleNamespace(
            execution_id=execution_id, status=ExecutionStatus.RUNNING, current_attempt_id="attempt-new")

    def get_execution_input(self, execution_id):
        return SimpleNamespace(trusted_actor={"subject": "owner"}, config_snapshot_ref="config:test",
                               user_message_id=f"{execution_id}_user", assistant_message_id=f"{execution_id}_reply")

    def get_agent_turn_input(self, execution_id):
        request = self.requests.get(execution_id)
        return {"kind": "chat", "request": dict(request)} if request is not None else None

    def admission_execution_id(self, _session_id, _key):
        return "exec-continued"

    def list_nonterminal(self, *, session_id):
        return list(self.running)


def _restart(monkeypatch, *, running_branch=None, goal=None, own_branch="branch-own"):
    """Continue an interrupted turn of ``own_branch`` while another turn may
    run on ``running_branch``; what ``start_recovery_turn`` answers."""
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.conversation import production_driver, restart_turn
    from openprogram.runtime.execution import restart
    from openprogram.runtime.execution.model import ExecutionStatus

    interrupted = SimpleNamespace(
        execution_id="exec-cut", session_id=SID, status=ExecutionStatus.INTERRUPTED,
        reason_code="owner_lost", status_version=5, current_attempt_id=None, created_at=1.0)
    running = []
    requests = {"exec-cut": {"source": "web", "user_text": "work", "user_msg_id": "u1",
                             "branch_id": own_branch, "interaction": None}}
    if running_branch:
        running.append(SimpleNamespace(
            execution_id="exec-running", session_id=SID, status=ExecutionStatus.RUNNING,
            reason_code=None, status_version=2, current_attempt_id="attempt-1", created_at=2.0))
        requests["exec-running"] = {"source": "web", "user_text": "other", "branch_id": running_branch,
                                    "interaction": None}
    store = _Executions(interrupted, running, requests)
    admitted = []

    class Adapter:
        def __init__(self, **_kw):
            pass

        def admit_payload(self, **kwargs):
            admitted.append(kwargs)
            return SimpleNamespace(execution_id="exec-continued")

    monkeypatch.setattr(production_driver, "CanonicalAgentAdapter", Adapter)
    monkeypatch.setattr(chat, "locked", lambda _session_id, **_kw: nullcontext())
    monkeypatch.setattr(goals, "load_goal", lambda _session_id: goal)
    monkeypatch.setattr(restart, "window_seconds", lambda: 3600)
    monkeypatch.setattr("openprogram.runtime.execution.chat_recovery.recovery_state",
                        lambda _store, _execution, **_kw: {"can_start_new_turn": True})
    event = SimpleNamespace(sequence=7, payload={
        "expected_version": 5, "interrupted_at": time.time() - 1, "resume_before": None})
    return restart_turn.start_recovery_turn(store, interrupted, event), admitted


def test_a_turn_on_another_branch_does_not_hold_a_restart_continuation_back(monkeypatch):
    outcome, admitted = _restart(monkeypatch, running_branch="branch-other")
    assert outcome == "new_turn_admitted"
    assert admitted[0]["payload"]["request"]["branch_id"] == "branch-own"


def test_a_turn_on_the_same_branch_supersedes_a_restart_continuation(monkeypatch):
    outcome, admitted = _restart(monkeypatch, running_branch="branch-own")
    assert outcome == "superseded" and not admitted


@pytest.mark.parametrize("goal_branch, outcome", [
    ("branch-other", "new_turn_admitted"),  # a Goal on another branch does not concern this turn
    ("branch-own", "goal_changed"),         # a Goal started on its branch since is not its authority
])
def test_only_a_goal_on_the_turns_own_branch_stops_its_restart_continuation(monkeypatch, goal_branch, outcome):
    goal = {"execution_mode": "chat", "status": "active", "goal_id": "g1", "revision": 1,
            "run_id": "r1", "branch_id": goal_branch, "execution_id": "exec-goal"}
    answered, admitted = _restart(monkeypatch, goal=goal)
    assert answered == outcome
    assert bool(admitted) is (outcome == "new_turn_admitted")


# -- a reply that paused and went on ----------------------------------------


def test_a_resumed_turn_counts_the_usage_of_the_whole_turn():
    from openprogram.models.providers.types import AssistantMessage, TextContent, ToolCall, Usage
    from openprogram.runtime.conversation.turn_runtime import loop_runner
    from openprogram.runtime.conversation.turn_runtime.loop_runner import (
        _TurnTally, _seed_tally_from_continuation)

    def response(content, usage, stop_reason):
        return AssistantMessage(content=content, api="completion", provider="openai", model="stub",
                                usage=usage, stop_reason=stop_reason, timestamp=int(time.time() * 1000))

    first = response([TextContent(text="Checking A."), ToolCall(id="call-1", name="probe", arguments={})],
                     Usage(input=100, output=20, cache_read=30), "toolUse")
    second = response([TextContent(text="Now B."), ToolCall(id="call-2", name="probe", arguments={})],
                      Usage(input=150, output=10, cache_read=120, cache_write=5), "toolUse")
    paused_on = response([TextContent(text="B needs your word."),
                          ToolCall(id="call-3", name="ask", arguments={})],
                         Usage(input=180, output=8), "toolUse")
    journal = SimpleNamespace(decisions=[
        SimpleNamespace(message=message.model_dump()) for message in (first, second, paused_on)])
    continuation = SimpleNamespace(display=(), assistant_message=paused_on, tool_results=(), journal=journal)

    tally = _TurnTally()
    _seed_tally_from_continuation(tally, continuation, [])
    assert tally.usage_total["provider_request_count"] == 3
    assert tally.usage_total["agent_iteration_count"] == 3
    assert tally.usage_total["input_tokens"] == 430
    assert tally.usage_total["output_tokens"] == 38
    assert tally.usage_total["cache_read_tokens"] == 150
    assert tally.usage_total["cache_write_tokens"] == 5

    # The response after the pause is added once, like any live response.
    after = response([TextContent(text="B is fine.")], Usage(input=200, output=6), "stop")
    request = SimpleNamespace(structured_output=None, structured_output_mode=None,
                              structured_output_attempt=None, last_round_text="", session_id=SID,
                              user_msg_id="u1")
    loop_runner._record_turn_end(SimpleNamespace(message=after), tally, req=request,
                                 on_event=lambda _event: None, ordered_blocks_out=None)
    assert tally.usage_total["provider_request_count"] == 4
    assert tally.usage_total["input_tokens"] == 630 and tally.usage_total["output_tokens"] == 44


def test_a_continuation_without_a_journal_counts_the_response_it_paused_on():
    from openprogram.models.providers.types import AssistantMessage, TextContent, Usage
    from openprogram.runtime.conversation.turn_runtime.loop_runner import (
        _TurnTally, _seed_tally_from_continuation)
    paused_on = AssistantMessage(content=[TextContent(text="One response.")], api="completion",
                                 provider="openai", model="stub", usage=Usage(input=5, output=2),
                                 timestamp=int(time.time() * 1000))
    tally = _TurnTally()
    _seed_tally_from_continuation(
        tally, SimpleNamespace(display=(), assistant_message=paused_on, tool_results=()), [])
    assert tally.usage_total["provider_request_count"] == 1
    assert (tally.usage_total["input_tokens"], tally.usage_total["output_tokens"]) == (5, 2)
