"""Goal time uses observed owner activity, not restart discovery time."""
import time

import pytest

from tests.component.agent import test_chat_goal as _test_chat_goal

# Shared pytest fixtures, bound at module level so pytest collects them here.
runtime = _test_chat_goal.runtime


def test_owner_loss_keeps_observed_work_and_excludes_offline_time(runtime, monkeypatch):
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter
    from openprogram.runtime.execution import default_control_service
    from openprogram.runtime.execution.attempts import AttemptStore
    goals, chat, store = runtime
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    adapter = CanonicalAgentAdapter(store=store)
    admission = adapter.admit(TurnRequest("goal-chat", "work", "main", "web"),
                              trusted_actor={}, user_message_id="work", config_snapshot_ref="test")
    attempts = AttemptStore(store, clock=lambda: clock[0])
    control = default_control_service()
    control.attempts = attempts
    leased, reserved = attempts.lease(admission.execution_id, expected_version=admission.status_version,
                                     owner_id="lost-worker")
    active, _ = attempts.activate(leased.attempt_id, generation=leased.generation,
                                 expected_execution_version=reserved.status_version)
    goal = goals.load_goal("goal-chat")
    goal["active_started_at"] = clock[0]
    goal["usage"]["active_elapsed_s"] = 7.0
    goals.save_goal("goal-chat", goal)
    clock[0] += 3600
    control.recover_owner_loss(admission.execution_id)
    chat.after_terminal(store, store.get_execution(admission.execution_id))
    recovered = goals.load_goal("goal-chat")
    assert recovered["usage"]["active_elapsed_s"] == pytest.approx(7)
    assert recovered["usage"]["active_time_known"] is False
    assert recovered["active_started_at"] is None
    clock[0] += 1000
    chat.after_terminal(store, store.get_execution(admission.execution_id))
    chat.refresh_usage("goal-chat")
    assert goals.load_goal("goal-chat")["usage"]["active_elapsed_s"] == pytest.approx(7)
    assert goals.load_goal("goal-chat")["active_started_at"] is None
    # No hidden cap is introduced. An explicit time ceiling cannot treat an
    # unobserved crash interval as free time, however.
    assert goals.budget_exhausted(goals.load_goal("goal-chat")) == ""
    goals.apply_goal_action("goal-chat", "budget", max_elapsed_s=100)
    assert goals.budget_exhausted(goals.load_goal("goal-chat")) == "elapsed_time_unknown"


def test_late_receipt_does_not_start_the_timer_of_a_paused_goal(runtime, monkeypatch):
    goals, chat, _ = runtime
    goals.apply_goal_action("goal-chat", "pause")
    now = time.time()
    monkeypatch.setattr(time, "time", lambda: now)
    chat.refresh_usage("goal-chat")
    monkeypatch.setattr(time, "time", lambda: now + 500)
    chat.refresh_usage("goal-chat")
    goal = goals.load_goal("goal-chat")
    assert goal["active_started_at"] is None
    assert goal["usage"]["active_elapsed_s"] == 0
