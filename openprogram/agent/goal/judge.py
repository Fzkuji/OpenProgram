"""Evaluation — one call to the ``goal`` agentic function. The only
judgment there is; the loop's retry accounting and stop rules live in
``loop.py``."""
from __future__ import annotations

from typing import Optional


def evaluate_goal(
    session_id: str, goal: dict, *, agent_id: str,
    spawn_caller: Optional[str] = None,
) -> tuple[str, str, str, list[dict]]:
    """``("met"|"unmet"|"needs_user"|"judge_failure", reason, question,
    options)`` — ``options`` is the judge's optional list of
    ``{"label", "description"}`` one-click answers for the question
    (empty for every non-``needs_user`` verdict).

    One call to the ``goal`` agentic function
    (``openprogram/programs/functions/agentic/workflow/goal/``) — the only judgment
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
    from openprogram.programs.functions.agentic.workflow.goal import goal as goal_decision

    items = [it for it in (goal.get("checklist") or [])
             if isinstance(it, dict)]
    last_error = "goal decision reply was not valid JSON"
    for _attempt in range(2):
        try:
            data = goal_decision(
                # Judge against the refined spec when the refinement
                # step has landed one; the raw one-liner otherwise.
                goal=goal.get("spec") or goal.get("text") or "",
                session_id=session_id,
                attended=is_attended(session_id),
                checklist=[str(it.get("text") or "") for it in items] or None,
                spawn_caller=spawn_caller,
                agent_id=agent_id,
            )
        except Exception as e:  # noqa: BLE001 — turn failure / bad JSON = one attempt
            last_error = f"goal decision failed: {type(e).__name__}: {e}"
            continue
        # Per-item verdicts overwrite "done" in order — true→false too,
        # evidence wins over any earlier tick. None = this evaluation
        # carried no per-item information, the stored ticks stand.
        flags = data.get("checklist")
        if flags is not None:
            for item, flag in zip(items, flags):
                item["done"] = bool(flag)
        if data["met"]:
            undone = [(i, it) for i, it in enumerate(items, 1)
                      if not it.get("done")]
            if undone:
                # Code-level enforcement: met requires every checklist
                # item ticked — the judge cannot talk past the list.
                reason = "清单未全部完成：" + "；".join(
                    f"{i}) {it.get('text')}" for i, it in undone)
                return "unmet", reason, "", []
            return "met", data["reason"], "", []
        question = data["question"].strip()
        if data["need_user"] and question:
            return "needs_user", data["reason"], question, data.get("options") or []
        return "unmet", data["reason"], "", []
    return "judge_failure", last_error, "", []
