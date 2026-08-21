"""The continuation loop — called by dispatcher.process_user_turn after
every completed turn: judge the goal, apply the stop rules, launch
``goal_continue`` turns until the goal is met or a rule fires."""
from __future__ import annotations

import logging
import time
from dataclasses import replace
from typing import Any, Callable, Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .judge import evaluate_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal

_log = logging.getLogger(__name__)


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

        goal = _goal.load_goal(prev_req.session_id)
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
            goal.pop("last_question_options", None)
        if goal.get("status") != "active":
            return result

        verdict, reason, question, options = _goal.evaluate_goal(
            prev_req.session_id, goal,
            agent_id=prev_req.agent_id,
            spawn_caller=getattr(result, "assistant_msg_id", None),
        )

        # The background refinement may have landed spec/checklist AFTER
        # this iteration loaded its snapshot (the judge turn takes
        # minutes). Writing the snapshot back verbatim would erase them
        # — adopt the freshly stored fields before any save below.
        _goal._adopt_refinement(prev_req.session_id, goal)

        goal["turns_used"] = int(goal.get("turns_used") or 0) + 1
        goal["last_reason"] = reason
        max_turns = goal.get("max_turns")  # None = unlimited (the default)

        if verdict == "met":
            goal["status"] = "achieved"
            goal["judge_parse_failures"] = 0
            _goal._finish(prev_req.session_id, goal, on_event)
            return result

        degraded_question = ""
        if verdict == "needs_user":
            last_q_at = float(goal.get("last_question_at") or 0)
            if last_q_at and (time.time() - last_q_at
                              < _goal.QUESTION_MIN_INTERVAL_SECONDS):
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
                goal["last_question_options"] = options
                goal["last_question_at"] = time.time()
                _goal._finish(prev_req.session_id, goal, on_event)
                _goal._emit_goal_question(on_event, prev_req.session_id,
                                          question)
                return result

        if verdict == "judge_failure":
            failures = int(goal.get("judge_parse_failures") or 0) + 1
            goal["judge_parse_failures"] = failures
            if failures >= _goal.JUDGE_PARSE_FAILURE_LIMIT:
                goal["status"] = "error"
                goal["last_reason"] = (
                    f"judge failed {failures} times in a row: {reason}")
                _goal._finish(prev_req.session_id, goal, on_event)
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
            _goal._finish(prev_req.session_id, goal, on_event)
            return result

        # Progress stall: zero-tool spin has a read-only twin — the
        # model calls inspection tools every turn but never advances the
        # deliverable (observed live: 15 consecutive rounds, byte-equal
        # MD5 each time). The checklist tick count is the loop's own
        # progress meter: STALL_ROUND_LIMIT consecutive unmet rounds
        # without a new tick → stop.
        items = [it for it in (goal.get("checklist") or [])
                 if isinstance(it, dict)]
        if verdict == "unmet" and items and prev_req.source == "goal_continue":
            done_count = sum(1 for it in items if it.get("done"))
            prev_done = goal.get("last_done_count")
            stalled = prev_done is not None and done_count <= int(prev_done)
            goal["stall_rounds"] = (int(goal.get("stall_rounds") or 0) + 1
                                    if stalled else 0)
            goal["last_done_count"] = done_count
            if goal["stall_rounds"] >= _goal.STALL_ROUND_LIMIT:
                goal["status"] = "error"
                goal["last_reason"] = (
                    f"checklist stuck at {done_count}/{len(items)} for "
                    f"{goal['stall_rounds']} consecutive rounds: {reason}")
                _goal._finish(prev_req.session_id, goal, on_event)
                return result
        elif items:
            goal["last_done_count"] = sum(
                1 for it in items if it.get("done"))
            goal["stall_rounds"] = 0

        if max_turns and goal["turns_used"] >= int(max_turns):
            goal["status"] = "capped"
            _goal._finish(prev_req.session_id, goal, on_event)
            return result

        # Still active — persist progress, announce, run the next turn.
        try:
            _goal.save_goal(prev_req.session_id, goal)
        except Exception:
            _log.warning("goal progress write failed for session %s",
                         prev_req.session_id, exc_info=True)
        _goal._emit_goal_update(on_event, prev_req.session_id, goal)

        if degraded_question:
            next_text = (f"[goal] 需要决定：{degraded_question} "
                         "提问额度已用（1 小时内最多问一次），自行选择最"
                         "合理的方案继续，把决定和理由写清楚。")
        else:
            next_text = (f"[goal] 未达成：{reason or '目标条件尚未满足'}。"
                         "继续——本轮必须实际动手：用工具修改交付物，"
                         "不调用任何工具的回复会被判定为放弃并终止目标。")
        undone = [(i, it) for i, it in enumerate(goal.get("checklist") or [], 1)
                  if isinstance(it, dict) and not it.get("done")]
        if undone:
            next_text += "\n未完成项：\n" + "\n".join(
                f"{i}. {it.get('text')}" for i, it in undone)
        from openprogram.agent.authority import runtime_authority
        next_req = replace(
            prev_req,
            user_text=next_text,
            source="goal_continue",
            user_msg_id=None,
            user_already_persisted=False,
            branch_from=_goal._inherit_parent(),
            history_override=None,
            attachments=None,
            spawn_caller=None,
            tools_override=_goal._tools_with_forced_web_search(
                prev_req.tools_override),
            **runtime_authority(prev_req, "goal_continue"),
        )
        result = run_turn(next_req, on_event=on_event,
                          cancel_event=cancel_event)
        prev_req = next_req


def apply_callable_verdict(goal: dict, verdict: str, reason: str) -> str | None:
    """Stop rules for the public :func:`goal` callable.

    Returns a terminal status (``achieved``, ``error``, ``capped``) or
    ``None`` to keep working. Does not write session meta: a nested
    Workflow must not overwrite a user's ``/goal``.
    """
    goal["last_reason"] = reason
    if verdict == "met":
        goal["status"] = "achieved"
        goal["judge_parse_failures"] = 0
        return "achieved"
    if verdict == "judge_failure":
        failures = int(goal.get("judge_parse_failures") or 0) + 1
        goal["judge_parse_failures"] = failures
        if failures >= _goal.JUDGE_PARSE_FAILURE_LIMIT:
            goal["status"] = "error"
            goal["last_reason"] = (
                f"judge failed {failures} times in a row: {reason}")
            return "error"
        return None
    goal["judge_parse_failures"] = 0
    max_turns = goal.get("max_turns")
    if max_turns and int(goal.get("turns_used") or 0) >= int(max_turns):
        goal["status"] = "capped"
        return "capped"
    return None


def _inherit_parent():
    from openprogram.agent.dispatcher.types import INHERIT_PARENT
    return INHERIT_PARENT
