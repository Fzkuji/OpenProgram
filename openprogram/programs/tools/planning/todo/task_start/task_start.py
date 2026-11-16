"""task_start — open a task: a named stretch of work with its steps."""
from __future__ import annotations

import time

from openprogram.models.providers.types import AgentToolResult, TextContent
from openprogram.programs._host.runtime import function
from .. import shared


@function(
    name="task_start",
    description=(
        "Open a task: a named stretch of work in this reply, shown to the "
        "user as one block that folds to a line when it is done. Use a task "
        "only for work of three or more steps or about a dozen tool calls; "
        "small work needs none. A reply usually holds none, one or two.\n"
        "Do not call this tool to open a task you are about to work on. "
        "Every other tool takes an optional task_progress argument, applied "
        "before that call runs: on the first real call of the work (a read, "
        "a search, a command) put {\"task\": title, \"steps\": [...], "
        "\"step\": 1}, and on each later call of the task {\"step\": N}, the "
        "number of the step that call works on. When N changes, the step "
        "before is done; add \"result\", half a sentence on what it found. "
        "This costs no extra model round trip; a response that holds only "
        "task calls costs a whole one. Calls that do not depend on each "
        "other still go out together, as parallel tool calls in one "
        "response, each with its task_progress. Call task_start only to give a "
        "purpose. The end of your reply closes the task: send no last call "
        "for it, and do not repeat the plan or the progress in your text, "
        "the block shows them.\n"
        "Starting a task closes the one that is open: its unfinished steps "
        "are dropped. When the user redirects you, drop steps with task_step "
        "or start a new task.\n"
        "\n"
        "Args:\n"
        "  title: a noun phrase of at most 40 characters naming the work.\n"
        "  purpose: one sentence saying what the work is for (optional).\n"
        "  steps: one to seven short phrases, every step you can foresee, "
        "in the order they will be done (optional: a title alone opens a "
        "task with the work listed flat under it)."
    ),
    toolset=["core"],
    unsafe_in=["wechat", "telegram"],
)
def task_start(title: str, purpose: str = "", steps: list[str] | None = None) -> str | AgentToolResult:
    """Open a task on the session's board."""
    return _task_start_impl(title, purpose, steps)


def _close_open(session_id, todos, tasks, plans, task, anchor, scope):
    """Close the task a new one takes the place of. Returns
    ``(plans, changed segment or None, a line for the tool result)``.

    As at the end of a reply, the step in progress is finished when that
    closes the task: the task was advanced, no step waits after it, and it
    belongs to no Goal (``shared.close_at_turn_end``)."""
    task_id = str(task["id"])
    steps = shared.steps_of(todos, task_id)
    running = shared.current_step(steps)
    if (running is not None and task.get("advanced") and not task.get("goal_id")
            and not any(s.get("status") == "pending" for s in steps)):
        shared.set_step(running, "completed", anchor=anchor)
    kept = shared.counted(steps)
    left = shared.unfinished(steps)
    done = sum(1 for s in kept if s.get("status") == "completed")
    here = any(p.get("anchor_id") == anchor and p.get("plan_id") == task_id for p in plans)
    finished = not left and (bool(task.get("advanced")) or not steps)
    now = time.time()
    segment = None
    if finished:
        task.update(status="done", ended_at=now, updated_at=now)
        if here:
            plans, segment = shared.place_segment(
                session_id, todos, tasks, task_id, state="done", anchor=anchor, actor=scope, plans=plans)
        note = f"Closed '{task.get('title')}'" + (f" ({done}/{len(kept)} done)" if steps else "")
        return plans, segment, note
    task.update(status="displaced", ended_at=now, updated_at=now)
    if here:
        # The segment keeps the task as it stood when it was displaced; a
        # segment of an earlier reply is left as that reply left it.
        plans, segment = shared.place_segment(
            session_id, todos, tasks, task_id, state="displaced", anchor=anchor, actor=scope, plans=plans)
    for step in left:
        shared.set_step(step, "dropped", anchor=anchor)
    note = (f"Closed '{task.get('title')}' at {done}/{len(kept)}; "
            f"{len(left)} open step{'s' if len(left) != 1 else ''} dropped")
    return plans, segment, note


def _task_start_impl(title: str, purpose: str = "", steps: list[str] | str | None = None,
                     call_id: str = "") -> str | AgentToolResult:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable).
    ``call_id`` names the tool call whose ``task_progress`` started the
    task (``progress.py``); it is kept on the task so the call is applied
    once."""
    sid = shared.current_session_id()
    if not sid:
        return "[task_start error] no active session context"
    title = " ".join(str(title or "").split())[:shared.MAX_TITLE].strip()
    if not title:
        return "[task_start error] title required"
    if isinstance(steps, str):
        steps = steps.splitlines()
    subjects = [s for s in (" ".join(str(item or "").split()) for item in (steps or [])) if s]
    if len(subjects) > shared.MAX_STEPS:
        return (f"Nothing was changed: a task starts with at most {shared.MAX_STEPS} steps and this one has "
                f"{len(subjects)}. Merge steps into {shared.MAX_STEPS} or fewer, or start with the first ones "
                "and add the rest with task_step(add=[...]).")
    with shared.lock():
        todos = shared.load(sid)
        tasks = shared.load_tasks(sid)
        plans = shared.load_plans(sid)
        from openprogram.programs.tools.planning.goal import chat
        goal = chat.current_identity() or {}
        scope = shared.current_scope()
        anchor = shared.current_anchor()
        changed: list = []
        notes: list[str] = []
        previous = shared.open_task(tasks, scope)
        if previous is not None and " ".join(str(previous.get("title") or "").split()).casefold() == title.casefold():
            # The open task again: nothing to start.
            return f"Task {previous['id']} '{previous.get('title')}' is already open.\n" + shared.progress(todos, previous)
        if previous is not None:
            plans, segment, note = _close_open(sid, todos, tasks, plans, previous, anchor, scope)
            changed.append(segment)
            notes.append(note)
        now = time.time()
        task_id = shared.next_task_id(tasks)
        owner = {
            **({"turn": anchor} if anchor else {}),
            **({"scope": scope} if scope else {}),
            **({"goal_id": goal["goal_id"], "goal_revision": goal["revision"]} if goal.get("goal_id") else {}),
        }
        task = {
            "id": task_id, "title": title, "purpose": " ".join(str(purpose or "").split()),
            "status": "open", "outcome": "", "advanced": False,
            "created_at": now, "updated_at": now, "ended_at": None, **owner,
            **({"calls": [call_id]} if call_id else {}),
        }
        tasks.append(task)
        first = None
        for subject in subjects:
            entry = {
                "id": shared.next_id(todos), "subject": subject, "description": "",
                "status": "pending" if first is not None else "in_progress",
                "blocked_by": [], "plan": task_id, "created_at": now, "updated_at": now, **owner,
            }
            first = first or entry
            todos.append(entry)
        plans, segment = shared.place_segment(sid, todos, tasks, task_id, state="running", anchor=anchor,
                                              actor=scope, earlier=[], plans=plans)
        changed.append(segment)
        shared.save(sid, todos, plans, tasks)
        # Inside the lock, so frames reach the UI in save order.
        shared.publish(sid, changed)
        state = shared.progress(todos, task)
    shared.notify_goal(sid)
    notes.append(f"Task {task_id} started: {title}")
    if subjects:
        notes.append(state)
    effect = {"task": task_id, **({"step": first["id"]} if first is not None else {})}
    return AgentToolResult(content=[TextContent(text="\n".join(notes))], details={"task": effect})
