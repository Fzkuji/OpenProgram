"""task_step — move the open task on: next step, changes to the steps, the end."""
from __future__ import annotations

import time

from openprogram.models.providers.types import AgentToolResult, TextContent
from openprogram.programs._host.runtime import function
from .. import shared

_NO_TASK = ("No task is open, so nothing was changed. Carry on with the work; open a task with "
            "task_start only when the work ahead is three steps or more.")
_NOTHING = "Nothing was changed: pass next, step, add, drop, result or outcome."


@function(
    name="task_step",
    description=(
        "Change the open task in ways task_progress cannot: add or drop "
        "steps, or end the task early with an outcome. To finish the "
        "current step and start the next, do not call this: put "
        "task_progress {\"step\": N, \"result\": ...} on the next real "
        "tool call. A response that holds only task calls costs a whole "
        "model round trip, so send this call beside a real one when you "
        "can.\n"
        "At a normal end of your reply the step in progress is finished for "
        "you when it is the last one, and a task with nothing left is "
        "closed: send no last call to close it. Under "
        "a Goal no step is finished for you: finish or drop the steps "
        "yourself. With no task open this returns a hint and changes "
        "nothing. A sub-agent without a task of its own ticks a step of the "
        "task it was sent from with step=N.\n"
        "\n"
        "Args:\n"
        "  next: finish the current step and start the next waiting one.\n"
        "  step: finish the current step and start step number N instead "
        "(1 is the first step), to work out of order.\n"
        "  add: steps to append, short phrases.\n"
        "  drop: numbers of steps to give up; they are struck through and "
        "no longer counted.\n"
        "  result: half a sentence on what the step just finished found or "
        "changed.\n"
        "  outcome: ends the task now with this result, a phrase of at most "
        "ten words, when you stop it before the end of your reply; steps "
        "still open are dropped."
    ),
    toolset=["core"],
    unsafe_in=["wechat", "telegram"],
)
def task_step(next: bool = False, step: int = 0, add: list[str] | None = None,  # noqa: A002 - the model's word
              drop: list[int] | None = None, result: str = "", outcome: str = "") -> str | AgentToolResult:
    """Advance or change the open task."""
    return _task_step_impl(next, step, add, drop, result, outcome)


def _numbers(value) -> list[int] | None:
    """The step numbers in ``value``; None when one of them is not a number."""
    if value is None or value == "":
        return []
    if isinstance(value, (int, str)) and not isinstance(value, bool):
        value = str(value).replace(",", " ").split()
    try:
        return [int(item) for item in value]
    except (TypeError, ValueError):
        return None


def _tick_parent(sid, todos, tasks, plans, number, result, anchor, scope) -> str:
    """A sub-agent finishing a step of the task it was sent from."""
    parent = shared.open_task(tasks, "")
    if parent is None:
        return _NO_TASK
    steps = shared.steps_of(todos, str(parent["id"]))
    if not 1 <= number <= len(steps):
        return f"Nothing was changed: '{parent.get('title')}' has steps 1 to {len(steps)}, not {number}."
    shared.set_step(steps[number - 1], "completed", anchor=anchor, result=result)
    parent.update(advanced=True, updated_at=time.time())
    plans, segment = shared.place_segment(sid, todos, tasks, str(parent["id"]), anchor=anchor, actor=scope, plans=plans)
    shared.save(sid, todos, plans, tasks)
    shared.publish(sid, [segment])
    return (f"Step {number} of '{parent.get('title')}' marked done: {steps[number - 1].get('subject')}\n"
            + shared.progress(todos, parent))


def _task_step_impl(next: bool = False, step: int = 0, add: list[str] | str | None = None,  # noqa: A002
                    drop: list[int] | str | int | None = None, result: str = "",
                    outcome: str = "", call_id: str = "") -> str | AgentToolResult:
    """Implementation body — kept apart from the @function binding so
    tests can call it directly (the binding object is not callable).
    ``call_id`` names the tool call whose ``task_progress`` moved the task
    (``progress.py``); it is kept on the task so the call is applied once."""
    sid = shared.current_session_id()
    if not sid:
        return "[task_step error] no active session context"
    try:
        number = int(step or 0)
    except (TypeError, ValueError):
        return "Nothing was changed: step is the number of a step, 1 for the first."
    if isinstance(add, str):
        add = add.splitlines()
    added = [s for s in (" ".join(str(item or "").split()) for item in (add or [])) if s]
    dropped = _numbers(drop)
    if dropped is None:
        return "Nothing was changed: drop is a list of step numbers, 1 for the first."
    result = " ".join(str(result or "").split())
    outcome = " ".join(str(outcome or "").split())
    advance = bool(next) and str(next).lower() not in ("false", "0", "no")
    if not (advance or number or added or dropped or result or outcome):
        return _NOTHING
    ticked = False
    with shared.lock():
        todos = shared.load(sid)
        tasks = shared.load_tasks(sid)
        plans = shared.load_plans(sid)
        scope = shared.current_scope()
        anchor = shared.current_anchor()
        task = shared.open_task(tasks, scope)
        if task is None:
            if not scope:
                return _NO_TASK
            if not number or advance or added or dropped or outcome:
                return ("No task of your own is open, so nothing was changed. To tick a step of the task "
                        "you were sent from, call task_step(step=N, result=...).")
            text = _tick_parent(sid, todos, tasks, plans, number, result, anchor, scope)
            ticked = True
        else:
            task_id = str(task["id"])
            steps = shared.steps_of(todos, task_id)
            if number and not 1 <= number <= len(steps):
                return f"Nothing was changed: this task has steps 1 to {len(steps)}, not {number}."
            bad = [n for n in dropped if not 1 <= n <= len(steps)]
            if bad:
                return (f"Nothing was changed: this task has steps 1 to {len(steps)}, "
                        f"not {', '.join(str(n) for n in bad)}.")
            done_before = [str(s["id"]) for s in steps if s.get("status") == "completed"]
            before = shared.current_step(steps)
            first_touch = not any(p.get("anchor_id") == anchor and p.get("plan_id") == task_id for p in plans)
            notes: list[str] = []
            result_left = result

            def finish(entry) -> None:
                nonlocal result_left
                shared.set_step(entry, "completed", anchor=anchor, result=result_left)
                result_left = ""
                notes.append(f"Step {steps.index(entry) + 1} done: {entry.get('subject')}")

            target = steps[number - 1] if number else None
            if advance or number:
                if before is not None and before is not target:
                    finish(before)
                if target is None:
                    target = next_pending(steps)
                if target is not None and target.get("status") != "in_progress":
                    shared.set_step(target, "in_progress", anchor=anchor)
                task["advanced"] = True
            if result_left and not outcome:
                # A result alone belongs to the step that finished last.
                finished = [s for s in steps if s.get("status") == "completed"]
                holder = (max(finished, key=lambda s: float(s.get("updated_at") or 0)) if finished
                          else shared.current_step(steps))
                if holder is not None:
                    holder["result"] = result_left
                    holder["updated_at"] = time.time()
                    result_left = ""
            goal = {key: task[key] for key in ("goal_id", "goal_revision") if key in task}
            now = time.time()
            for subject in added:
                entry = {
                    "id": shared.next_id(todos), "subject": subject, "description": "", "status": "pending",
                    "blocked_by": [], "plan": task_id, "created_at": now, "updated_at": now,
                    **({"turn": anchor} if anchor else {}), **({"scope": scope} if scope else {}), **goal,
                }
                todos.append(entry)
            for n in dropped:
                shared.set_step(steps[n - 1], "dropped", anchor=anchor)
            steps = shared.steps_of(todos, task_id)
            if outcome:
                running = shared.current_step(steps)
                if running is not None:
                    finish(running)
                for entry in shared.unfinished(steps):
                    shared.set_step(entry, "dropped", anchor=anchor)
                task.update(status="done", outcome=outcome, ended_at=now)
            elif (advance or number or added or dropped) and shared.current_step(steps) is None:
                # An open task with steps waiting always has one in progress.
                waiting = next_pending(steps)
                if waiting is not None:
                    shared.set_step(waiting, "in_progress", anchor=anchor)
            task["updated_at"] = now
            if anchor:
                task["turn"] = anchor
            if call_id:
                task["calls"] = [*(task.get("calls") or ()), call_id][-shared.MAX_CALLS:]
            plans, segment = shared.place_segment(
                sid, todos, tasks, task_id, state="done" if outcome else "running", anchor=anchor,
                actor=scope, earlier=done_before, plans=plans)
            shared.save(sid, todos, plans, tasks)
            # Inside the lock, so frames reach the UI in save order.
            shared.publish(sid, [segment])
            after = shared.current_step(steps)
            if outcome:
                notes.append(f"Task '{task.get('title')}' finished: {outcome}")
                effect = {"task": None}
            else:
                notes.append(shared.progress(todos, task))
                moved = (after or {}).get("id") != (before or {}).get("id")
                effect = ({"task": task_id, **({"step": str(after["id"])} if after is not None else {})}
                          if moved or first_touch else None)
            text = "\n".join(notes)
    shared.notify_goal(sid)
    if ticked or effect is None:
        return text
    return AgentToolResult(content=[TextContent(text=text)], details={"task": effect})


def next_pending(steps):
    return next((s for s in steps if s.get("status") == "pending"), None)
