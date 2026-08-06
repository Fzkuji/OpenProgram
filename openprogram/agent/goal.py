"""Session goals — a per-session condition the agent keeps working toward.

``/goal <condition>`` stores a goal in the session meta. After every
completed turn the dispatcher asks :func:`continue_goal_turns` whether
the goal is met; while it is not (and no stop rule fires) the loop
launches a follow-up turn (``source="goal_continue"``). Every
continuation turn is persisted, committed and compacted like any
user-sent turn.

Evaluation is one no-tools LLM judge call over the goal text plus the
branch tail, returning strict JSON ``{"met": bool, "reason": str}``.

The two LLM judgment points are agentic functions —
``goal_judge`` / ``goal_verify`` in
``openprogram/functions/agentics/goal/`` (prompts in their
docstrings, runnable from the Functions panel). This module keeps the
deterministic control flow: retry accounting, stop rules, budgets,
state writes.

The judge is separate from the working model on purpose: agents that
self-report completion (Codex / Cline style) systematically declare
victory early, so the verdict must come from outside the working
context. Design doc: docs/reference/design/runtime/goal.md.

Goal meta shape (``session extra_meta["goal"]``)::

    {"text": str, "status": "active" | "achieved" |
     "cleared" | "capped" | "error", "created_at": float,
     "turns_used": int, "max_turns": int, "last_reason": str,
     "judge_parse_failures": int}
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import Any, Callable, Optional

_log = logging.getLogger(__name__)

JUDGE_TAIL_MESSAGES = 8
JUDGE_TAIL_MAX_CHARS = 24_000  # ~8k tokens
JUDGE_PARSE_FAILURE_LIMIT = 3
DEFAULT_MAX_TURNS = 20

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


def default_max_turns() -> int:
    """``goal.max_turns`` from config.json (config_schema setting), 20
    when unset/unreadable."""
    try:
        from openprogram import setup as _setup
        v = (_setup._read_config().get("goal") or {}).get("max_turns")
        n = int(v) if v is not None else DEFAULT_MAX_TURNS
        return n if n > 0 else DEFAULT_MAX_TURNS
    except Exception:
        return DEFAULT_MAX_TURNS


def _emit_goal_update(on_event: Optional[Callable], session_id: str,
                      goal: dict) -> None:
    """Fan the goal state out: dispatcher event stream (for the calling
    surface) + webui broadcast (all connected tabs; best-effort — the
    server may not be running, e.g. pure-CLI use or tests)."""
    payload = {
        "type": "goal_update",
        "session_id": session_id,
        "goal": {k: goal.get(k) for k in (
            "text", "status", "turns_used", "max_turns",
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
# Evaluation — LLM judge
# ---------------------------------------------------------------------------

def _message_blocks(msg: dict) -> list[dict]:
    """Parsed ``extra.blocks`` of a persisted assistant row (may be a
    JSON string or an already-parsed dict)."""
    extra = msg.get("extra")
    if isinstance(extra, str) and extra:
        try:
            extra = json.loads(extra)
        except (ValueError, TypeError):
            return []
    if not isinstance(extra, dict):
        return []
    blocks = extra.get("blocks")
    return blocks if isinstance(blocks, list) else []


def render_branch_tail(session_id: str, *,
                       max_messages: int = JUDGE_TAIL_MESSAGES,
                       max_chars: int = JUDGE_TAIL_MAX_CHARS) -> str:
    """Plain-text tail of the active branch: last messages' content plus
    each assistant row's tool results. Tail-biased on purpose — the
    stock ``render_session_transcript`` keeps the HEAD and drops later
    turns, which is the wrong end for judging recent progress."""
    from openprogram.store.session.transcript import _clip
    try:
        branch = _db().get_branch(session_id) or []
    except Exception:
        branch = []
    parts: list[str] = []
    for m in branch[-max_messages:]:
        role = m.get("role") or "?"
        content = _clip(m.get("content"), 2000)
        parts.append(f"[{role}] {content}" if content else f"[{role}]")
        for blk in _message_blocks(m):
            if blk.get("type") != "tool":
                continue
            status = "FAILED: " if blk.get("is_error") else ""
            result = _clip(blk.get("result"), 600)
            parts.append(f"  [tool {blk.get('tool')}] {status}{result}")
    text = "\n".join(parts)
    return text[-max_chars:]


def _judge_runtime(agent_id: str, model_override: Optional[str]):
    """A ``Runtime`` on the session's configured model, for ``goal_judge``.

    The provider registry's ``fast`` flag is a speed tier for the SAME
    model (service_tier / speed:"fast" request bodies), not a cheaper
    judge model — so the session's main model is the judge model.
    The Model instance ``resolve_model`` produced (including its
    custom-model fallback and claude-code→anthropic relabel) is stamped
    onto the runtime directly, so the judge uses exactly the model the
    chat path would. Module-level so tests monkeypatch it directly.
    """
    from openprogram.agent.internals._model_tools import (
        load_agent_profile, resolve_model,
    )
    from openprogram.agentic_programming.runtime import Runtime

    model = resolve_model(load_agent_profile(agent_id), model_override)
    rt = Runtime(model="pending")  # legacy-shape ctor; real model below
    rt.api_model = model
    rt.model = f"{model.provider}:{model.id}"
    rt.provider_id = model.provider
    return rt


def _evaluate_with_llm_judge(
    session_id: str, goal: dict, *, agent_id: str,
    model_override: Optional[str],
) -> tuple[str, str, str]:
    """``("met"|"unmet"|"needs_user"|"judge_failure", reason, question)``.

    The judgment itself is the ``goal_judge`` agentic function
    (``openprogram/functions/agentics/goal/``); this wrapper keeps the
    deterministic accounting: one retry on a malformed reply or a
    provider hiccup, both attempts failing counts as ONE judge failure
    (the loop stops after ``JUDGE_PARSE_FAILURE_LIMIT`` consecutive).
    The pause decision lives HERE, in the verification step that
    already runs each turn — not in the working model's own restraint:
    need_user=true (only for the four critical cases in the judge
    prompt) pauses the loop with the question for the user."""
    from openprogram.functions.agentics.goal import goal_judge

    tail = render_branch_tail(session_id)
    last_error = "judge reply was not valid JSON"
    for _attempt in range(2):
        try:
            data = goal_judge(
                goal=goal.get("text") or "",
                transcript_tail=tail,
                runtime=_judge_runtime(agent_id, model_override),
            )
        except Exception as e:  # noqa: BLE001 — provider hiccup / bad JSON = one attempt
            last_error = f"judge call failed: {type(e).__name__}: {e}"
            continue
        if data["met"]:
            return "met", data["reason"], ""
        question = data["question"].strip()
        if data["need_user"] and question:
            return "needs_user", data["reason"], question
        return "unmet", data["reason"], ""
    return "judge_failure", last_error, ""


def evaluate_goal(session_id: str, goal: dict, *, agent_id: str,
                  model_override: Optional[str]) -> tuple[str, str, str]:
    """``("met"|"unmet"|"needs_user"|"judge_failure", reason, question)``
    for the goal, from the LLM judge."""
    return _evaluate_with_llm_judge(
        session_id, goal, agent_id=agent_id, model_override=model_override)


# ---------------------------------------------------------------------------
# Active verification — evidence gathered from the world, not the tail
# ---------------------------------------------------------------------------

def _actively_verify(session_id: str, goal: dict, verdict: str,
                     reason: str, question: str, *, agent_id: str,
                     spawn_caller: Optional[str]) -> tuple[bool, str]:
    """``(confirmed, evidence_or_gap)`` for a stop verdict.

    A wrong "keep going" costs one more turn; a wrong STOP — a false
    completion, or an interruption that wasn't needed — is the
    expensive error. So stop verdicts from the cheap tail judge get a
    second opinion that trusts nothing: the ``goal_verify`` agentic
    function spawns an agent with inspection-only tools, given the
    goal and the CLAIM but no transcript, and gathers its own evidence
    from the working directory. It cannot inherit the working model's
    framing because it never sees it. Verifier failure fails OPEN
    (``goal_verify`` returns confirmed=True) — the cheap verdict is
    trusted, the loop must not brick on a verification hiccup.
    """
    from openprogram.functions.agentics.goal import goal_verify

    claim = ("目标已达成" if verdict == "met"
             else f"需要暂停并询问用户：{question}")
    if reason:
        claim = f"{claim}\n声称理由：{reason}"
    try:
        res = goal_verify(
            goal=goal.get("text") or "",
            claim=claim,
            session_id=session_id,
            spawn_caller=spawn_caller,
            agent_id=agent_id,
        )
    except Exception as e:  # noqa: BLE001 — fall back to the cheap verdict
        _log.warning("goal active verification failed for %s: %s",
                     session_id, e)
        return True, reason
    if res.get("confirmed") is True:
        return True, str(res.get("evidence") or "") or reason
    return False, (str(res.get("gap") or "")
                   or str(res.get("evidence") or "") or reason)


# ---------------------------------------------------------------------------
# The continuation loop — called by dispatcher.process_user_turn after
# every completed turn
# ---------------------------------------------------------------------------

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
            model_override=prev_req.model_override,
        )

        # Stop verdicts from the tail judge get actively verified —
        # evidence from the working directory, not the transcript. An
        # unconfirmed stop becomes an unmet with the verifier's gap as
        # the reason for the next continuation.
        if verdict in ("met", "needs_user"):
            confirmed, detail = _actively_verify(
                prev_req.session_id, goal, verdict, reason, question,
                agent_id=prev_req.agent_id,
                spawn_caller=getattr(result, "assistant_msg_id", None),
            )
            if confirmed:
                reason = detail
            else:
                verdict, reason, question = "unmet", f"核实未通过：{detail}", ""

        goal["turns_used"] = int(goal.get("turns_used") or 0) + 1
        goal["last_reason"] = reason
        max_turns = int(goal.get("max_turns") or DEFAULT_MAX_TURNS)

        if verdict == "met":
            goal["status"] = "achieved"
            goal["judge_parse_failures"] = 0
            _finish(prev_req.session_id, goal, on_event)
            return result

        if verdict == "needs_user":
            # The verification step decided the run must pause for the
            # user (irreversible action / missing access / direction-
            # deciding ambiguity / unrecoverable failure). No
            # continuation launches; the next real user turn resumes
            # the loop. Waiting consumes no budget beyond the turn
            # that just ran.
            goal["status"] = "waiting_user"
            goal["judge_parse_failures"] = 0
            goal["last_question"] = question
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

        if goal["turns_used"] >= max_turns:
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

        next_req = replace(
            prev_req,
            user_text=f"[goal] 未达成：{reason or '目标条件尚未满足'}。继续。",
            source="goal_continue",
            user_msg_id=None,
            user_already_persisted=False,
            branch_from=_inherit_parent(),
            history_override=None,
            attachments=None,
            spawn_caller=None,
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
    return {
        "text": (f"Goal set (LLM judge, up to {goal['max_turns']} turns): "
                 f"{goal['text']}"),
        "send_text": goal["text"],
    }


def _status_text(goal: Optional[dict]) -> str:
    if not goal:
        return "No goal set. /goal <condition> to set one."
    lines = [
        f"Goal [{goal.get('status')}]: {goal.get('text') or ''}",
        f"  turns: {int(goal.get('turns_used') or 0)}/"
        f"{int(goal.get('max_turns') or DEFAULT_MAX_TURNS)}",
    ]
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
