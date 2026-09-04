"""/goal command — shared by the Rich REPL, the web chat handler and
the commands registry: set / status / clear against a session."""
from __future__ import annotations

import math
import time
from typing import Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal


def _cancel_execution(session_id: str, goal: dict) -> None:
    execution_id = str(goal.get("execution_id") or "")
    if not execution_id:
        return
    try:
        from openprogram.agent.run_control import mark_cancelled
        mark_cancelled(
            session_id,
            execution_id=execution_id,
        )
    except Exception:
        pass


def _resume_invocation(goal: dict) -> dict:
    return {
        "name": "goal",
        "kwargs": {
            "prompt": goal.get("text") or "",
            "context_mode": "session",
            "resume": True,
            "expected_goal": {key: goal.get(key) for key in (
                "goal_id", "revision", "run_id", "version",
            )},
        },
    }


def apply_goal_action(session_id: str, action: str, **values) -> dict:
    """Apply one UI/TUI Goal action and return the committed projection."""
    goal = _goal.load_goal(session_id)
    if not goal:
        raise ValueError("No Goal exists for this session")
    _goal.check_goal_preconditions(goal, values.get("expected"))
    action = action.strip().lower()
    if action == "pause":
        if goal.get("status") not in _goal.RUNNING_STATUSES:
            raise ValueError("Only a running Goal can be paused")
        goal.update({
            "status": "paused",
            "phase": "paused",
            "recoverable": True,
            "pause_reason": "user",
            "last_reason": "Goal paused by the user.",
        })
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
        _cancel_execution(session_id, goal)
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
            "last_reason": "Goal was edited; resume to refine the new revision.",
        })
        goal.pop("spec", None)
        goal.pop("evidence_window", None)
        goal.pop("checklist", None)
        goal.pop("pending_answer", None)
        goal["pending_answers"] = []
        goal["questions"] = [
            {
                **item,
                "revision": item.get("revision", int(goal["revision"]) - 1),
                "status": (
                    "superseded"
                    if item.get("status") == "pending"
                    else item.get("status")
                ),
                **(
                    {"superseded_at": time.time()}
                    if item.get("status") == "pending"
                    else {}
                ),
            }
            for item in (goal.get("questions") or [])
            if isinstance(item, dict)
        ]
        goal.pop("last_question", None)
        goal.pop("last_question_id", None)
        goal.pop("last_question_options", None)
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
        _cancel_execution(session_id, goal)
    elif action == "answer":
        answer = str(values.get("answer") or "").strip()
        if not answer:
            raise ValueError("Goal answer cannot be empty")
        questions = [
            dict(item) for item in (goal.get("questions") or [])
            if isinstance(item, dict)
        ]
        pending = [item for item in questions if item.get("status") == "pending"]
        requested_id = str(values.get("question_id") or "")
        question = next(
            (item for item in pending if str(item.get("id") or "") == requested_id),
            pending[0] if pending and not requested_id else None,
        )
        if question is None:
            raise ValueError("No matching pending Goal question")
        question_id = str(question.get("id") or "")
        question.update({
            "status": "answered",
            "answer": answer,
            "answered_at": time.time(),
        })
        queued_answers = [
            dict(item) for item in (goal.get("pending_answers") or [])
            if isinstance(item, dict)
        ]
        queued_answers.append({
            "question_id": question_id,
            "prompt": str(question.get("prompt") or ""),
            "answer": answer,
        })
        goal["questions"] = questions
        goal["pending_answers"] = queued_answers
        remaining = [item for item in questions if item.get("status") == "pending"]
        if remaining:
            goal.update({
                "last_question": remaining[0].get("prompt") or "",
                "last_question_id": remaining[0].get("id") or "",
                "last_question_options": remaining[0].get("options") or [],
            })
        else:
            goal.pop("last_question", None)
            goal.pop("last_question_id", None)
            goal.pop("last_question_options", None)
        if goal.get("status") == "waiting_user":
            goal.update({
                "status": "paused",
                "phase": "answer_received",
                "recoverable": True,
                "pause_reason": "answer_received",
            })
        goal["last_reason"] = "User answer saved for the next Goal boundary."
        _goal.save_goal(session_id, goal)
    elif action == "budget":
        budget = dict(goal.get("budget") or {})
        for key in ("max_turns", "max_tokens", "max_elapsed_s", "max_cost_usd"):
            if key in values:
                raw = values[key]
                if raw in (None, "", 0, 0.0):
                    budget[key] = None
                    continue
                try:
                    parsed = (
                        float(raw)
                        if key in {"max_elapsed_s", "max_cost_usd"}
                        else int(raw)
                    )
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"{key} must be a positive number or zero") from exc
                if not math.isfinite(float(parsed)) or parsed < 0:
                    raise ValueError(f"{key} must be a positive number or zero")
                budget[key] = parsed
        goal["budget"] = budget
        goal["max_turns"] = budget.get("max_turns")
        _goal.save_goal(session_id, goal)
    elif action in {"clear", "cancel"}:
        goal.update({
            "status": "cancelled",
            "phase": "terminal",
            "recoverable": False,
            "last_reason": "Goal cancelled by the user.",
        })
        _goal.checkpoint_active_elapsed(goal, stop=True)
        _goal.save_goal(session_id, goal)
        _cancel_execution(session_id, goal)
    else:
        raise ValueError(f"Unknown Goal action: {action}")
    _goal._emit_goal_update(None, session_id, goal)
    return goal


def handle_goal_command(session_id: str, raw_args: str) -> dict:
    """Execute ``/goal <args>`` against a session.

    Set returns one invocation descriptor for the public ``goal()`` Workflow.
    Status and clear remain local operations against that Workflow's state.
    """
    if not session_id:
        return {"text": "No active session.", "send_text": None}
    args = (raw_args or "").strip()

    if not args:
        return {"text": _goal._status_text(_goal.load_goal(session_id)),
                "send_text": None}

    head = args.split()[0].lower()
    if head in _goal._CLEAR_VERBS:
        try:
            apply_goal_action(session_id, "cancel")
        except ValueError:
            return {"text": "No Goal to cancel.", "send_text": None}
        return {"text": "Goal cancelled.", "send_text": None}
    if head == "pause":
        try:
            apply_goal_action(session_id, "pause")
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        return {"text": "Goal paused.", "send_text": None}
    if head == "resume":
        goal = _goal.load_goal(session_id)
        if not goal or goal.get("status") not in _goal.RESUMABLE_STATUSES:
            return {"text": "No resumable Goal.", "send_text": None}
        return {
            "text": "Resuming Goal from its latest checkpoint.",
            "send_text": None,
            "invoke": _resume_invocation(goal),
        }
    if head == "answer":
        answer_args = args[len(args.split()[0]):].strip()
        pending = [
            item for item in ((_goal.load_goal(session_id) or {}).get("questions") or [])
            if isinstance(item, dict) and item.get("status") == "pending"
        ]
        requested_id = ""
        answer = answer_args
        first, separator, rest = answer_args.partition(" ")
        if separator and any(str(item.get("id") or "") == first for item in pending):
            requested_id, answer = first, rest.strip()
        try:
            answered = apply_goal_action(
                session_id, "answer", question_id=requested_id, answer=answer,
            )
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        result = {
            "text": "Goal answer saved; resuming from the latest checkpoint.",
            "send_text": None,
        }
        if answered.get("status") == "paused" and answered.get("phase") == "answer_received":
            result["invoke"] = _resume_invocation(answered)
        elif answered.get("status") == "paused":
            result["text"] = "Goal answer saved; the user-paused Goal remains paused."
        else:
            result["text"] = "Goal answer saved for the active execution."
        return result
    if head == "edit":
        prompt = args[len(args.split()[0]):].strip()
        try:
            edited = apply_goal_action(session_id, "edit", prompt=prompt)
        except ValueError as exc:
            return {"text": str(exc), "send_text": None}
        return {"text": f"Goal revision {edited.get('revision')} saved. Use /goal resume to continue.", "send_text": None}

    return {
        "text": f"Starting Goal Workflow with session context: {args}",
        "send_text": None,
        "invoke": {
            "name": "goal",
            "kwargs": {
                "prompt": args,
                "context_mode": "session",
            },
        },
    }


def _status_text(goal: Optional[dict]) -> str:
    if not goal:
        return "No goal set. /goal <prompt> to set one."
    cap = goal.get("max_turns")
    lines = [
        f"Goal [{goal.get('status')}]: {goal.get('text') or ''}",
        f"  id: {goal.get('goal_id') or 'legacy'} · revision {int(goal.get('revision') or 1)} · version {int(goal.get('version') or 0)}",
        f"  turns: {int(goal.get('turns_used') or 0)}"
        + (f"/{int(cap)}" if cap else ""),
    ]
    usage = goal.get("usage") or {}
    for name, role in (goal.get("roles") or {}).items():
        lines.append(
            f"  {name}: {role.get('provider')}/{role.get('model')} · "
            f"effort {role.get('effort')} · timeout {role.get('timeout_s')}s"
        )
    if goal.get("roles_origin") == "legacy-resolved":
        lines.append("  roles: resolved on first resume of a legacy Goal")
    if usage:
        lines.append(
            f"  usage: {int(usage.get('total_tokens') or 0)} tokens · "
            f"${float(usage.get('cost_usd') or 0):.4f}"
        )
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
    pending = [
        item for item in (goal.get("questions") or [])
        if isinstance(item, dict) and item.get("status") == "pending"
    ]
    if pending:
        lines.append(f"  pending questions: {len(pending)}")
        lines.extend(
            f"  [?] {item.get('id') or '?'}: {item.get('prompt') or ''}"
            for item in pending
        )
    if goal.get("checkpoint"):
        checkpoint = goal["checkpoint"]
        lines.append(
            f"  checkpoint: {checkpoint.get('phase') or '?'} after round "
            f"{int(checkpoint.get('round') or 0)}"
        )
    return "\n".join(lines)


def goal_builtin_handler(session_ctx: dict, raw_args: str) -> dict:
    """``register_builtin`` handler contract: ``(session_ctx, raw_args)
    -> result dict``. Hosts read ``text`` for display and ``send_text``
    to launch the first turn."""
    return _goal.handle_goal_command(
        str((session_ctx or {}).get("session_id") or ""), raw_args or "")
