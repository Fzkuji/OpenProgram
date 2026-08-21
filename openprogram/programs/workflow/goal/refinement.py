"""Spec refinement — runs right after /goal set, in the background:
expand the one-line goal into a full specification plus acceptance
checklist, and let the loop adopt a refinement that lands mid-flight."""
from __future__ import annotations

import inspect
import logging
from typing import Optional

from openprogram.agentic_programming.function import current_session_id
from openprogram.programs.workflow.json_parsing import parse_json

# Cross-function calls go through the package object so monkeypatches on
# ``openprogram.programs.workflow.goal`` (the tests' seam) hit every internal call
# site — ``from .state import save_goal`` would freeze the original
# binding and bypass patches.
import openprogram.programs.workflow.goal as _goal

_log = logging.getLogger(__name__)

# Inspection plus search. Refining a goal must not change files.
REFINE_TOOLS = ("read", "glob", "grep", "list", "bash", "web_search")


def _run_refine_turn(session_id: str, prompt: str, *, agent_id: str,
                     spawn_caller: Optional[str]) -> str:
    """One spawned inspection-only agent turn. Module-level so tests stub it."""
    from openprogram.agent.sub_agent_run import run_agent_turn
    res = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label="goal 完善",
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(REFINE_TOOLS),
    )
    if res.failed:
        raise RuntimeError(res.error or "goal spec refinement turn failed")
    return res.final_text or ""


def _parse_refinement(raw: str) -> tuple[str, list[str]]:
    """``(spec, checklist)`` from a refinement reply."""
    try:
        data = parse_json(raw or "")
        if isinstance(data, dict) and isinstance(data.get("spec"), str) \
                and data["spec"].strip():
            items = [it.strip() for it in (data.get("checklist") or [])
                     if isinstance(it, str) and it.strip()]
            return data["spec"].strip(), items[:20]
    except ValueError:
        pass
    text = (raw or "").strip()
    if len(text) >= 200:
        return text, []
    raise ValueError("goal refinement reply had no valid spec")


def refine_goal_spec_candidate(goal_text: str, session_id: str = "", *,
                               agent_id: str = "main",
                               spawn_caller: Optional[str] = None) -> tuple[str, list[str]]:
    """You expand a user's one-line session goal into a complete goal
    SPECIFICATION. The user typed a single sentence; it cannot cover
    everything, so you fill in what a completion judge will need. You
    have inspection tools (read, glob, grep, list, bash) and may look
    at the working directory to understand the task context.

    Write the specification with:

    * Completion criteria — a checklist of verifiable items: formal,
      checkable outcomes (files that must exist, tests that must pass,
      outputs that must appear) AND process requirements (e.g. "read
      sources X and Y before writing section Z", "verify every
      citation individually").
    * Reference anchor — when the goal names or implies a reference
      (an example paper, an existing implementation, a competing
      product, a prior version), or an established work of the same
      kind is findable (use web_search), READ the reference and
      translate it into countable criteria: structure and length,
      coverage, feature list, depth of treatment — whatever the kind
      of deliverable makes measurable. Record the reference's path or
      source in the specification. The bar is MEET OR EXCEED the
      reference on every extracted criterion — a reference is a floor,
      not a style suggestion. No reference given or findable: skip
      this part, do not invent one.
    * Form anchor — countable structure is not enough: also extract
      HOW the reference presents its content, and turn that into
      checkable items. For prose deliverables that means e.g. "body
      sections argue in connected paragraphs; list lines are under
      10% of body lines", "every major section carries at least N
      words of connected prose", "figure count meets the reference's".
      A deliverable that matches the reference's chapter and citation
      counts but reads as bullet-point notes has NOT met the
      reference.
    * Verification depth — for any "sources are real / verified"
      criterion, spell out that acceptance requires SAMPLED
      re-checking (open or search a random handful of the claimed
      sources), not trusting the writer's own "verified" notes.
    * Boundaries — what is explicitly OUT of scope, so the run does
      not wander.
    * Judge checklist — the items the completion judge checks one by
      one before declaring the goal met. Emit them as the "checklist"
      JSON field: 3 to 12 short sentences, each independently
      verifiable on its own, written in the SAME LANGUAGE as the goal
      text.

    Stay faithful to the user's intent: refine and sharpen it, never
    replace it. Keep the specification concise enough to be checked
    item by item.

    End your reply with STRICT JSON only, no markdown fence, no prose
    after it:
    {"spec": "<the full specification as one string>",
     "checklist": ["<verifiable item>", "<verifiable item>", …]}
    """
    sid = session_id or current_session_id()
    prompt = (
        f"{inspect.getdoc(refine_goal_spec_candidate)}\n\n"
        f"<goal>\n{goal_text}\n</goal>"
    )
    raw = _run_refine_turn(sid, prompt, agent_id=agent_id,
                           spawn_caller=spawn_caller)
    return _parse_refinement(raw)


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
        spec, checklist = _goal.refine_goal_spec_candidate(
            text, session_id=session_id
        )
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
