"""Public Goal Workflow: run work until the session Goal judge accepts it.

Other Workflows call :func:`goal`. This is the same Goal package as
session ``/goal`` — spec refinement, ``evaluate_goal``, stop rules, and
notices — not the original ``agent`` + ``llm`` primitive. Session
``/goal`` additionally persists meta and continues whole chat turns
through :func:`continue_goal_turns`.
"""
from __future__ import annotations

from openprogram.agentic_programming.function import agentic_function


@agentic_function
def goal(
    prompt: str,
    condition: str,
    *,
    model: str = "",
    effort: str = "",
    max_rounds: int = 10,
    timeout_s: float | None = None,
) -> str:
    """Run a goal loop: agent works, the Goal judge checks completion.

    Args:
        prompt: Task prompt for the agent
        condition: Success condition to judge
        model: Model override for the working agent
        effort: Reasoning effort override
        max_rounds: Max judgment rounds
        timeout_s: Timeout per agent round

    Returns:
        Final result when the condition is met, or the last result if
        max_rounds is reached.
    """
    from openprogram.agentic_programming.agent import agent
    from openprogram.agentic_programming.function import current_session_id
    import openprogram.programs.workflow.goal as _goal

    sid = current_session_id()
    spec = ""
    items: list[str] = []
    try:
        spec, items = _goal.refine_goal_spec_candidate(condition, session_id=sid)
    except Exception:
        spec, items = "", []

    goal_state: dict = {
        "text": condition,
        "spec": spec or None,
        "checklist": [{"text": t, "done": False} for t in items] or None,
        "status": "active",
        "turns_used": 0,
        "max_turns": max_rounds if max_rounds and max_rounds > 0 else None,
        "last_reason": "",
        "judge_parse_failures": 0,
    }

    work_prompt = prompt
    last_result = ""
    rounds = max_rounds if max_rounds and max_rounds > 0 else 1
    for _round_num in range(rounds):
        last_result = agent(
            prompt=work_prompt,
            model=model,
            effort=effort,
            timeout_s=timeout_s,
        )
        goal_state["turns_used"] = int(goal_state.get("turns_used") or 0) + 1
        if sid:
            _goal._adopt_refinement(sid, goal_state)
        verdict, reason, question, _options = _goal.evaluate_goal(
            sid, goal_state, agent_id="main",
        )
        terminal = _goal.apply_callable_verdict(goal_state, verdict, reason)
        if terminal:
            label = _goal._TERMINAL_LABELS.get(terminal)
            if sid and label:
                detail = str(goal_state.get("last_reason") or "").strip()
                _goal._emit_goal_notice(
                    sid,
                    f"[goal] {label}：{detail}" if detail else f"[goal] {label}",
                )
            return last_result
        undone = [
            (i, item)
            for i, item in enumerate(goal_state.get("checklist") or [], 1)
            if isinstance(item, dict) and not item.get("done")
        ]
        if verdict == "needs_user" and question:
            work_prompt = (
                f"{prompt}\n\n[goal] 需要决定：{question} "
                "自行选择最合理的方案继续，把决定和理由写清楚。"
            )
        else:
            work_prompt = (
                f"{prompt}\n\n[goal] 未达成：{reason or '目标条件尚未满足'}。继续。"
            )
        if undone:
            work_prompt += "\n未完成项：\n" + "\n".join(
                f"{i}. {item.get('text')}" for i, item in undone
            )
    return last_result


__all__ = ["goal"]
