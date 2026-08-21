"""/goal command — shared by the Rich REPL, the web chat handler and
the commands registry: set / status / clear against a session."""
from __future__ import annotations

import time
from typing import Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal


def handle_goal_command(session_id: str, raw_args: str) -> dict:
    """Execute ``/goal <args>`` against a session.

    Returns ``{"text": <display text>, "send_text": <directive or None>}``.
    ``send_text`` is set only by the "set" form — the caller launches it
    as a normal turn so the goal work starts immediately.
    """
    if not session_id:
        return {"text": "No active session.", "send_text": None}
    args = (raw_args or "").strip()

    if not args:
        return {"text": _goal._status_text(_goal.load_goal(session_id)),
                "send_text": None}

    head = args.split()[0].lower()
    if head in _goal._CLEAR_VERBS:
        goal = _goal.load_goal(session_id)
        if not goal or goal.get("status") not in ("active", "waiting_user"):
            return {"text": "No active goal to clear.", "send_text": None}
        goal["status"] = "cleared"
        _goal.save_goal(session_id, goal)
        _goal._emit_goal_update(None, session_id, goal)
        return {"text": "Goal cleared.", "send_text": None}

    goal = {
        "text": args,
        "status": "active",
        "created_at": time.time(),
        "turns_used": 0,
        "max_turns": _goal.default_max_turns(),
        "last_reason": "",
        "judge_parse_failures": 0,
    }
    _goal.save_goal(session_id, goal)
    _goal._emit_goal_update(None, session_id, goal)
    # Refine the one-liner into a full spec in the background — never
    # blocks the set, fail-open (no spec = judge uses the raw text).
    _goal._start_spec_refinement(session_id)
    cap = goal["max_turns"]
    cap_note = f"up to {cap} turns" if cap else "no turn cap"
    return {
        "text": f"Goal set (LLM judge, {cap_note}): {goal['text']}",
        "send_text": goal["text"],
    }


def _status_text(goal: Optional[dict]) -> str:
    if not goal:
        return "No goal set. /goal <condition> to set one."
    cap = goal.get("max_turns")
    lines = [
        f"Goal [{goal.get('status')}]: {goal.get('text') or ''}",
        f"  turns: {int(goal.get('turns_used') or 0)}"
        + (f"/{int(cap)}" if cap else ""),
    ]
    if goal.get("spec"):
        spec = str(goal["spec"])
        lines.append("  spec: " + (spec[:300] + "…" if len(spec) > 300
                                   else spec))
    items = [it for it in (goal.get("checklist") or [])
             if isinstance(it, dict)]
    if items:
        done = sum(1 for it in items if it.get("done"))
        lines.append(f"  checklist: {done}/{len(items)}")
        lines.extend(f"  [ ] {it.get('text')}" for it in items
                     if not it.get("done"))
    if goal.get("last_reason"):
        lines.append(f"  last reason: {goal['last_reason']}")
    if goal.get("status") == "waiting_user" and goal.get("last_question"):
        lines.append(f"  waiting on you: {goal['last_question']}")
    return "\n".join(lines)


def goal_builtin_handler(session_ctx: dict, raw_args: str) -> dict:
    """``register_builtin`` handler contract: ``(session_ctx, raw_args)
    -> result dict``. Hosts read ``text`` for display and ``send_text``
    to launch the first turn."""
    return _goal.handle_goal_command(
        str((session_ctx or {}).get("session_id") or ""), raw_args or "")
