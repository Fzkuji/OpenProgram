"""The standing Goal of an always-on conversation.

Always-on (``openprogram.runtime.conversation.always_on``) wakes a
conversation and, before any check-in, asks the standing work registered
for the ``wake`` turn moment whether it takes the wake. This is the chat
Goal's answer: a standing Goal set in the Agent's always-on configuration is
created and started, an unattended Goal nothing is running is started, a
stuck automatic one is resumed (or reported once), and one still running
keeps the wake. Stopping and restoring a conversation pause and restore its
unattended Goal; a check-in that left a Goal behind brings the next wake
forward. The runtime sits below the Goal and never imports it, so the
Goal's side of always-on lives here and reaches the wake's records through
``always_on.runtime``.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable

from openprogram.runtime.conversation.always_on import runtime as always_on
from openprogram.runtime.conversation.always_on import schedule, store

_log = logging.getLogger(__name__)

# Goals a standing Goal may replace. ``goal.chat.create`` refuses any other status.
GOAL_ENDED = {"achieved", "cancelled", "cleared", "impossible"}
# Goals a wake starts when nothing is running them: every one not typed by a person.
UNATTENDED_GOAL_ORIGINS = ("always_on", "check_in", "handoff", "scheduled")
# Unfinished Goals nothing moves on its own: failed, stalled, out of budget or blocked.
# ``paused`` is a person's own pause, and the waiting statuses belong to a Workflow Goal.
GOAL_STUCK = {"paused_recoverable", "blocked", "stalled", "budget_exhausted", "capped", "error", "failed"}
# Restarts of a failed automatic Goal in a row before a person is asked.
AUTOMATIC_GOAL_RESUMES = 3
# A chat turn still has not settled its Goal this long after it ended: it never will.
GOAL_SETTLE_GRACE_S = 60.0


def _goal(session_id: str) -> dict | None:
    import openprogram.programs.tools.planning.goal as goals
    return goals.load_goal(session_id)


def _execution_ended(execution_id: str) -> bool:
    """Whether the turn is over with nothing left to carry its Goal on.

    Asked only when no check-in is running, so a check-in's row with no ending
    yet is one a restart or crash cut off; recovery closes it later. A chat
    turn's end settles its Goal itself, in the moments after its row turns
    terminal: starting the Goal in that gap would take the turn from it.
    """
    from openprogram.programs.tools.planning.goal.chat import settled_at_turn_end
    from openprogram.runtime.execution import default_store
    from openprogram.runtime.execution.model import TERMINAL_EXECUTION_STATUSES
    from openprogram.runtime.execution.recovery.restart import is_check_in
    executions = default_store()
    execution = executions.get_execution(execution_id)
    if execution is None or is_check_in(executions, execution_id):
        return True
    if execution.status not in TERMINAL_EXECUTION_STATUSES:
        return False
    request = (executions.get_agent_turn_input(execution_id) or {}).get("request") or {}
    if not settled_at_turn_end(request):
        return True
    return time.time() - (execution.terminal_at or execution.updated_at) >= GOAL_SETTLE_GRACE_S


def _goal_has_no_runner(goal: dict | None, *, ended: str | None = None) -> bool:
    """An active unattended Goal nothing will continue. ``ended`` names a turn
    known to be over, for the moment its row may not say so yet. A Goal turn
    that finished and was accounted is continued by the Goal itself."""
    if (not goal or goal.get("status") != "active" or goal.get("stop_requested")
            or goal.get("origin") not in UNATTENDED_GOAL_ORIGINS
            or goal.get("continuation_restart") is not None):
        return False
    execution_id = goal.get("execution_id")
    if not execution_id or execution_id == ended:
        return True
    return goal.get("accounted_execution_id") != execution_id and _execution_ended(execution_id)


def _goal_stuck(goal: dict | None) -> bool:
    """An unfinished Goal that only someone's decision moves. A stop's own undo restores
    the Goals it paused, and a person's pause is theirs: neither is stuck."""
    from openprogram.programs.tools.planning.goal.chat import STOP_REASONS
    return bool(goal) and goal.get("status") in GOAL_STUCK and goal.get("pause_reason") not in STOP_REASONS


def _resumes_by_itself(session_id: str, goal: dict) -> bool:
    """An automatic Goal cut short by a failure or a restart, not yet restarted too often.
    A turn someone stopped is saved as a failure too, and a restart must not undo the Stop.
    A turn whose record is gone fails resume's check every time, so retrying would block for good."""
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal.chat import AUTOMATIC_RESUME_REASONS
    return (goal.get("origin") in UNATTENDED_GOAL_ORIGINS and goal.get("status") == "paused_recoverable"
            and goal.get("pause_reason") in AUTOMATIC_RESUME_REASONS
            and int(goal.get("automatic_resumes") or 0) < AUTOMATIC_GOAL_RESUMES
            and not goals.budget_exhausted(goal)
            and goals.goal_execution_state(goal, session_id)["status"] not in ("cancelled", "unavailable"))


def _resume_goal(session_id: str, spec, config: dict, goal: dict, now: float,
                 reason: str) -> dict[str, Any] | None:
    """Restart a stuck automatic Goal; ``None`` when it turns out it cannot be."""
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.permissions.identity import AUTOMATIC_GOAL_ORIGINS
    if goal.get("origin") in AUTOMATIC_GOAL_ORIGINS and always_on._budget_left(spec, now) == 0:
        return always_on._record(session_id, spec.id, config, now, reason, "budget",
                                 next_at=schedule.next_allowed(always_on._next_day(now), config), keep_reason=True)
    try:
        resumed = chat.resume_unattended(session_id)
    except (goals.GoalConflictError, goals.GoalStateUnavailable):
        return always_on._record(session_id, spec.id, config, now, reason, "busy",
                                 next_at=schedule.next_allowed(now + always_on.BUSY_RETRY_MINUTES * 60, config),
                                 keep_reason=True)
    except ValueError:
        return None
    return _start_goal(session_id, spec, config, resumed, now, reason)


def _stuck_goal_reason(session_id: str, goal: dict) -> str:
    import openprogram.programs.tools.planning.goal as goals
    status, pause = goal.get("status"), goal.get("pause_reason")
    if status in ("budget_exhausted", "capped"):
        return "It used up its limit."
    if status == "stalled":
        return "Its automatic turns stopped doing any work."
    if status == "blocked":
        return "It reported a blocker it could not get past."
    if pause == "execution_failed" and goals.goal_execution_state(goal, session_id)["status"] == "cancelled":
        return "Its turn was stopped, so it waits for you."
    if pause == "execution_failed":
        return "Its turn failed, and it cannot be restarted on its own."
    if pause == "restart_policy":
        return "A restart cut it off, and it cannot be restarted on its own."
    if pause == "plan_mode":
        return "It stopped because the conversation is in plan mode."
    return "It stopped and cannot continue on its own."


def _report_stuck_goal(session_id: str, goal: dict | None) -> None:
    """Tell the user once per stop that an automatic Goal needs them. A person's own Goal is
    left to its panel. A failed report is retried at the next wake; the check-in runs either way."""
    if not _goal_stuck(goal) or goal.get("origin") not in UNATTENDED_GOAL_ORIGINS:
        return
    stop = ":".join(str(goal.get(key) or "") for key in ("goal_id", "run_id", "status", "pause_reason"))
    if store.load_state(session_id).get("stuck_goal_reported") == stop:
        return
    detail = " ".join(str(goal.get("last_reason") or "").split())
    body = " ".join(filter(None, (_stuck_goal_reason(session_id, goal), f"Last reason: {detail}." if detail else "",
                                  "Resume, change or clear it from its Goal controls. "
                                  "Check-ins keep running meanwhile.")))
    try:
        always_on.report(session_id, "Goal stopped: " + " ".join(str(goal.get("text") or "").split()), body, kind="approval")
        store.update_state(session_id, stuck_goal_reported=stop)
    except Exception as exc:  # noqa: BLE001 - see above
        _log.warning("stuck Goal report for %s failed: %s: %s", session_id, type(exc).__name__, exc)


def _start_goal(session_id: str, spec, config: dict, goal: dict, now: float, reason: str) -> dict[str, Any]:
    """Start ``goal`` as a turn of the conversation itself."""
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat
    from openprogram.runtime.permissions.identity import AUTOMATIC_GOAL_ORIGINS
    if goal.get("origin") in AUTOMATIC_GOAL_ORIGINS:
        left = always_on._budget_left(spec, now)
        if left == 0:
            return always_on._record(session_id, spec.id, config, now, reason, "budget",
                                     next_at=schedule.next_allowed(always_on._next_day(now), config), keep_reason=True)
        if left is not None and reason != "manual" and always_on._agent_check_in_running(spec.id):
            # What is left today is not known until that check-in ends.
            return always_on._record(session_id, spec.id, config, now, reason, "busy",
                                     next_at=schedule.next_allowed(now + always_on.BUSY_RETRY_MINUTES * 60, config),
                                     keep_reason=True)
    try:
        if goals.budget_exhausted(goal):
            # Admission would refuse it at every wake, and only a turn's end closes it otherwise.
            chat.close_exhausted(session_id)
            return always_on._record(session_id, spec.id, config, now, reason, "budget")
        chat.start_from_controls(session_id, source=always_on.TURN_SOURCE)
    except goals.GoalConflictError:
        return always_on._record(session_id, spec.id, config, now, reason, "busy",
                                 next_at=schedule.next_allowed(now + always_on.BUSY_RETRY_MINUTES * 60, config),
                                 keep_reason=True)
    return always_on._record(session_id, spec.id, config, now, reason, "goal_started")


def _waiting_for_goal_lock(action: Callable[[], bool], wait_seconds: float) -> bool:
    import openprogram.programs.tools.planning.goal as goals
    deadline = time.monotonic() + wait_seconds
    while True:
        try:
            return action()
        except goals.GoalConflictError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


class StandingGoal:
    """The ``wake`` turn moment's standing work: the conversation's chat Goal."""

    def decide(self, session_id: str, spec, config: dict, state: dict, now: float,
               reason: str) -> dict[str, Any] | None:
        """The wake's outcome when the Goal takes it; None leaves it to a check-in."""
        goal = _goal(session_id)
        standing = (config.get("goal") or "").strip()
        applied = (state.get("applied_goal") or {}).get("text")
        unfinished = goal is not None and goal.get("status") not in GOAL_ENDED
        if standing and standing != applied and not unfinished:
            from openprogram.programs.tools.planning.goal import chat
            created = chat.create(session_id, standing, origin="always_on")
            store.update_state(session_id, applied_goal={"text": standing, "goal_id": created.get("goal_id")})
            return _start_goal(session_id, spec, config, created, now, reason)
        if _goal_has_no_runner(goal):
            return _start_goal(session_id, spec, config, goal, now, reason)
        if _goal_stuck(goal):
            started = (_resume_goal(session_id, spec, config, goal, now, reason)
                       if _resumes_by_itself(session_id, goal) else None)
            if started is not None:
                return started
            _report_stuck_goal(session_id, _goal(session_id))
        elif unfinished:
            status_text = str(goal.get("status") or "")
            outcome = "goal_paused" if status_text.startswith("paused") or goal.get("stop_requested") else "goal_in_progress"
            return always_on._record(session_id, spec.id, config, now, reason, outcome)
        return None

    def left_behind(self, session_id: str, ended_execution_id: str | None) -> bool:
        """Whether a check-in that ended left a Goal nothing will continue."""
        return _goal_has_no_runner(_goal(session_id), ended=ended_execution_id)

    def stop(self, session_id: str, reason: str, *, wait_seconds: float = 0.0) -> bool:
        """Pause the conversation's unattended Goal. Raises ``GoalConflictError`` if the lock stays held."""
        from openprogram.programs.tools.planning.goal import chat
        return _waiting_for_goal_lock(lambda: chat.pause_always_on(session_id, reason), wait_seconds)

    def restore(self, session_id: str, *, wait_seconds: float = 0.0) -> bool:
        """Restore the Goal a stop paused; True when one was restored."""
        from openprogram.programs.tools.planning.goal import chat
        return _waiting_for_goal_lock(lambda: chat.restore_always_on(session_id), wait_seconds)

    def relabel(self, session_id: str, reason: str, *, wait_seconds: float = 0.0) -> None:
        """Record ``reason`` as what still keeps the paused Goal stopped."""
        from openprogram.programs.tools.planning.goal import chat
        _waiting_for_goal_lock(lambda: chat.relabel_stop(session_id, reason), wait_seconds)

    def summary(self, session_id: str) -> dict[str, Any] | None:
        """The Goal as the always-on status shows it."""
        goal = _goal(session_id)
        return {"text": goal.get("text"), "status": goal.get("status")} if goal else None
