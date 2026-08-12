"""task_list — the todo planning board run as an executable workflow.

The board is the control flow; each entry is a bounded sub-task carrying
only the context it needs. Two things this buys over one long-running
agent: stability on long tasks (scope pinned per item, per-item retry,
resume after a crash) and token economy (history does not grow with the
step count).

Nothing here is a parallel mechanism. The list IS the session's todo
board (``openprogram/functions/tools/todo/``, stored at
``<session-repo>/todos.json``), so a workflow's progress shows up in the
TUI and web todo views with no extra surface, and the completion
judgment IS the goal judge (``openprogram.agent.goal.evaluate_goal``),
so an item is decided the same way a session goal is. This module adds
three workflow fields to a board entry, all optional so entries written
by the plain ``todo_create`` tool keep working:

    done_criteria   what makes this item complete (the goal payload)
    context_spec    {"upstream": N} — how many upstream results it reads
    result_summary  the hand-off text the next item receives

What this module actually owns: the ``run_task_list`` entry point and
its size decision, the serial driver that advances the board, revision
of the unfinished tail, hand-off assembly with its length limit, and the
    context wiring onto ``render_range``.

Every LLM round is a spawned same-session agent turn whose prompt is a
docstring, and each goes through a module-level ``_run_*_turn`` seam, so
tests stub one function instead of the network. Same shape as ``goal``.

Registration: AGENTIC_MODULES.
"""
from __future__ import annotations

import inspect
import logging
import time
from typing import Optional

from openprogram.agentic_programming.function import (
    agentic_function,
    current_session_id,
)
from openprogram.functions.agentics.json_parsing import parse_json
from openprogram.functions.tools.todo import shared

_log = logging.getLogger(__name__)

# A hand-off summary is a conclusion plus where the artifacts are, never
# a transcript. Past this many characters it has stopped being a summary,
# so it is cut and the executor is told to point at paths instead.
HANDOFF_SUMMARY_MAX_CHARS = 1200

# One retry inside the item; a second failure goes back to the planner.
# Retrying the same failure a third time does not fix it — splitting the
# item or correcting its criterion does.
ITEM_ATTEMPT_LIMIT = 2

# Items an executor may work through before the loop stops on its own.
# A list that keeps growing past this is a planning failure, not progress.
MAX_ITEMS_EXECUTED = 40

# The executor gets real work tools. The planner and the size check only
# look — planning must not change the working tree.
EXECUTOR_TOOLS = ("bash", "read", "write", "edit", "grep", "glob", "list")
PLANNER_TOOLS = ("read", "grep", "glob", "list")

# Board statuses this workflow drives. ``shared.STATUSES`` owns the
# vocabulary; a workflow only ever moves an entry between these three.
PENDING = "pending"
IN_PROGRESS = "in_progress"
COMPLETED = "completed"

# Marks the board entries belonging to one workflow run. Entries a user
# or another agent wrote with todo_create carry no owner of this shape,
# so a workflow never adopts todos it did not plan.
WORKFLOW_OWNER = "task_list"


# ---------------------------------------------------------------------------
# Board access — the checkpoint is the todo board itself
# ---------------------------------------------------------------------------

def workflow_items(session_id: str, task: str) -> list[dict]:
    """This task's board entries, in board order.

    Matched on ``owner`` plus the task text so a workflow sees only its
    own items: todos the user wrote by hand, and items from an earlier
    run of a different task, are left alone."""
    return [t for t in shared.load(session_id)
            if t.get("owner") == WORKFLOW_OWNER and t.get("task") == task]


def next_pending(items: list[dict]) -> Optional[dict]:
    """The first entry still to run, or ``None`` when the list is spent.

    ``in_progress`` counts as still-to-run: an entry left that way is a
    run that died mid-item, and resuming picks it up again."""
    for item in items:
        if item.get("status") in (PENDING, IN_PROGRESS):
            return item
    return None


def _write_items(session_id: str, task: str, items: list[dict]) -> None:
    """Replace this task's entries on the board, keeping every other
    entry (and their order) untouched. One locked read-modify-write, the
    same discipline the todo tools use."""
    with shared.lock():
        todos = shared.load(session_id)
        keep = [t for t in todos
                if not (t.get("owner") == WORKFLOW_OWNER
                        and t.get("task") == task)]
        shared.save(session_id, keep + items)


def _update_item(session_id: str, item: dict, **fields) -> None:
    """Patch one entry on the board and checkpoint it.

    Every transition goes through here, so ``todos.json`` on disk is a
    checkpoint rather than a log: a killed process resumes from the last
    item this wrote."""
    item.update(fields)
    item["updated_at"] = time.time()
    with shared.lock():
        todos = shared.load(session_id)
        for i, t in enumerate(todos):
            if t.get("id") == item.get("id"):
                todos[i] = item
                break
        else:
            todos.append(item)
        shared.save(session_id, todos)


def _new_item(session_id: str, task: str, raw: dict, todo_id: str) -> dict:
    """One board entry in workflow shape.

    The base fields are exactly what ``todo_create`` writes, so the
    entry renders in ``todo_list`` like any other; the three workflow
    fields ride alongside. ``done_criteria`` falls back to the subject
    so an item is never unjudgeable."""
    subject = str(raw.get("text") or raw.get("subject") or "").strip()
    spec = raw.get("context_spec")
    upstream = -1
    if isinstance(spec, dict):
        try:
            upstream = int(spec.get("upstream", -1))
        except (TypeError, ValueError):
            upstream = -1
    now = time.time()
    return {
        # —— the todo board's own fields ——
        "id": todo_id,
        "subject": subject,
        "description": str(raw.get("description") or ""),
        "status": PENDING,
        "owner": WORKFLOW_OWNER,
        "blocked_by": [],
        "created_at": now,
        "updated_at": now,
        # —— workflow fields (optional everywhere else) ——
        "task": task,
        "done_criteria": str(raw.get("done_when")
                             or raw.get("done_criteria") or "").strip() or subject,
        "context_spec": {"upstream": upstream},
        "result_summary": "",
        "attempts": 0,
    }


def clip_handoff(text: str) -> str:
    """Cut a hand-off summary down to the limit.

    A summary that grew into a transcript loses the whole point of the
    split, so it is truncated with the rule spelled out — the next
    executor reads the note and knows to look for artifact paths rather
    than assume it got the full story."""
    text = (text or "").strip()
    if len(text) <= HANDOFF_SUMMARY_MAX_CHARS:
        return text
    return (text[:HANDOFF_SUMMARY_MAX_CHARS].rstrip()
            + "\n[truncated — hand-off summaries carry conclusions and "
              "artifact paths, not content; read the named paths for detail]")


def _upstream_summaries(items: list[dict], item: dict) -> str:
    """The hand-off block an item's executor reads.

    ``context_spec.upstream`` is what the planner marked when it wrote
    the list: how many completed items back this one needs. ``0`` means
    the item stands alone, ``-1`` (or a missing spec) means every
    completed item so far. Small items look at little, big items look at
    more, decided at planning time rather than guessed at execution
    time."""
    done = [it for it in items
            if it.get("status") == COMPLETED and it.get("result_summary")]
    spec = item.get("context_spec") or {}
    try:
        want = int(spec.get("upstream", -1))
    except (TypeError, ValueError):
        want = -1
    if want == 0 or not done:
        return ""
    if want > 0:
        done = done[-want:]
    return "\n\n".join(
        f"[#{it['id']}] {it.get('subject', '')}\n{it.get('result_summary', '')}"
        for it in done
    )


def render_range_for(item: dict) -> dict:
    """The item's context spec as a DAG ``render_range``.

    ``context_spec.upstream`` is how many upstream hand-offs the planner
    wants visible; that same number bounds the DAG slice a nested call
    brings in, so the two stay one decision instead of two. ``callers:
    0`` walls the item off from the conversation that preceded it —
    an item's brief is its own text plus its hand-offs, nothing else."""
    spec = item.get("context_spec") or {}
    try:
        upstream = int(spec.get("upstream", -1))
    except (TypeError, ValueError):
        upstream = -1
    return {"callers": 0, "subcalls": upstream}


# ---------------------------------------------------------------------------
# Spawned turns — the seams tests stub
# ---------------------------------------------------------------------------

def _run_planner_turn(session_id: str, prompt: str, *, agent_id: str,
                      spawn_caller: Optional[str], label: str) -> str:
    """One inspection-only planning turn. Module-level so tests stub it."""
    from openprogram.agent.sub_agent_run import run_agent_turn
    res = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label=label,
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(PLANNER_TOOLS),
    )
    if res.failed:
        raise RuntimeError(res.error or "task list planning turn failed")
    return res.final_text or ""


def _run_executor_turn(session_id: str, prompt: str, *, agent_id: str,
                       spawn_caller: Optional[str], label: str,
                       render_range: dict[str, int]) -> str:
    """One item-execution turn with work tools. Module-level so tests
    stub it. The prompt is the whole context this executor gets — item
    text plus upstream hand-offs, not the session's history."""
    from openprogram.agent.sub_agent_run import run_agent_turn
    res = run_agent_turn(
        session_id=session_id,
        prompt=prompt,
        agent_id=agent_id,
        branch_from=None,
        label=label,
        spawn_caller=spawn_caller,
        advance_head=False,
        tools_override=list(EXECUTOR_TOOLS),
        render_range=render_range,
    )
    if res.failed:
        raise RuntimeError(res.error or "task list item turn failed")
    return res.final_text or ""


def judge_item(session_id: str, item: dict, *, agent_id: str,
               spawn_caller: Optional[str] = None) -> tuple[bool, str]:
    """``(passed, reason)`` for one item, from the session goal judge.

    The item's ``done_criteria`` is handed to ``evaluate_goal`` as the
    goal payload — the same single judgment that decides session goals,
    not a second one invented here. Item-level retry accounting stays in
    this module's driver; the turn-level continuation loop in
    ``agent/goal/loop.py`` is a different layer and is not involved.

    Anything other than ``met`` is "not passed": ``needs_user`` and
    ``judge_failure`` both mean this item cannot be called done, and the
    driver's retry treats them like any other miss.

    The call goes through the package object because that is the goal
    package's monkeypatch seam (see the note at the top of
    ``agent/goal/loop.py``)."""
    from openprogram.agent import goal as _goal
    verdict, reason, _question, _options = _goal.evaluate_goal(
        session_id,
        {"text": item.get("done_criteria") or item.get("subject") or ""},
        agent_id=agent_id,
        spawn_caller=spawn_caller,
    )
    return verdict == "met", reason


# ---------------------------------------------------------------------------
# Planner — sizing, the initial list, revision
# ---------------------------------------------------------------------------

def _parse_plan(raw: str) -> dict:
    """``{"split": bool, "reason": str, "items": [...]}`` from a planner
    reply. Raises ``ValueError`` when the reply carries no JSON object or
    ``split`` is not a bool. A ``split`` that is true with no usable item
    also raises — an empty list is not a plan."""
    data = parse_json(raw or "")
    if not isinstance(data, dict) or not isinstance(data.get("split"), bool):
        raise ValueError("task list plan reply was not valid JSON")
    items = [it for it in (data.get("items") or [])
             if isinstance(it, dict) and str(it.get("text") or "").strip()]
    if data["split"] and not items:
        raise ValueError("task list plan said split but listed no items")
    return {
        "split": bool(data["split"]),
        "reason": str(data.get("reason") or ""),
        "items": items,
    }


def _plan_prompt(task: str) -> str:
    return f"{inspect.getdoc(plan_task_list)}\n\n<task>\n{task}\n</task>"


def plan_task_list(task: str, session_id: str = "", *,
                   agent_id: str = "main",
                   spawn_caller: Optional[str] = None) -> dict:
    """You are the planner for a task about to be executed by agents.
    Your FIRST decision is whether the task should be split at all.

    Do NOT split when one agent can finish the task in a single pass —
    a small edit, one question answered, a single file written. Planning
    and handing work between agents costs more than just doing such a
    task once. Answer split=false and let one agent run the whole task.

    Split only when the task genuinely exceeds one pass: several
    distinct deliverables, work across many files or stages, or steps
    whose output the next step depends on. Then write the list.

    Each item is a bounded sub-task:

    * text — what to do, concretely enough that an agent that sees ONLY
      this line and the upstream summaries can act on it. Name the
      files, commands, or outputs involved.
    * done_when — the completion criterion, verifiable by inspection:
      a file that must exist, a command that must pass, an output that
      must appear. This is judged literally, so write what can be
      checked, not a feeling.
    * context_spec.upstream — how many EARLIER completed items this one
      needs the results of. Use 0 for an item that stands alone, a small
      number when it only follows on from the immediately previous work,
      and -1 when it genuinely needs everything before it. This is the
      item's context budget: smaller means a cheaper, more focused run,
      so do not ask for more than the item needs.

    Order the items so that each one's dependencies come before it —
    execution is strictly serial, top to bottom. Keep the list as short
    as the task allows; every item costs a full agent run. The
    repository itself is shared state an executor can read, so never
    plan an item whose only job is to carry file contents forward.

    You have inspection tools (read, glob, grep, list) and may
    look at the working directory to size the task honestly.

    End your reply with STRICT JSON only, no markdown fence, no prose
    after it:
    {"split": true|false,
     "reason": "<why this is or is not worth splitting>",
     "items": [{"text": "<what to do>",
                "done_when": "<verifiable criterion>",
                "context_spec": {"upstream": -1}}, …]}
    When split is false, "items" may be omitted.
    """
    sid = session_id or current_session_id()
    raw = _run_planner_turn(sid, _plan_prompt(task), agent_id=agent_id,
                            spawn_caller=spawn_caller, label="task list 规划")
    return _parse_plan(raw)


def _revise_prompt(task: str, remaining: list[dict], item: dict,
                   reason: str) -> str:
    listing = "\n".join(
        f"- [#{it['id']}] {it.get('subject', '')} "
        f"(done_when: {it.get('done_criteria', '')})"
        for it in remaining
    ) or "(none)"
    return (
        f"{inspect.getdoc(revise_task_list)}\n\n"
        f"<task>\n{task}\n</task>\n\n"
        f"<failed_item>\n[#{item['id']}] {item.get('subject', '')}\n"
        f"done_when: {item.get('done_criteria', '')}\n</failed_item>\n\n"
        f"<why_it_failed>\n{reason}\n</why_it_failed>\n\n"
        f"<remaining_items>\n{listing}\n</remaining_items>"
    )


def revise_task_list(task: str, remaining: list[dict], item: dict,
                     reason: str, session_id: str = "", *,
                     agent_id: str = "main",
                     spawn_caller: Optional[str] = None) -> dict:
    """An item failed its completion judgment twice. You decide what the
    remaining list should be.

    Read why it failed. Usually the item was too big to finish in one
    run, or its done_when criterion did not describe what actually needs
    to be true. Replace the failed item with whatever now makes sense:

    * split it into smaller items that each finish in one pass,
    * restate it with a criterion that can actually be checked,
    * insert work that turned out to be a prerequisite,
    * or drop it, when the failure shows the item was unnecessary or
      impossible — answer with the remaining items minus this one.

    Return the FULL replacement for the items that have not run yet, in
    execution order, including any of the listed remaining items you
    want kept unchanged. Items already completed are not yours to
    change — their results stand and they are not in the list below.

    Item fields are the same as when planning: text, done_when (a
    verifiable criterion), and context_spec.upstream (how many earlier
    completed items' results this one needs; 0 stands alone, -1 needs
    all of them).

    End your reply with STRICT JSON only, no markdown fence, no prose
    after it:
    {"reason": "<what you changed and why>",
     "items": [{"text": "…", "done_when": "…",
                "context_spec": {"upstream": 1}}, …]}
    An empty "items" list means the rest of the work is abandoned.
    """
    sid = session_id or current_session_id()
    raw = _run_planner_turn(
        sid, _revise_prompt(task, remaining, item, reason),
        agent_id=agent_id, spawn_caller=spawn_caller, label="task list 修订")
    data = parse_json(raw or "")
    if not isinstance(data, dict):
        raise ValueError("task list revision reply was not valid JSON")
    if not isinstance(data.get("items"), list):
        raise ValueError("task list revision items must be an array")
    return {
        "reason": str(data.get("reason") or ""),
        "items": [it for it in (data.get("items") or [])
                  if isinstance(it, dict) and str(it.get("text") or "").strip()],
    }


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def _execute_prompt(task: str, item: dict, upstream: str,
                    retry_note: str) -> str:
    upstream_block = (
        f"<upstream_results>\n{upstream}\n</upstream_results>\n\n"
        if upstream else ""
    )
    retry_block = (
        f"<previous_attempt_failed>\n{retry_note}\n</previous_attempt_failed>\n\n"
        if retry_note else ""
    )
    return (
        f"{inspect.getdoc(execute_item)}\n\n"
        f"<overall_task>\n{task}\n</overall_task>\n\n"
        f"<your_item>\n{item.get('subject', '')}\n</your_item>\n\n"
        f"<done_when>\n{item.get('done_criteria', '')}\n</done_when>\n\n"
        f"{upstream_block}{retry_block}"
    )


def execute_item(task: str, item: dict, upstream: str, retry_note: str = "",
                 session_id: str = "", *, agent_id: str = "main",
                 spawn_caller: Optional[str] = None) -> str:
    """You execute ONE item of a task list, then hand off. The item and
    the upstream results below are your whole brief — there is no
    conversation history behind them, so treat them as complete.

    Do the item's work with your tools. The overall task is given for
    orientation only: do not do the other items' work, and do not stop
    short of this one. You are done when the done_when criterion below
    is literally true — a separate judge checks it against the working
    directory, so finish the work rather than describing it.

    The repository is shared state. Anything you write to a file is
    available to whoever runs the next item, which means your hand-off
    is a POINTER, not a payload.

    End with a short hand-off summary for the next agent:

    * what you concluded or decided,
    * the paths of what you created or changed,
    * anything downstream work needs to know that is not obvious from
      those files.

    Keep it under a few hundred words. Never paste file contents,
    command output, or a narrative of your steps into it — name the path
    and let the next agent read it. A summary that grows into a
    transcript is truncated.
    """
    sid = session_id or current_session_id()
    raw = _run_executor_turn(
        sid, _execute_prompt(task, item, upstream, retry_note),
        agent_id=agent_id, spawn_caller=spawn_caller,
        label=f"task list item #{item.get('id')}",
        render_range=render_range_for(item),
    )
    return clip_handoff(raw)


# ---------------------------------------------------------------------------
# run_task_list — the entry point
# ---------------------------------------------------------------------------

def _plan_onto_board(session_id: str, task: str, agent_id: str,
                     spawn_caller: Optional[str]) -> tuple[list[dict], dict]:
    """Plan the task and write its items onto the board."""
    plan = plan_task_list(task, session_id, agent_id=agent_id,
                          spawn_caller=spawn_caller)
    raws = plan["items"] if plan["split"] else [
        {"text": task, "done_when": task, "context_spec": {"upstream": 0}}]
    with shared.lock():
        todos = shared.load(session_id)
        base = int(shared.next_id(todos))
        items = [_new_item(session_id, task, raw, str(base + i))
                 for i, raw in enumerate(raws)]
        shared.save(session_id, todos + items)
    return items, plan


def _run_one_item(session_id: str, task: str, items: list[dict], item: dict,
                  agent_id: str, spawn_caller: Optional[str]) -> tuple[bool, str]:
    """Execute one item until it passes or runs out of attempts.

    ``(passed, reason)``. Each attempt is checkpointed onto the board
    before it runs, so a crash mid-item resumes at the same item with
    its attempt count intact."""
    retry_note = ""
    reason = ""
    if item.get("status") == IN_PROGRESS:
        _update_item(session_id, item, attempts=0)
    while item.get("attempts", 0) < ITEM_ATTEMPT_LIMIT:
        _update_item(session_id, item, status=IN_PROGRESS,
                     attempts=item.get("attempts", 0) + 1)
        try:
            summary = execute_item(
                task, item, _upstream_summaries(items, item), retry_note,
                session_id, agent_id=agent_id, spawn_caller=spawn_caller,
            )
        except Exception as exc:  # noqa: BLE001 — a failed run is one attempt
            reason = f"item run failed: {type(exc).__name__}: {exc}"
            retry_note = reason
            continue
        passed, reason = judge_item(session_id, item, agent_id=agent_id,
                                    spawn_caller=spawn_caller)
        if passed:
            _update_item(session_id, item, status=COMPLETED,
                         result_summary=summary)
            return True, reason
        retry_note = (f"Your previous attempt did not meet the criterion. "
                      f"The judge said: {reason}")
    return False, reason


@agentic_function(input={
    "task": {"description": "The task to plan and run as a task list",
             "multiline": True},
    "session_id": {"hidden": True},
    "resume": {"description": "Continue this task's unfinished board "
                              "entries instead of planning it again"},
    "spawn_caller": {"hidden": True},
    "agent_id": {"hidden": True},
})
def run_task_list(task: str, session_id: str = "", resume: bool = True,
                  spawn_caller: Optional[str] = None,
                  agent_id: str = "main") -> dict:
    """Run a task as a task list: plan it into bounded items on the
    session's todo board, then execute them one at a time.

    The planner sizes the task first. A task one agent finishes in a
    single pass is not split — it runs as one item, because the cost of
    planning and handing off exceeds the cost of just doing it. A task
    that genuinely exceeds one pass becomes a list, and each item runs
    as its own bounded agent whose entire context is the item text plus
    the upstream hand-off summaries the planner marked it as needing.

    After every item the session goal judge decides whether the item's
    completion criterion is met. A miss is retried once; a second miss
    goes back to the planner, which rewrites the part of the list that
    has not run yet. Completed items keep their results through any
    revision.

    Items live on the ordinary todo board, so progress is visible in
    ``todo_list`` and the TUI/web todo views while the run is going, and
    the board doubles as the checkpoint: an interrupted run resumes from
    the last completed item.

    Returns ``{"status", "task", "items", "revisions", "summary"}``,
    where ``status`` is ``completed`` when every item is settled,
    ``abandoned`` when a revision emptied the list, or ``capped`` when
    the run hit its item ceiling. An invalid revision returns ``error``
    and leaves the current checkpoint resumable.
    """
    sid = session_id or current_session_id()

    items = workflow_items(sid, task) if resume else []
    if next_pending(items) is None:
        items = []
    if not items:
        items, _plan = _plan_onto_board(sid, task, agent_id, spawn_caller)

    revisions: list[dict] = []
    executed = 0
    status = "completed"

    while True:
        item = next_pending(items)
        if item is None:
            break
        if executed >= MAX_ITEMS_EXECUTED:
            status = "capped"
            break
        executed += 1

        passed, reason = _run_one_item(sid, task, items, item, agent_id,
                                       spawn_caller)
        if passed:
            continue

        # Two misses on the same item: the planner rewrites the rest of
        # the list rather than a third identical attempt. ``replaced`` is
        # the whole tail that has not completed — the failed item plus
        # whatever was queued behind it; ``remaining`` is what the
        # planner is shown, which excludes the failed item because it is
        # given separately as <failed_item>.
        replaced = [it for it in items if it.get("status") != COMPLETED]
        remaining = [it for it in replaced if it is not item]
        try:
            revision = revise_task_list(task, remaining, item, reason, sid,
                                        agent_id=agent_id,
                                        spawn_caller=spawn_caller)
        except Exception as exc:  # noqa: BLE001 — no revision means stop here
            revisions.append({
                "at": time.time(), "failed_item": item["id"],
                "reason": f"revision failed: {type(exc).__name__}: {exc}",
                "before": [it["id"] for it in replaced], "after": [],
            })
            status = "error"
            break

        # Completed items are never rewritten — only the tail that has
        # not run is replaced, and the failed item is closed out with
        # why it went away.
        done = [it for it in items if it.get("status") == COMPLETED]
        with shared.lock():
            base = int(shared.next_id(shared.load(sid)))
        replacement = [_new_item(sid, task, raw, str(base + i))
                       for i, raw in enumerate(revision["items"])]
        item["status"] = COMPLETED
        item["result_summary"] = f"(revised away: {revision['reason']})"
        item["updated_at"] = time.time()
        items = done + ([item] if item not in done else []) + replacement
        _write_items(sid, task, items)
        revisions.append({
            "at": time.time(), "failed_item": item["id"],
            "reason": revision["reason"],
            "before": [it["id"] for it in replaced],
            "after": [it["id"] for it in replacement],
        })
        if not replacement and next_pending(items) is None:
            status = "abandoned"
            break

    done = [it for it in items if it.get("status") == COMPLETED
            and it.get("result_summary")
            and not it["result_summary"].startswith("(")]
    return {
        "status": status,
        "task": task,
        "items": items,
        "revisions": revisions,
        "summary": "\n\n".join(
            f"[#{it['id']}] {it.get('subject', '')}\n{it['result_summary']}"
            for it in done
        ),
    }


__all__ = [
    "run_task_list", "plan_task_list", "revise_task_list", "execute_item",
    "judge_item", "workflow_items", "next_pending", "clip_handoff",
    "render_range_for", "WORKFLOW_OWNER",
    "EXECUTOR_TOOLS", "PLANNER_TOOLS", "HANDOFF_SUMMARY_MAX_CHARS",
    "ITEM_ATTEMPT_LIMIT", "MAX_ITEMS_EXECUTED",
]
