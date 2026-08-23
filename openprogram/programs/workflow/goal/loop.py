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


IDLE_WARNING = (
    "[goal] 警告：上一轮没有使用任何工具。本轮必须实际动手使用工具，"
    "连续不使用工具会被判定为放弃并终止。"
)


def apply_idle_spin(goal: dict, used_tools: bool, verdict: str) -> str | None:
    """Two-stage zero-tool idle detection (OpenHands StuckDetector style).

    A round that used at least one tool resets the counter. A zero-tool
    round whose verdict is still ``unmet`` increments ``idle_rounds``:
    the first one gets a warning injected into the next work prompt
    (via :func:`next_work_prompt`), the second consecutive one stops
    the loop with ``status="error"``.
    """
    if used_tools:
        goal["idle_rounds"] = 0
        return None
    if verdict != "unmet":
        return None
    idle = int(goal.get("idle_rounds") or 0) + 1
    goal["idle_rounds"] = idle
    if idle < _goal.IDLE_ROUND_LIMIT:
        return None
    goal["status"] = "error"
    goal["last_reason"] = (
        f"idle spin: {idle} consecutive rounds without any tool use"
    )
    return "error"


def next_work_prompt(
    prompt: str,
    goal: dict,
    reason: str,
    *,
    user_answer: str = "",
    user_declined: bool = False,
) -> str:
    """Build the next working-agent instruction for the single loop."""
    if user_answer:
        text = (
            f"{prompt}\n\n[goal] 用户对上一项决定的回答：{user_answer}。"
            "依据这个回答继续完成目标。"
        )
    elif user_declined:
        text = (
            f"{prompt}\n\n[goal] 用户未回答上一个问题"
            f"（{reason or '需要一个决定'}）。自行选择最合理方案继续，"
            "在结果中写清你的决定与理由。"
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
    if int(goal.get("idle_rounds") or 0) >= 1:
        text += f"\n\n{IDLE_WARNING}"
    return text


__all__ = [
    "IDLE_WARNING",
    "apply_checklist_stall",
    "apply_goal_verdict",
    "apply_idle_spin",
    "next_work_prompt",
]
