"""Storage layer for tasks and their steps.

Session-scoped: the board lives in ``<session-repo>/todos.json``, the same
placement pattern as ``inbox.json`` (agent/inbox.py). A task is one list
and a step is one todo, so Goal verification and a sub-agent ticking its
parent's step read the same entries.

A task (``tasks``):

    id          ``T<n>``, counted per branch board
    title       noun phrase, at most 40 characters
    purpose     one sentence, optional
    status      open | done | displaced
    outcome     what the task ended with, when the model said so
    advanced    whether a ``task_step`` call ever moved it on
    scope       absent for the conversation's own agent,
                ``agent:<id>:<reply>`` for a task a sub-agent started: the
                run that started it, so two runs of one agent never share
                a task
    turn        the reply that last changed it
    calls       ids of the tool calls whose ``task_progress`` changed it
    goal_id / goal_revision  the Goal it was started under, if any
    created_at / updated_at / ended_at  unix timestamps

A step (``todos``):

    id          short incrementing string ("1", "2", ...)
    subject     the step's title
    status      pending | in_progress | completed | dropped
    result      half a sentence left when the step finished
    plan        the id of the task the step belongs to
    turn        the reply of the turn that last changed the step
    scope, goal_id, goal_revision, blocked_by, description, timestamps

A dropped step is not counted in progress and is no Goal requirement.

Beside the board, ``plans`` holds segments: one entry per (reply, task),
the task as that reply last left it. The chat draws each segment as a task
block inside the reply of its ``anchor_id``. An entry without ``title``
was written when the board held plain todo lists; it is read as stored.

Each branch of the conversation has its own board (version 4:
``{"branches": {branch_id: {"tasks", "todos", "plans"}}}``): two branches
running side by side never overwrite each other's task. A branch's board
is the turn's own (``current_branch_id``); a branch made by a retry, an
edit or a fork starts with a copy of the board of the branch it left from,
taken the first time it reads one. An older file's single board belongs to
the branch that was selected when it is first read.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional
import logging

_log = logging.getLogger(__name__)

STATUSES = ("pending", "in_progress", "completed", "dropped")

#: Statuses of a step that is neither done nor given up.
UNFINISHED = ("pending", "in_progress")

#: A task title is cut to this many characters.
MAX_TITLE = 40

#: Steps a task can be started with.
MAX_STEPS = 7

#: Ids of the tool calls whose ``task_progress`` changed a task, kept on
#: the task so a call dispatched again is not applied twice.
MAX_CALLS = 50

# Segments kept per branch board; the oldest drop off past this.
MAX_PLANS = 500

# ponytail: one global lock; per-session locks if todo traffic ever matters
_lock = threading.Lock()


def current_session_id() -> Optional[str]:
    try:
        from openprogram.runtime.execution.run_control.binding import (
            _current_session_id,
        )
        return _current_session_id.get(None)
    except Exception:
        return None


def current_branch_id(session_id: str) -> Optional[str]:
    """The branch a board read or write belongs to: the running turn's own
    branch when the turn is in this conversation, else the selected one."""
    try:
        from openprogram.runtime.agent.turn_request import get_turn_request
        req = get_turn_request()
    except Exception:
        req = None
    if req is not None and getattr(req, "session_id", None) == session_id and getattr(req, "branch_id", None):
        return req.branch_id
    return _selected_branch(session_id)


def _selected_branch(session_id: str) -> Optional[str]:
    try:
        from openprogram.runtime.context.session_db import default_db
        return (default_db().get_session(session_id) or {}).get("active_branch_id")
    except Exception:
        return None


def _parent_branch(session_id: str, branch_id: str) -> Optional[str]:
    """The branch a branch was made from (its record's ``parent_branch_id``)."""
    try:
        from openprogram.runtime.context.session_db import default_db
        ref = ((default_db().get_session(session_id) or {}).get("branch_refs") or {}).get(branch_id)
    except Exception:
        return None
    parent = ref.get("parent_branch_id") if isinstance(ref, dict) else None
    return parent if isinstance(parent, str) and parent != branch_id else None


def todos_path(session_id: str):
    """Path to the session's todos.json, or None when the session repo
    doesn't exist."""
    from openprogram.runtime.context.session_db import default_db
    sessions = default_db()
    if not sessions._session_dir(session_id).exists():  # noqa: SLF001 — same as inbox.py
        return None
    return sessions.state_file(session_id, "todos.json")


def _read(session_id: str) -> dict[str, Any]:
    path = todos_path(session_id)
    if path is None or not path.exists():
        return {}
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return blob if isinstance(blob, dict) else {}


_NO_BRANCH = ""


def _branches(session_id: str, blob: Optional[dict] = None) -> dict[str, dict[str, Any]]:
    """Every branch's ``{tasks, todos, plans}``; an older file's single board
    is the selected branch's."""
    blob = _read(session_id) if blob is None else blob
    branches = blob.get("branches")
    if isinstance(branches, dict):
        return {key: value for key, value in branches.items() if isinstance(value, dict)}
    if "todos" in blob or "plans" in blob:
        return {_selected_branch(session_id) or _NO_BRANCH: {
            "todos": blob.get("todos"), "plans": blob.get("plans")}}
    return {}


def _entry(session_id: str, branch_id: Optional[str]) -> dict[str, Any]:
    """One branch's board. A branch with none yet starts from a copy of the
    board of the branch it was made from, stored on it at once, so later
    changes on either side stay apart."""
    key = branch_id or _NO_BRANCH
    branches = _branches(session_id)
    if key in branches:
        return branches[key]
    seen = {key}
    parent = _parent_branch(session_id, key) if branch_id else None
    while parent and parent not in seen:
        if parent in branches:
            copied = json.loads(json.dumps(branches[parent]))
            try:
                _write(session_id, {**branches, key: copied})
            except Exception:  # noqa: BLE001 - the copy is read again next time
                _log.debug("todo board copy not stored session=%s", session_id, exc_info=True)
            return copied
        seen.add(parent)
        parent = _parent_branch(session_id, parent)
    return {}


def _write(session_id: str, branches: dict[str, dict[str, Any]]) -> None:
    path = todos_path(session_id)
    if path is None:
        raise ValueError(f"session {session_id!r} not found")
    payload = {"version": 4, "branches": branches}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load(session_id: str, branch_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The board of ``branch_id`` (the current turn's branch by default)."""
    todos = _entry(session_id, branch_id or current_branch_id(session_id)).get("todos")
    return todos if isinstance(todos, list) else []


def _valid_plans(plans: Any) -> list[dict[str, Any]]:
    if not isinstance(plans, list):
        return []
    return [p for p in plans if isinstance(p, dict) and p.get("anchor_id") and isinstance(p.get("todos"), list)]


def load_plans(session_id: str, branch_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The placed plans of ``branch_id`` (the current turn's branch by default)."""
    return _valid_plans(_entry(session_id, branch_id or current_branch_id(session_id)).get("plans"))


def load_all_plans(session_id: str) -> list[dict[str, Any]]:
    """Every branch's placed plans: the chat draws each under its own turn,
    and a turn lies on one branch, so they never collide."""
    out: list[dict[str, Any]] = []
    seen: set = set()
    for entry in _branches(session_id).values():
        for plan in _valid_plans(entry.get("plans")):
            ident = (plan.get("anchor_id"), plan.get("plan_id"))
            if ident not in seen:
                seen.add(ident)
                out.append(plan)
    return out


def load_tasks(session_id: str, branch_id: Optional[str] = None) -> list[dict[str, Any]]:
    """The tasks of ``branch_id`` (the current turn's branch by default)."""
    tasks = _entry(session_id, branch_id or current_branch_id(session_id)).get("tasks")
    return [t for t in tasks if isinstance(t, dict)] if isinstance(tasks, list) else []


def save(session_id: str, todos: list[dict[str, Any]], plans: Optional[list[dict[str, Any]]] = None,
         tasks: Optional[list[dict[str, Any]]] = None, branch_id: Optional[str] = None) -> None:
    """Write one branch's board; ``plans`` or ``tasks`` omitted keeps what
    the branch has."""
    key = branch_id or current_branch_id(session_id) or _NO_BRANCH
    if plans is None:
        plans = load_plans(session_id, key)
    if tasks is None:
        tasks = load_tasks(session_id, key)
    branches = _branches(session_id)
    branches[key] = {"tasks": tasks, "todos": todos, "plans": plans}
    _write(session_id, branches)


def carry_board(session_id: str, source: Optional[str], target: Optional[str]) -> bool:
    """Give ``target`` the board ``source`` has now, replacing a copy it
    took earlier. For work that moves from one branch record to another (a
    Goal following the branch its last turn was written on): the tasks and
    steps go with it. False when ``source`` has no board."""
    if not source or not target or source == target:
        return False
    with _lock:
        branches = _branches(session_id)
        if source not in branches:
            return False
        _write(session_id, {**branches, target: json.loads(json.dumps(branches[source]))})
    return True


def next_id(todos: list[dict[str, Any]]) -> str:
    highest = 0
    for t in todos:
        try:
            highest = max(highest, int(t.get("id", 0)))
        except (TypeError, ValueError):
            continue
    return str(highest + 1)


def current_anchor() -> Optional[str]:
    """The assistant reply of the turn in flight: the reply a task the turn
    changes is drawn in. None outside a turn."""
    try:
        from openprogram.state.store import _current_turn_id
        raw = _current_turn_id.get() or ""
    except Exception:
        return None
    # A tool run in a function subprocess carries ``<reply>|node:<id>``.
    raw = raw.split("|node:", 1)[0]
    if raw.startswith("pred:"):
        raw = raw[len("pred:"):]
    return raw if raw and raw != "ROOT" else None


def scope_of(req: Any, anchor: Optional[str] = None) -> str:
    """Whose tasks a turn works on: "" for the conversation's own agent,
    ``agent:<id>:<reply>`` for a sub-agent run, whose tasks are its own.

    ``anchor`` is the run's reply. Every sub-agent of a conversation writes
    to the board of the branch it was sent from, so the reply is what keeps
    two runs of the same agent, side by side or one after the other, from
    reading each other's task."""
    if req is None or getattr(req, "source", None) != "agent_spawn":
        return ""
    scope = f"agent:{getattr(req, 'agent_id', '') or 'agent'}"
    return f"{scope}:{anchor}" if anchor else scope


def current_scope() -> str:
    """The running turn's :func:`scope_of`."""
    try:
        from openprogram.runtime.agent.turn_request import get_turn_request
        return scope_of(get_turn_request(), current_anchor())
    except Exception:
        return ""


def _id_order(todo: dict[str, Any]) -> int:
    try:
        return int(todo.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def steps_of(todos: list[dict[str, Any]], task_id: str) -> list[dict[str, Any]]:
    """The steps of one task, in the order they were written."""
    return sorted((t for t in todos if str(t.get("plan") or "") == task_id), key=_id_order)


def counted(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps that count: every one that was not dropped."""
    return [s for s in steps if s.get("status") != "dropped"]


def unfinished(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The steps still to do."""
    return [s for s in steps if s.get("status") in UNFINISHED]


def current_step(steps: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    return next((s for s in steps if s.get("status") == "in_progress"), None)


def open_task(tasks: list[dict[str, Any]], scope: str) -> Optional[dict[str, Any]]:
    """The task one agent has open: its latest with status ``open``."""
    return next((t for t in reversed(tasks)
                 if t.get("status") == "open" and (t.get("scope") or "") == scope), None)


def open_task_lines(session_id: str) -> list[str]:
    """The task the running agent has open, as a compaction restores it
    (``runtime.context.compaction.restore``): its title, purpose, numbered
    steps with status and result, and the step in progress. Tasks already
    closed are in the summary, not here."""
    task = open_task(load_tasks(session_id), current_scope())
    if task is None:
        return []
    lines = [str(task.get("title") or "").strip()]
    if task.get("purpose"):
        lines.append(str(task["purpose"]).strip())
    steps = steps_of(load(session_id), str(task.get("id")))
    current = None
    for number, step in enumerate(steps, 1):
        line = f"- [{step.get('status') or 'pending'}] {number} {str(step.get('subject') or '').strip()}"
        if step.get("result"):
            line += f" — {step['result']}"
        lines.append(line)
        if step.get("status") == "in_progress":
            current = number
    if current is not None:
        lines.append(f"Current step: {current}")
    return lines


def next_task_id(tasks: list[dict[str, Any]]) -> str:
    highest = 0
    for task in tasks:
        try:
            highest = max(highest, int(str(task.get("id") or "")[1:]))
        except (TypeError, ValueError):
            continue
    return f"T{highest + 1}"


def set_step(step: dict[str, Any], status: str, *, anchor: Optional[str] = None, result: str = "") -> None:
    """Move a step to ``status``, by the turn of ``anchor``."""
    step["status"] = status
    step["updated_at"] = time.time()
    if result:
        step["result"] = result
    if anchor:
        step["turn"] = anchor


def progress(todos: list[dict[str, Any]], task: dict[str, Any]) -> str:
    """One line on where a task stands, for a tool result: the model reads
    the state there instead of asking for it. A step's number is its place
    in the task, counting dropped ones."""
    steps = steps_of(todos, str(task.get("id")))
    if not steps:
        return "Task: no steps"
    kept = counted(steps)
    done = sum(1 for s in kept if s.get("status") == "completed")
    line = f"Task: {done} of {len(kept)} done"
    current = current_step(steps)
    upcoming = next((s for s in steps if s.get("status") == "pending"), None)
    if current is not None:
        line += f"; now step {steps.index(current) + 1} {current.get('subject')}"
    if upcoming is not None:
        line += f"; next step {steps.index(upcoming) + 1} {upcoming.get('subject')}"
    return line


def _snapshot(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"id": str(s.get("id")), "subject": s.get("subject") or "", "status": s.get("status") or "pending",
         **({"result": s["result"]} if s.get("result") else {})}
        for s in steps
    ]


def place_segment(session_id: str, todos: list[dict[str, Any]], tasks: list[dict[str, Any]], task_id: str, *,
                  state: Optional[str] = None, anchor: Optional[str] = None, actor: Optional[str] = None,
                  earlier: Optional[list[str]] = None, plans: Optional[list[dict[str, Any]]] = None,
                  ) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    """Record a task under the reply that changed it.

    One segment per (reply, task), holding the task and all its steps as
    the reply last left them: a later change in the same reply replaces the
    segment, a change in a later reply adds one, so earlier replies keep the
    task as they left it. ``state`` moves the segment to ``running``,
    ``done``, ``carried`` or ``displaced``; omitted, it stays as it is. A
    new segment remembers which steps were finished before it (``earlier``:
    the ids given, else the steps completed now).

    ``actor`` is the scope of who made the change (the running turn's by
    default). A sub-agent ticking a step of its parent's task updates the
    parent's latest segment instead, since the task is not the sub-agent's.

    Returns ``(plans to save, the segment that changed or None)``.
    """
    plans = load_plans(session_id) if plans is None else plans
    task = next((t for t in tasks if t.get("id") == task_id), None)
    anchor = current_anchor() if anchor is None else anchor
    if task is None:
        return plans, None
    steps = steps_of(todos, task_id)
    scope = task.get("scope") or ""
    now = time.time()
    running = current_step(steps)

    def write(segment: dict[str, Any]) -> dict[str, Any]:
        segment.setdefault("state", "running")
        if state is not None:
            segment["state"] = state
        if segment["state"] in ("done", "carried", "displaced") and not segment.get("ended_at"):
            segment["ended_at"] = now
        elif segment["state"] == "running":
            segment["ended_at"] = None
        segment.update(
            title=task.get("title") or "", purpose=task.get("purpose") or "",
            outcome=task.get("outcome") or "", advanced=bool(task.get("advanced")),
            current=(str(running["id"]) if running is not None
                     else None if segment["state"] == "done" else segment.get("current")),
            updated_at=now, todos=_snapshot(steps),
        )
        return segment

    if (current_scope() if actor is None else actor) != scope:
        owner = next((p for p in reversed(plans)
                      if p.get("plan_id") == task_id and (p.get("scope") or "") == scope), None)
        return plans, write(owner) if owner is not None else None
    if anchor is None:
        return plans, None
    for existing in plans:
        if existing.get("anchor_id") == anchor and existing.get("plan_id") == task_id:
            return plans, write(existing)
    done_before = [str(s["id"]) for s in steps if s.get("status") == "completed"]
    segment = write({
        "anchor_id": anchor, "plan_id": task_id, "scope": scope, "state": "running",
        "earlier": list(done_before if earlier is None else earlier),
        "started_at": now, "ended_at": None, "created_at": now,
    })
    plans.append(segment)
    return plans[-MAX_PLANS:], segment


def publish(session_id: str, changed: list[Optional[dict[str, Any]]], branch_id: Optional[str] = None) -> None:
    """Push the segments that changed to every connected UI tab (best-effort:
    the server may not be running, e.g. pure-CLI use or tests). A client
    replaces its copy of each by (``anchor_id``, ``plan_id``);
    ``load_session`` hydrates all of them."""
    segments = [segment for segment in changed if segment]
    if not segments:
        return
    try:
        from openprogram.runtime import interface_link
        interface_link.broadcast(
            {"type": "todos_update", "data": {
                "session_id": session_id,
                "branch_id": branch_id or current_branch_id(session_id),
                "changed": segments,
            }},
        )
    except Exception:
        # No interface may be running (CLI, tests); load_session hydrates the segments later.
        _log.debug("todos broadcast failed session=%s", session_id, exc_info=True)


def turn_owner(session_id: str, scope: str, goal: Optional[dict[str, Any]],
               branch_id: Optional[str] = None) -> Optional[dict[str, str]]:
    """What a reply belongs to before it calls a task tool.

    Nothing, except in a turn of the Goal the open task was started under:
    that turn continues the task, on the step in progress if there is one.
    """
    if not goal or not goal.get("goal_id"):
        return None
    task = open_task(load_tasks(session_id, branch_id), scope)
    if task is None or task.get("goal_id") != goal.get("goal_id") or task.get("goal_revision") != goal.get("revision"):
        return None
    running = current_step(steps_of(load(session_id, branch_id), str(task["id"])))
    return {"task": str(task["id"]), **({"step": str(running["id"])} if running is not None else {})}


def open_turn_segment(session_id: str, anchor: str, task_id: str, *, scope: str = "",
                      branch_id: Optional[str] = None) -> None:
    """Start this reply's segment of a task it continues without calling a
    task tool first (a Goal turn going on with the open task)."""
    with _lock:
        tasks = load_tasks(session_id, branch_id)
        task = next((t for t in tasks if t.get("id") == task_id), None)
        if task is None or task.get("status") != "open":
            return
        todos = load(session_id, branch_id)
        plans, segment = place_segment(
            session_id, todos, tasks, task_id, state="running", anchor=anchor, actor=scope,
            plans=load_plans(session_id, branch_id))
        save(session_id, todos, plans, tasks, branch_id)
        publish(session_id, [segment], branch_id)


def close_at_turn_end(session_id: str, anchor: str, *, scope: str = "",
                      branch_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Close the task a reply worked on, when the reply ended normally.

    Only a task this reply has a segment of is touched. The step in
    progress is finished only when that closes the task: the task was
    advanced, no step waits after it, and the task belongs to no Goal. A
    Goal's turn ends whenever the model stops calling tools, not when the
    step is done, so its step stays in progress for the next turn.

    Then: nothing left to do ends the task; a task that never advanced and
    belongs to no Goal ends too, its steps left as they are (no step is
    ticked for it); anything else stays open and the segment is marked
    ``carried`` for the next turn. A sub-agent's run has no next turn: what
    it leaves unfinished is closed as displaced, its open steps dropped.
    Returns the segment, or None when there was nothing to close.
    """
    with _lock:
        tasks = load_tasks(session_id, branch_id)
        task = open_task(tasks, scope)
        if task is None:
            return None
        task_id = str(task["id"])
        plans = load_plans(session_id, branch_id)
        if not any(p.get("anchor_id") == anchor and p.get("plan_id") == task_id for p in plans):
            return None
        todos = load(session_id, branch_id)
        steps = steps_of(todos, task_id)
        advanced = bool(task.get("advanced"))
        of_goal = bool(task.get("goal_id"))
        waiting = any(s.get("status") == "pending" for s in steps)
        if advanced and not waiting and (scope or not of_goal):
            for step in steps:
                if step.get("status") == "in_progress":
                    set_step(step, "completed", anchor=anchor)
        now = time.time()
        left = unfinished(steps)
        if not left or (not advanced and not of_goal):
            task.update(status="done", ended_at=now)
            state = "done"
        elif scope:
            task.update(status="displaced", ended_at=now)
            state = "displaced"
        else:
            state = "carried"
        task.update(updated_at=now, turn=anchor)
        plans, segment = place_segment(session_id, todos, tasks, task_id, state=state, anchor=anchor,
                                       actor=scope, plans=plans)
        if state == "displaced":
            # The segment keeps the task as the run left it.
            for step in left:
                set_step(step, "dropped", anchor=anchor)
        save(session_id, todos, plans, tasks, branch_id)
        publish(session_id, [segment], branch_id)
    notify_goal(session_id)
    return segment


def notify_goal(session_id: str) -> None:
    """Refresh derived Goal progress after saving and releasing the todo lock."""
    from openprogram.programs.tools.planning.goal import chat
    try:
        chat.refresh_todos(session_id)
    except Exception:
        # The todo is already durable; terminal accounting reconciles progress.
        import logging
        logging.getLogger(__name__).exception("Goal todo progress refresh deferred")


def lock() -> threading.Lock:
    return _lock
