"""Session goals — a per-session condition the agent keeps working toward.

``/goal <condition>`` stores a goal in the session meta. After every
completed turn the dispatcher asks :func:`continue_goal_turns` whether
the goal is met; while it is not (and no stop rule fires) the loop
launches a follow-up turn (``source="goal_continue"``). Every
continuation turn is persisted, committed and compacted like any
user-sent turn.

Evaluation is one decision agent turn: the single ``goal`` agentic
function in ``openprogram/functions/agentics/goal/`` (prompt in its
docstring; the one panel-runnable entry) reads the session's compacted
context view plus the goal text and answers strict JSON
``{"met", "reason", "need_user", "question"}``. Only its "met" counts
as completion. This module keeps the deterministic control flow:
retry accounting, stop rules, budgets, state writes.

The judge is separate from the working model on purpose: agents that
self-report completion (Codex / Cline style) systematically declare
victory early, so the verdict must come from outside the working
context. Design doc: docs/reference/design/runtime/goal.md.

Goal meta shape (``session extra_meta["goal"]``)::

    {"text": str, "spec": str (refined specification; absent until the
     refinement step lands, judging falls back to text),
     "status": "active" | "waiting_user" | "achieved" |
     "cleared" | "capped" | "error", "created_at": float,
     "turns_used": int, "max_turns": int | None (None = unlimited),
     "last_reason": str, "last_question": str,
     "last_question_at": float, "judge_parse_failures": int}
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

JUDGE_PARSE_FAILURE_LIMIT = 3
# 提问限频：1 小时内最多问用户 1 次；超出的 needs_user 裁决降级为续轮。
QUESTION_MIN_INTERVAL_SECONDS = 3600.0

_CLEAR_VERBS = {"clear", "stop", "off", "cancel"}


# ---------------------------------------------------------------------------
# Goal meta read / write
# ---------------------------------------------------------------------------

def _db():
    from openprogram.agent.session_db import default_db
    return default_db()


def load_goal(session_id: str) -> Optional[dict]:
    """The session's goal dict, or ``None``. Re-read fresh each time so a
    ``/goal clear`` from another surface takes effect on the next check."""
    try:
        sess = _db().get_session(session_id) or {}
        goal = (sess.get("extra_meta") or {}).get("goal")
        return dict(goal) if isinstance(goal, dict) else None
    except Exception:
        _log.debug("goal read failed for session %s", session_id, exc_info=True)
        return None


def save_goal(session_id: str, goal: dict) -> None:
    _db().update_session(session_id, goal=dict(goal))


def default_max_turns() -> Optional[int]:
    """``goal.max_turns`` from config.json (config_schema setting).
    ``None`` — the default — means NO turn cap: like Claude Code's and
    Codex's stop hooks, runaway protection is the internal stop rules
    (3 consecutive judge failures, idle-spin detection) plus the user's
    own interrupt / ``/goal clear``, not a number. A positive value set
    explicitly is honoured."""
    try:
        from openprogram import setup as _setup
        v = (_setup._read_config().get("goal") or {}).get("max_turns")
        n = int(v) if v not in (None, "") else None
        return n if n and n > 0 else None
    except Exception:
        return None


def _emit_goal_update(on_event: Optional[Callable], session_id: str,
                      goal: dict) -> None:
    """Fan the goal state out: dispatcher event stream (for the calling
    surface) + webui broadcast (all connected tabs; best-effort — the
    server may not be running, e.g. pure-CLI use or tests)."""
    payload = {
        "type": "goal_update",
        "session_id": session_id,
        "goal": {k: goal.get(k) for k in (
            "text", "spec", "status", "turns_used", "max_turns",
            "last_reason", "last_question")},
    }
    if on_event is not None:
        try:
            on_event({"type": "chat_response", "data": dict(payload)})
        except Exception:
            _log.debug("goal on_event emit failed", exc_info=True)
    # 事件层 tap：goal 状态变化进总线（emit_safe 自己吞异常）。
    from openprogram.events import emit_safe
    emit_safe("goal.update", "system",
              {"session_id": session_id, "goal": dict(payload["goal"])},
              {"session": session_id})
    try:
        from openprogram.webui import server as _s
        _s._broadcast(json.dumps({"type": "goal_update", "data": payload},
                                 default=str))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Evaluation — one call to the `goal` agentic function
# ---------------------------------------------------------------------------

def evaluate_goal(session_id: str, goal: dict, *, agent_id: str,
                  spawn_caller: Optional[str] = None) -> tuple[str, str, str]:
    """``("met"|"unmet"|"needs_user"|"judge_failure", reason, question)``.

    One call to the ``goal`` agentic function
    (``openprogram/functions/agentics/goal/``) — the only judgment
    there is. It reads the session's compacted context view plus the
    goal text (inspection tools available, the agent decides whether to
    use them) and answers ``{"met", "reason", "need_user",
    "question"}``. One retry on a malformed reply or a turn failure;
    both attempts failing counts as ONE judge failure (the loop stops
    after ``JUDGE_PARSE_FAILURE_LIMIT`` consecutive). The pause
    decision lives in the same judgment — not in the working model's
    own restraint: the ask policy in the decision prompt depends on
    attended/unattended mode (``agent/attended.py``), and an empty
    question is treated as plain unmet. The 1-hour ask rate limit is
    enforced by the loop, not here."""
    from openprogram.agent.attended import is_attended
    from openprogram.functions.agentics.goal import goal as goal_decision

    last_error = "goal decision reply was not valid JSON"
    for _attempt in range(2):
        try:
            data = goal_decision(
                # Judge against the refined spec when the refinement
                # step has landed one; the raw one-liner otherwise.
                goal=goal.get("spec") or goal.get("text") or "",
                session_id=session_id,
                attended=is_attended(session_id),
                spawn_caller=spawn_caller,
                agent_id=agent_id,
            )
        except Exception as e:  # noqa: BLE001 — turn failure / bad JSON = one attempt
            last_error = f"goal decision failed: {type(e).__name__}: {e}"
            continue
        if data["met"]:
            return "met", data["reason"], ""
        question = data["question"].strip()
        if data["need_user"] and question:
            return "needs_user", data["reason"], question
        return "unmet", data["reason"], ""
    return "judge_failure", last_error, ""


# ---------------------------------------------------------------------------
# Spec refinement — runs right after /goal set, in the background
# ---------------------------------------------------------------------------

def refine_goal_spec(session_id: str) -> None:
    """Expand the goal's one-line text into a full specification and
    store it as ``goal["spec"]``. Blocking; ``_start_spec_refinement``
    runs it on a background thread so setting a goal never waits.

    Fail-open but never silent: any failure (spawn error, unparseable
    reply) leaves the goal without a spec — judging falls back to the
    raw text — and a system row tells the user the judge is working
    from the one-liner only. The goal is re-read after the refinement
    turn so a racing ``/goal clear`` or a replacement goal is never
    overwritten."""
    goal = load_goal(session_id)
    if not goal or goal.get("status") != "active" or goal.get("spec"):
        return
    text = goal.get("text") or ""
    try:
        from openprogram.functions.agentics.goal import refine as _refine
        spec = _refine(text, session_id=session_id)
    except Exception:
        _log.warning("goal spec refinement failed (fail-open) for session %s",
                     session_id, exc_info=True)
        _emit_goal_notice(session_id, (
            "[goal] 目标完善失败——判定将只按你的原始一句话核对，"
            "没有展开的验收清单。想要更严的判定可 /goal clear 后重设。"))
        return
    goal = load_goal(session_id)
    if (not goal or goal.get("status") not in ("active", "waiting_user")
            or (goal.get("text") or "") != text):
        return
    goal["spec"] = spec
    save_goal(session_id, goal)
    _emit_goal_update(None, session_id, goal)
    _emit_goal_spec_notice(session_id, spec)


def _start_spec_refinement(session_id: str) -> None:
    """Kick off :func:`refine_goal_spec` on a daemon thread. Tests stub
    this to keep the set flow synchronous."""
    import threading
    threading.Thread(
        target=refine_goal_spec, args=(session_id,),
        name=f"goal-refine-{session_id[:8]}", daemon=True,
    ).start()


def _emit_goal_spec_notice(session_id: str, spec: str) -> None:
    """Show the refined spec as a system row in the transcript (same
    ``local_command`` surface the /goal command replies use), so the
    user sees what the system understood the goal to be — ``/goal
    clear`` and a fresh ``/goal`` re-set if it misread the intent."""
    _emit_goal_notice(session_id, (
        f"[goal] 目标已完善为规格（判定按此逐条核对；"
        f"不满意可 /goal clear 重设）：\n{spec}"))


def _emit_goal_notice(session_id: str, content: str) -> None:
    """One system row in the transcript (``local_command`` envelope,
    webui broadcast; best-effort — absent server is a no-op)."""
    payload = {
        "type": "local_command",
        "session_id": session_id,
        "content": content,
    }
    try:
        from openprogram.webui import server as _s
        _s._broadcast(json.dumps(
            {"type": "chat_response", "data": payload}, default=str))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The continuation loop — called by dispatcher.process_user_turn after
# every completed turn
# ---------------------------------------------------------------------------

def _tools_with_forced_web_search(override: Any) -> Any:
    """The tools override for a ``goal_continue`` turn: the inherited
    per-turn config with ``web_search`` forced ON. Continuations are
    unattended autonomous work, so they get the search tool the user
    could have toggled on — per turn only, the session's persisted
    settings are untouched. Mirrors ``session_config._with_web_search``
    but also lifts ``None`` (fall back to agent profile) into a full
    dict intent and turns ``[]`` (all tools off) into web-search-only."""
    if override is None:
        return {"enabled": True, "web_search": True}
    if isinstance(override, dict):
        return {**override, "web_search": True}
    if isinstance(override, list):
        return override if "web_search" in override else [*override, "web_search"]
    return override

def continue_goal_turns(req: Any, result: Any, *, run_turn: Callable,
                        on_event: Optional[Callable] = None,
                        cancel_event: Any = None) -> Any:
    """Judge the session goal after a finished turn and keep launching
    continuation turns until it is met or a stop rule fires.

    ``run_turn`` is the dispatcher's single-turn primitive
    (``_process_turn_once``) — never ``process_user_turn`` itself, so
    the loop cannot recurse into a second loop. Returns the LAST
    TurnResult so callers still get "the result of my call".

    Stop rules (docs/reference/design/runtime/goal.md):
      * goal met → status="achieved"
      * turns_used reaches max_turns → status="capped"
      * consecutive judge failures reach 3 → status="error"
      * a continuation turn made zero tool calls and the goal is still
        unmet → status="error" (idle spin)
      * turn failed / cancelled / goal cleared → stop, status untouched
    """
    prev_req = req
    while True:
        if getattr(result, "failed", False):
            return result
        if cancel_event is not None and getattr(cancel_event, "is_set", lambda: False)():
            return result
        try:
            from openprogram.agent.run_control import is_cancelled
            if is_cancelled(prev_req.session_id):
                return result
        except Exception:
            pass

        goal = load_goal(prev_req.session_id)
        if not goal:
            return result
        if goal.get("status") == "waiting_user":
            # The loop paused with a question. A goal_continue turn can
            # never be the answer; a real user turn is — resume and
            # judge it like any other completed turn.
            if prev_req.source == "goal_continue":
                return result
            goal["status"] = "active"
            goal.pop("last_question", None)
        if goal.get("status") != "active":
            return result

        verdict, reason, question = evaluate_goal(
            prev_req.session_id, goal,
            agent_id=prev_req.agent_id,
            spawn_caller=getattr(result, "assistant_msg_id", None),
        )

        goal["turns_used"] = int(goal.get("turns_used") or 0) + 1
        goal["last_reason"] = reason
        max_turns = goal.get("max_turns")  # None = unlimited (the default)

        if verdict == "met":
            goal["status"] = "achieved"
            goal["judge_parse_failures"] = 0
            _finish(prev_req.session_id, goal, on_event)
            return result

        degraded_question = ""
        if verdict == "needs_user":
            last_q_at = float(goal.get("last_question_at") or 0)
            if last_q_at and (time.time() - last_q_at
                              < QUESTION_MIN_INTERVAL_SECONDS):
                # 限频：1 小时内已问过 → 不暂停，降级成续轮，让 agent
                # 自行选择方案继续（续轮 prompt 说明额度已用）。
                verdict = "unmet"
                degraded_question = question
                question = ""
            else:
                # The decision agent says the run must pause for the
                # user. No continuation launches; the next real user
                # turn resumes the loop. Waiting consumes no budget
                # beyond the turn that just ran. ``last_question_at``
                # persists across the resume — it is the rate-limit
                # clock, max one question per hour.
                goal["status"] = "waiting_user"
                goal["judge_parse_failures"] = 0
                goal["last_question"] = question
                goal["last_question_at"] = time.time()
                _finish(prev_req.session_id, goal, on_event)
                _emit_goal_question(on_event, prev_req.session_id, question)
                return result

        if verdict == "judge_failure":
            failures = int(goal.get("judge_parse_failures") or 0) + 1
            goal["judge_parse_failures"] = failures
            if failures >= JUDGE_PARSE_FAILURE_LIMIT:
                goal["status"] = "error"
                goal["last_reason"] = (
                    f"judge failed {failures} times in a row: {reason}")
                _finish(prev_req.session_id, goal, on_event)
                return result
        else:
            goal["judge_parse_failures"] = 0

        # Idle spin: a continuation turn that called zero tools while the
        # goal stayed unmet is going nowhere — stop instead of burning
        # the remaining budget on repeated apologies.
        if (prev_req.source == "goal_continue"
                and not getattr(result, "tool_calls", None)):
            goal["status"] = "error"
            goal["last_reason"] = (
                "continuation turn made no tool calls while the goal "
                f"stayed unmet: {reason}")
            _finish(prev_req.session_id, goal, on_event)
            return result

        if max_turns and goal["turns_used"] >= int(max_turns):
            goal["status"] = "capped"
            _finish(prev_req.session_id, goal, on_event)
            return result

        # Still active — persist progress, announce, run the next turn.
        try:
            save_goal(prev_req.session_id, goal)
        except Exception:
            _log.warning("goal progress write failed for session %s",
                         prev_req.session_id, exc_info=True)
        _emit_goal_update(on_event, prev_req.session_id, goal)

        if degraded_question:
            next_text = (f"[goal] 需要决定：{degraded_question} "
                         "提问额度已用（1 小时内最多问一次），自行选择最"
                         "合理的方案继续，把决定和理由写清楚。")
        else:
            next_text = f"[goal] 未达成：{reason or '目标条件尚未满足'}。继续。"
        next_req = replace(
            prev_req,
            user_text=next_text,
            source="goal_continue",
            user_msg_id=None,
            user_already_persisted=False,
            branch_from=_inherit_parent(),
            history_override=None,
            attachments=None,
            spawn_caller=None,
            tools_override=_tools_with_forced_web_search(
                prev_req.tools_override),
        )
        result = run_turn(next_req, on_event=on_event,
                          cancel_event=cancel_event)
        prev_req = next_req


def _inherit_parent():
    from openprogram.agent.dispatcher.types import INHERIT_PARENT
    return INHERIT_PARENT


def _emit_goal_question(on_event: Optional[Callable], session_id: str,
                        question: str) -> None:
    """Surface the pause question where the user reads: a system row in
    the transcript (``local_command`` envelope — same surface the /goal
    command's own notices use). The next user message is the answer."""
    payload = {
        "type": "local_command",
        "session_id": session_id,
        "content": f"[goal] 需要你的确认才能继续：{question}",
    }
    if on_event is not None:
        try:
            on_event({"type": "chat_response", "data": dict(payload)})
        except Exception:
            _log.debug("goal question emit failed", exc_info=True)
    try:
        from openprogram.webui import server as _s
        _s._broadcast(json.dumps(
            {"type": "chat_response", "data": payload}, default=str))
    except Exception:
        pass


def _finish(session_id: str, goal: dict, on_event: Optional[Callable]) -> None:
    try:
        save_goal(session_id, goal)
    except Exception:
        _log.warning("goal terminal write failed for session %s",
                     session_id, exc_info=True)
    _emit_goal_update(on_event, session_id, goal)


# ---------------------------------------------------------------------------
# /goal command — shared by the Rich REPL, the web chat handler and the
# commands registry
# ---------------------------------------------------------------------------

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
        return {"text": _status_text(load_goal(session_id)), "send_text": None}

    head = args.split()[0].lower()
    if head in _CLEAR_VERBS:
        goal = load_goal(session_id)
        if not goal or goal.get("status") not in ("active", "waiting_user"):
            return {"text": "No active goal to clear.", "send_text": None}
        goal["status"] = "cleared"
        save_goal(session_id, goal)
        _emit_goal_update(None, session_id, goal)
        return {"text": "Goal cleared.", "send_text": None}

    goal = {
        "text": args,
        "status": "active",
        "created_at": time.time(),
        "turns_used": 0,
        "max_turns": default_max_turns(),
        "last_reason": "",
        "judge_parse_failures": 0,
    }
    save_goal(session_id, goal)
    _emit_goal_update(None, session_id, goal)
    # Refine the one-liner into a full spec in the background — never
    # blocks the set, fail-open (no spec = judge uses the raw text).
    _start_spec_refinement(session_id)
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
    if goal.get("last_reason"):
        lines.append(f"  last reason: {goal['last_reason']}")
    if goal.get("status") == "waiting_user" and goal.get("last_question"):
        lines.append(f"  waiting on you: {goal['last_question']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Commands-registry handler (kind="local" builtin)
# ---------------------------------------------------------------------------

def goal_builtin_handler(session_ctx: dict, raw_args: str) -> dict:
    """``register_builtin`` handler contract: ``(session_ctx, raw_args)
    -> result dict``. Hosts read ``text`` for display and ``send_text``
    to launch the first turn."""
    return handle_goal_command(
        str((session_ctx or {}).get("session_id") or ""), raw_args or "")
