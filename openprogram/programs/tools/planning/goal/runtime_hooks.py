"""How a chat Goal reacts at the moments of a conversation turn.

A chat Goal runs as ordinary conversation turns, but the conversation runtime
sits below Programs and never imports the Goal. It names the moments
(``openprogram.runtime.conversation.turn_hooks``) and this module registers
the Goal's part in each when the Goal package is imported, which
``openprogram.programs`` does when it loads:

* admission — Goal turns and user turns are admitted under the Goal's lock
  (``chat.admit``);
* the turn — the turn binds the Goal it works for (``chat.turn_context``),
  a verification turn gets its verifier and its stream its identity
  (``verification``, ``presentation.event_sink``), a background turn's
  prompt carries the Goal and the conversation's own turn its note and todo
  reminder (``chat.instructions``, ``chat.goal_note``, ``chat.todo_reminder``);
* the end — the driver and the control plane settle the Goal's usage and
  next turn (``chat.settle_terminal``, ``chat.after_terminal``);
* a worker restart — the Goal's owner lock, its pause after the restart and
  whether a cut-off Goal turn may be continued (``ownership``, ``restart``);
* always-on — the standing Goal takes a wake before any check-in
  (``standing``).

Each handler looks its Goal function up when it is called, so a test that
replaces one (on this package or on ``chat``) is what the runtime reaches.
"""
from __future__ import annotations

import logging
from typing import Any

from openprogram.runtime.conversation import turn_hooks

_log = logging.getLogger(__name__)

OWNER = "goal"
# Sources whose chat turns a Goal can own.
_GOAL_SOURCES = frozenset({"web", "tui", "acp"})


def _admit(entry: Any, **admission: Any) -> Any:
    from openprogram.programs.tools.planning.goal import chat

    return chat.admit(entry, **admission)


def _turn_scope(request: Any, execution_id: str):
    if request.source not in _GOAL_SOURCES:
        return None
    from openprogram.programs.tools.planning.goal import chat

    return chat.turn_context(request.session_id, execution_id, getattr(request, "goal_context", None))


def _turn_events(on_event: Any, request: Any) -> Any:
    from openprogram.programs.tools.planning.goal.presentation import event_sink

    return event_sink(on_event, request)


class _Verification:
    """The verifier of a turn that checks a Goal's completion candidate."""

    def require_candidate(self, session_id: str, request: Any, *, execution_id: str | None) -> None:
        from openprogram.programs.tools.planning.goal import verification

        verification.require_candidate(session_id, request, execution_id=execution_id)

    def wrap_read(self, tool: Any, request: Any) -> Any:
        from openprogram.programs.tools.planning.goal import verification

        return verification.wrap_read(tool, request)

    def instructions(self, session_id: str, request: Any) -> str:
        from openprogram.programs.tools.planning.goal import verification

        return verification.instructions(session_id, request)


def _turn_prompt(request: Any) -> tuple[str, str]:
    from openprogram.programs.tools.planning.goal.chat import instructions

    return "goal", instructions(request.session_id, getattr(request, "branch_id", None))


def _turn_notes(request: Any, view: Any) -> list[tuple[str, str, dict]]:
    from openprogram.programs.tools.planning.goal import chat as goal_chat

    branch = getattr(request, "branch_id", None)
    try:
        note = goal_chat.goal_note(request.session_id, branch, (view or {}).get("goal"))
        todo = (view or {}).get("todo") or {}
        reminder = goal_chat.todo_reminder(request.session_id, branch, todo.get("turns_since_use"),
                                           todo.get("turns_since_reminder")) if view is not None else ""
    except Exception:  # noqa: BLE001 - a Goal store that cannot be read sends no note
        _log.warning("goal note not built for %s", request.session_id, exc_info=True)
        note, reminder = None, ""
    notes: list[tuple[str, str, dict]] = []
    if note is not None:
        text, disclosure, full = note
        notes.append(("goal", text, {"disclosure": disclosure, "full": full}))
    if reminder:
        notes.append(("todo_reminder", reminder, {}))
    return notes


def _turn_finished(executions: Any, execution: Any) -> None:
    # A busy or unavailable Goal is retried until the notice lands.
    from openprogram.programs.tools.planning.goal import chat

    chat.settle_terminal(executions, execution)


def _execution_terminal(executions: Any, execution: Any) -> None:
    import openprogram.programs.tools.planning.goal as goals
    from openprogram.programs.tools.planning.goal import chat

    try:
        chat.after_terminal(executions, execution)
    except (goals.GoalConflictError, goals.GoalStateUnavailable):
        # The Goal's lock stayed held or its usage is not in yet. The turn is
        # over either way, and whoever closes it (a Stop's cancelled reply,
        # resource release) goes on. The accounting is not lost: the turn's
        # owner retries it (``chat.settle_terminal``), and a Goal left on an
        # ended turn is settled when it is next read (``chat.recover_unsettled``).
        _log.info("Goal accounting of %s deferred", getattr(execution, "execution_id", ""), exc_info=True)


def _session_owner(store: Any, session_id: str):
    from openprogram.programs.tools.planning.goal.ownership import goal_owner

    return goal_owner(store, session_id)


def _restart_pause(store: Any, session_id: str) -> bool:
    from openprogram.programs.tools.planning.goal.restart import pause_for_worker_restart

    return pause_for_worker_restart(store, session_id)


class _RestartContinuation:
    """Whether a chat turn a restart cut off may go on as a new turn, for its Goal."""

    def locked(self, session_id: str):
        from openprogram.programs.tools.planning.goal import chat

        return chat.locked(session_id)

    def branch(self, session_id: str, request: dict) -> str | None:
        """The continued turn's branch: a Goal's turn admitted without one runs on the Goal's."""
        import openprogram.programs.tools.planning.goal as goals

        goal = goals.load_goal(session_id)
        return request.get("branch_id") or (
            (goal or {}).get("branch_id") if request.get("goal_context") else None)

    def check(self, store: Any, execution: Any, new_execution_id: str, request: dict) -> str | None:
        """Why the new turn may not continue (``goal_changed``, ``budget_exhausted``),
        or None; a turn that continues the Goal's is bound to it, with the cut-off
        turn accounted first."""
        import openprogram.programs.tools.planning.goal as goals
        from openprogram.programs.tools.planning.goal import chat

        goal = goals.load_goal(execution.session_id)
        expected = request.get("goal_context")
        if not expected and goal:
            created = goal.get("creation_execution_context") or {}
            if (created.get("execution_id") == execution.execution_id
                    and {key: created.get(key) for key in ("goal_id", "revision", "run_id")} == chat.identity(goal)):
                expected = chat.identity(goal)
                request["goal_context"] = expected
        if expected:
            if (not goal or goal.get("status") != "active" or goal.get("stop_requested")
                    or chat.identity(goal) != expected
                    or goal.get("execution_id") not in {execution.execution_id, new_execution_id}):
                return "goal_changed"
            if goal.get("execution_id") == execution.execution_id:
                if goal.get("usage_pending_until") is not None:
                    goals.accumulate_goal_usage(execution.session_id, goal, until=goal["usage_pending_until"])
                if goal.get("accounted_execution_id") != execution.execution_id:
                    goals.accumulate_goal_usage(execution.session_id, goal)
                    goals.checkpoint_active_elapsed(goal, stop=True)
                    goal["turns_used"] = int(goal.get("turns_used") or 0) + 1
                    goal["run_turns"] = int(goal.get("run_turns") or 0) + 1
                    goal["accounted_execution_id"] = execution.execution_id
                exhausted = goals.budget_exhausted(goal)
                if exhausted:
                    goal.update(status="budget_exhausted", phase="terminal", last_reason=exhausted)
                chat.publish(execution.session_id, goal)
                if goal.get("usage_pending_until") is not None:
                    raise goals.GoalStateUnavailable("Interrupted Goal usage settlement is pending")
                if request.get("goal_trigger"):
                    chat.charge_daily_budget(execution.session_id, goal, execution.execution_id)
                if exhausted:
                    return "budget_exhausted"
            request.update(goal_trigger=True, goal_previous_execution=execution.execution_id)
        elif (goal and goal.get("execution_mode") == "chat" and goal.get("status") == "active"
              and chat.on_goal_branch(goal, request.get("branch_id"))):
            # A new/revised Goal is not authority to continue the abandoned
            # task. A Goal on another branch does not concern this turn.
            return "goal_changed"
        return None


def register() -> None:
    from openprogram.programs.tools.planning.goal.standing import StandingGoal

    turn_hooks.register("turn_admission", OWNER, _admit)
    turn_hooks.register("turn_scope", OWNER, _turn_scope)
    turn_hooks.register("turn_events", OWNER, _turn_events)
    turn_hooks.register("verification_turn", OWNER, _Verification())
    turn_hooks.register("turn_prompt", OWNER, _turn_prompt)
    turn_hooks.register("turn_notes", OWNER, _turn_notes)
    turn_hooks.register("turn_finished", OWNER, _turn_finished)
    turn_hooks.register("execution_terminal", OWNER, _execution_terminal)
    turn_hooks.register("session_owner", OWNER, _session_owner)
    turn_hooks.register("restart_pause", OWNER, _restart_pause)
    turn_hooks.register("restart_continuation", OWNER, _RestartContinuation())
    turn_hooks.register("wake", OWNER, StandingGoal())
