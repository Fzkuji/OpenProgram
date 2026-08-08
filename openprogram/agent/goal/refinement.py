"""Spec refinement — runs right after /goal set, in the background:
expand the one-line goal into a full specification plus acceptance
checklist, and let the loop adopt a refinement that lands mid-flight."""
from __future__ import annotations

import logging
from typing import Optional

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.agent.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
from openprogram.agent import goal as _goal

_log = logging.getLogger(__name__)


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
    goal = _goal.load_goal(session_id)
    if not goal or goal.get("status") != "active" or goal.get("spec"):
        return
    text = goal.get("text") or ""
    try:
        from openprogram.functions.agentics.goal import refine as _refine
        spec, checklist = _refine(text, session_id=session_id)
    except Exception:
        _log.warning("goal spec refinement failed (fail-open) for session %s",
                     session_id, exc_info=True)
        _goal._emit_goal_notice(session_id, (
            "[goal] 目标完善失败——判定将只按你的原始一句话核对，"
            "没有展开的验收清单。想要更严的判定可 /goal clear 后重设。"))
        return
    goal = _goal.load_goal(session_id)
    if (not goal or goal.get("status") not in ("active", "waiting_user")
            or (goal.get("text") or "") != text):
        return
    goal["spec"] = spec
    if checklist:
        goal["checklist"] = [{"text": t, "done": False} for t in checklist]
    _goal.save_goal(session_id, goal)
    _goal._emit_goal_update(None, session_id, goal)
    _goal._emit_goal_spec_notice(session_id, spec, checklist)


def _start_spec_refinement(session_id: str) -> None:
    """Kick off :func:`refine_goal_spec` on a daemon thread. Tests stub
    this to keep the set flow synchronous."""
    import threading
    threading.Thread(
        target=_goal.refine_goal_spec, args=(session_id,),
        name=f"goal-refine-{session_id[:8]}", daemon=True,
    ).start()


def _adopt_refinement(session_id: str, goal: dict) -> None:
    """Copy spec/checklist that the background refinement stored while
    this loop iteration held a pre-refinement snapshot. Only fills the
    fields when the snapshot lacks them — a checklist the judge just
    ticked is never overwritten by the stored (unticked) one."""
    if goal.get("spec") and goal.get("checklist"):
        return
    fresh = _goal.load_goal(session_id) or {}
    if not goal.get("spec") and fresh.get("spec"):
        goal["spec"] = fresh["spec"]
    if not goal.get("checklist") and fresh.get("checklist"):
        goal["checklist"] = fresh["checklist"]


def _emit_goal_spec_notice(session_id: str, spec: str,
                           checklist: Optional[list[str]] = None) -> None:
    """Show the refined spec as a system row in the transcript (same
    ``local_command`` surface the /goal command replies use), so the
    user sees what the system understood the goal to be — ``/goal
    clear`` and a fresh ``/goal`` re-set if it misread the intent.
    The acceptance checklist renders after the spec as a numbered
    list."""
    body = spec
    if checklist:
        numbered = "\n".join(f"{i}. {t}" for i, t in enumerate(checklist, 1))
        body = f"{spec}\n\n验收清单：\n{numbered}"
    _goal._emit_goal_notice(session_id, (
        f"[goal] 目标已完善为规格（判定按此逐条核对；"
        f"不满意可 /goal clear 重设）：\n{body}"))
