"""Deterministic state transitions for the single public ``goal()`` loop."""
from __future__ import annotations

import openprogram.programs.workflow.goal as _goal


def apply_goal_verdict(goal: dict, verdict: str, reason: str) -> str | None:
    """Apply one judge verdict and return a terminal status when finished."""
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
                f"judge failed {failures} times in a row: {reason}"
            )
            return "error"
    else:
        goal["judge_parse_failures"] = 0
    max_turns = goal.get("max_turns")
    if max_turns and int(goal.get("turns_used") or 0) >= int(max_turns):
        goal["status"] = "capped"
        return "capped"
    return None


def apply_checklist_stall(goal: dict, verdict: str, reason: str) -> str | None:
    """Stop after the checklist makes no progress for the shared limit."""
    items = [
        item for item in (goal.get("checklist") or [])
        if isinstance(item, dict)
    ]
    if not items:
        return None
    done_count = sum(1 for item in items if item.get("done"))
    if verdict != "unmet":
        goal["last_done_count"] = done_count
        goal["stall_rounds"] = 0
        return None
    previous = goal.get("last_done_count")
    stalled = previous is not None and done_count <= int(previous)
    goal["stall_rounds"] = (
        int(goal.get("stall_rounds") or 0) + 1 if stalled else 0
    )
    goal["last_done_count"] = done_count
    if goal["stall_rounds"] < _goal.STALL_ROUND_LIMIT:
        return None
    goal["status"] = "error"
    goal["last_reason"] = (
        f"checklist stuck at {done_count}/{len(items)} for "
        f"{goal['stall_rounds']} consecutive rounds: {reason}"
    )
    return "error"


def next_work_prompt(
    prompt: str,
    goal: dict,
    reason: str,
    *,
    user_answer: str = "",
) -> str:
    """Build the next working-agent instruction for the single loop."""
    if user_answer:
        text = (
            f"{prompt}\n\n[goal] 用户对上一项决定的回答：{user_answer}。"
            "依据这个回答继续完成目标。"
        )
    else:
        text = (
            f"{prompt}\n\n[goal] 未达成："
            f"{reason or '目标条件尚未满足'}。继续。"
        )
    undone = [
        (index, item)
        for index, item in enumerate(goal.get("checklist") or [], 1)
        if isinstance(item, dict) and not item.get("done")
    ]
    if undone:
        text += "\n未完成项：\n" + "\n".join(
            f"{index}. {item.get('text')}" for index, item in undone
        )
    return text


__all__ = ["apply_checklist_stall", "apply_goal_verdict", "next_work_prompt"]
