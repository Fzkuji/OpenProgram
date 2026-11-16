"""/goal command — shared by the Rich REPL, the web chat handler and
the commands registry: set / status / clear against a session."""
from __future__ import annotations

import math
import shlex
from typing import Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.tools.planning.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.tools.planning.goal as _goal


BUDGET_KEYS = ("max_tokens", "max_elapsed_s", "max_cost_usd")


def _cancel_execution(session_id: str, goal: dict) -> None:
    _goal.request_goal_stop(goal, session_id)


def apply_goal_action(session_id: str, action: str, **values) -> dict:
    from . import chat
    with chat.locked(session_id):
        return _apply_goal_action(session_id, action, **values)


def _apply_goal_action(session_id: str, action: str, **values) -> dict:
    """Apply one UI/TUI Goal action and return the committed projection."""
    goal = _goal.load_goal(session_id)
    if not goal:
        raise ValueError("No Goal exists for this session")
    _goal.check_goal_preconditions(goal, values.get("expected"))
    action = action.strip().lower()
    if action == "verify":
        from .chat import verify_from_controls
        return verify_from_controls(session_id, values.get("expected"))
    goal["control_version"] = int(goal.get("control_version") or 0) + 1
    if action == "pause":
        if goal.get("status") not in _goal.RUNNING_STATUSES:
            raise ValueError("Only a running Goal can be paused")
        goal.update({
            "status": "paused",
            "phase": "paused",
            "recoverable": True,
            "pause_reason": "user",
            "stop_requested": True,
            "last_reason": "Goal paused by the user.",
        })
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
    elif action == "edit":
        prompt = str(values.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("Goal prompt cannot be empty")
        goal.update({
            "text": prompt,
            "revision": int(goal.get("revision") or 1) + 1,
            "status": "paused",
            "phase": "paused",
            "recoverable": True,
            "pause_reason": "edited",
            "stop_requested": True,
            "last_reason": "Goal was edited; resume to work on the new revision.",
        })
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
    elif action == "budget":
        budget = dict(goal.get("budget") or {})
        for key in BUDGET_KEYS:
            if key in values:
                parse = _goal.positive_int if key == "max_tokens" else _goal.positive_float
                budget[key] = parse(values[key], name=key)
        goal["budget"] = budget
        _goal.save_goal(session_id, goal)
    elif action in {"clear", "cancel"}:
        goal.update({
            "status": "cancelled",
            "phase": "terminal",
            "recoverable": False,
            "stop_requested": True,
            "last_reason": "Goal cancelled by the user.",
        })
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
    elif action == "stop":
        if not goal.get("stop_requested"):
            raise ValueError("No saved Goal stop request to retry")
    else:
        raise ValueError(f"Unknown Goal action: {action}")
    _goal._emit_goal_update(None, session_id, goal)
    if action in {"pause", "edit", "cancel", "clear", "stop"}:
        _cancel_execution(session_id, goal)
    return goal


def handle_goal_command(session_id: str, raw_args: str) -> dict:
    """Report unavailable storage without interpreting it as an absent Goal."""
    try:
        return _handle_goal_command(session_id, raw_args)
    except (_goal.GoalStateUnavailable, ValueError, _goal.GoalStopUnconfirmed) as exc:
        return {"text": str(exc), "send_text": None}


def _handle_goal_command(session_id: str, raw_args: str) -> dict:
    """Execute ``/goal <args>`` against a session; any other text sets a new Goal."""
    if not session_id:
        return {"text": "No active session.", "send_text": None}
    args = (raw_args or "").strip()

    if not args or args.lower() == "status":
        return {"text": _goal._status_text(_goal.load_goal(session_id), session_id),
                "send_text": None}

    head = args.split()[0].lower()
    if args.lower() == "help":
        return {"text": (
            "/goal | /goal status — show objective, usage, limits and steps\n"
            "/goal pause | resume | verify | clear | stop (retry saved stop)\n/goal edit <objective>\n"
            "/goal budget max_tokens=10000 max_elapsed_s=3600 max_cost_usd=5\n"
            "Zero removes a limit."
        ), "send_text": None}
    if head == "budget":
        try:
            options = _command_options(shlex.split(args)[1:], set(BUDGET_KEYS))
            if not options:
                raise ValueError("Usage: /goal budget max_tokens=10000 max_cost_usd=5 (zero removes a limit)")
            apply_goal_action(session_id, "budget", **options)
            return {"text": "Goal limits saved.", "send_text": None}
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
    if head in _goal._CLEAR_VERBS:
        try:
            apply_goal_action(session_id, "cancel")
        except ValueError:
            return {"text": "No Goal to cancel.", "send_text": None}
        return {"text": "Goal cancellation saved. " + _goal._status_text(_goal.load_goal(session_id), session_id), "send_text": None}
    if head == "stop":
        try:
            stopped = apply_goal_action(session_id, "stop")
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        return {"text": _goal._status_text(stopped, session_id), "send_text": None}
    if head == "pause":
        try:
            apply_goal_action(session_id, "pause")
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        return {"text": "Goal pause saved. " + _goal._status_text(_goal.load_goal(session_id), session_id), "send_text": None}
    if head == "verify":
        apply_goal_action(session_id, "verify")
        return {"text": "Verifying Goal evidence in an independent conversation turn.", "send_text": None}
    if head == "resume":
        goal = _goal.load_goal(session_id)
        if not goal or goal.get("status") not in _goal.RESUMABLE_STATUSES:
            return {"text": "No resumable Goal.", "send_text": None}
        from . import chat
        chat.resume(session_id)
        return {"text": "Resuming Goal in this conversation.",
                "send_text": "Continue the active Goal and its open task."}
    if head == "edit":
        prompt = args[len(args.split()[0]):].strip()
        try:
            edited = apply_goal_action(session_id, "edit", prompt=prompt)
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        return {"text": f"Goal revision {edited.get('revision')} saved. Use /goal resume to continue.", "send_text": None}

    from . import chat
    chat.create(session_id, args)
    # ``starts_goal`` marks the message that carries the objective; a surface
    # that draws that message labels it Goal and shows no ``text`` line.
    return {"text": "Goal saved; working in this conversation.", "send_text": args,
            "starts_goal": True}


def _command_options(parts, allowed):
    options = {}
    for part in parts:
        key, separator, value = part.partition("=")
        if not separator or key not in allowed or key in options:
            raise ValueError("Invalid or duplicate Goal setting: " + part)
        options[key] = value
    return options


def _status_text(goal: Optional[dict], session_id: str = "") -> str:
    if not goal:
        return "No goal set. /goal <prompt> to set one."
    lines = [
        f"Goal [{goal.get('status')}]: {goal.get('text') or ''}",
        f"  id: {goal.get('goal_id') or 'legacy'} · revision {int(goal.get('revision') or 1)} · version {int(goal.get('version') or 0)}",
    ]
    usage = goal.get("usage") or {}
    observed = _goal.goal_execution_state(goal, session_id)
    lines.append(
        "  execution: untracked"
        if observed["status"] == "untracked" else
        f"  execution: {observed['status']} · stop confirmed: {observed['finished'] is True}"
    )
    budget = goal.get("budget") or {}
    lines.append("  limits: " + ", ".join(
        f"{key}={budget.get(key) if budget.get(key) is not None else 'unlimited'}"
        for key in BUDGET_KEYS
    ))
    if usage:
        lines.append(
            f"  usage: {int(usage.get('total_tokens') or 0)} tokens · "
            + (f"${float(usage['cost_usd']):.4f}" if usage.get("cost_known") is True
               and isinstance(usage.get("cost_usd"), (int, float)) and math.isfinite(usage["cost_usd"])
               else "cost unknown")
        )
        qualifier = "at least " if usage.get("active_time_known") is False else ""
        suffix = "; interrupted interval unknown" if qualifier else ""
        lines.append(f"  active time: {qualifier}{float(usage.get('active_elapsed_s') or 0):.1f}s{suffix}")
    items = [it for it in (goal.get("checklist") or [])
             if isinstance(it, dict)]
    if items:
        done = sum(1 for it in items if it.get("done"))
        lines.append(f"  checklist: {done}/{len(items)}")
        lines.extend(f"  [ ] {it.get('text')}" for it in items
                     if not it.get("done"))
    if goal.get("last_reason"):
        lines.append(f"  last reason: {goal['last_reason']}")
    return "\n".join(lines)


def goal_builtin_handler(session_ctx: dict, raw_args: str) -> dict:
    """``register_builtin`` handler contract: ``(session_ctx, raw_args)
    -> result dict``. Hosts read ``text`` for display and ``send_text``
    to launch the first turn; ``starts_goal`` says that turn's message sets
    the Goal."""
    return _goal.handle_goal_command(
        str((session_ctx or {}).get("session_id") or ""), raw_args or "")
