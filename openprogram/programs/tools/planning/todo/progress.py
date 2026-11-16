"""Task progress carried on a real tool call (``task_progress``).

A model that reports progress with its own ``task_start`` or ``task_step``
call often sends that call alone, and each one then costs a model round
trip. So every tool the model can call while the task tools are offered
takes one more optional argument, ``task_progress``, and the runtime
applies it before the call runs. It says where the call stands, not what
changed, so sending it again on every call of a step changes nothing:

    {"task": "Fix the inventory", "steps": ["Run tests", "Fix", ...], "step": 1}
        this call is work of step 1 of that task: the task is opened when
        no open task has that title
    {"step": 2, "result": "two bugs found"}
        this call is work of step 2 of the open task: the step in progress
        is finished (with that result) and step 2 starts

Measured on gpt-6-luna, a model given ``{start}`` / ``{next}`` deltas sent
the opening delta again on every call and never moved on; a state it can
repeat is what it writes anyway.

The argument is added where the request's tools array is assembled
(``annotate``), never to a tool's own definition, and taken out of the
arguments before they are checked (``take``), so a tool, an MCP server or
a plugin never sees it. ``task_start`` / ``task_step`` stay for what has no
call to ride on: a purpose, dropped steps, an outcome. The end of the
reply closes the task (``shared.close_at_turn_end``).

Applying it runs the same code as the task tools, so a sub-agent's
progress goes to its own task, and a call dispatched again after a pause
finds its occurrence id on the task and is not applied twice.
"""
from __future__ import annotations

from typing import Any, Optional

#: The argument's name in every annotated tool's schema.
KEY = "task_progress"

#: Tools that never take it: the task tools themselves (they say it with
#: their own arguments) and the loader, whose call is no step's work.
EXCLUDED = frozenset({"task_start", "task_step", "task_list", "tool_search"})

#: Whether a request offers the task tools: only then is progress taken.
OFFERED_BY = "task_start"

SCHEMA: dict[str, Any] = {
    "type": "object",
    "description": (
        "Where this call stands in a task of three or more steps; leave it out for small work. "
        "task: the task's title; steps: all its steps, on the first call; "
        "step: the number of the step this call works on (1 = first). "
        "Send it on the calls of the task; when step changes, the step before is done "
        "(result: half a sentence on what it found)."
    ),
    "properties": {
        "task": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "step": {"type": "integer"},
        "result": {"type": "string"},
    },
}


def offered(tools: Any) -> bool:
    """Whether ``tools`` (the turn's offered tools) include the task tools."""
    return any(getattr(tool, "name", None) == OFFERED_BY for tool in (tools or ()))


def takes(tool: Any) -> bool:
    """Whether ``tool`` is sent with the argument: any tool but the excluded
    ones, unless its own schema already has a property of that name."""
    if tool is None or getattr(tool, "name", None) in EXCLUDED:
        return False
    parameters = getattr(tool, "parameters", None)
    if not isinstance(parameters, dict) or parameters.get("type", "object") != "object":
        return False
    return KEY not in (parameters.get("properties") or {})


def annotate(provider_tools: list, offered_tools: Any) -> list:
    """The request's tools array with the argument added to each tool that
    takes it, when the turn offers the task tools. The tools themselves are
    left as they are: each annotated one is a copy."""
    if not offered(offered_tools):
        return provider_tools
    out = []
    for tool in provider_tools:
        if takes(tool):
            parameters = dict(tool.parameters)
            parameters["properties"] = {**(parameters.get("properties") or {}), KEY: SCHEMA}
            tool = tool.model_copy(update={"parameters": parameters})
        out.append(tool)
    return out


def take(tool: Any, args: dict, offered_tools: Any) -> tuple[dict, Optional[Any]]:
    """``args`` without the argument, and the argument (None when absent).
    A tool that does not take it keeps its arguments as sent."""
    if not isinstance(args, dict) or KEY not in args or not takes(tool):
        return args, None
    args = dict(args)
    value = args.pop(KEY)
    return args, (value if offered(offered_tools) else None)


def _text(result: Any) -> str:
    if isinstance(result, str):
        return result
    return "\n".join(getattr(c, "text", "") for c in (getattr(result, "content", None) or []) if getattr(c, "text", ""))


def _effect(result: Any) -> Optional[dict]:
    details = getattr(result, "details", None)
    return details.get("task") if isinstance(details, dict) else None


def _open() -> tuple[Optional[str], Optional[dict], list]:
    """The session, the running agent's open task and its steps."""
    from . import shared
    sid = shared.current_session_id()
    if not sid:
        return None, None, []
    task = shared.open_task(shared.load_tasks(sid), shared.current_scope())
    steps = shared.steps_of(shared.load(sid), str(task["id"])) if task is not None else []
    return sid, task, steps


def _owner(task: dict, steps: list) -> dict:
    from . import shared
    running = shared.current_step(steps)
    return {"task": str(task["id"]), **({"step": str(running["id"])} if running is not None else {})}


def _applied(call_id: str) -> Optional[dict]:
    """Where the task of a call applied before stands now (a call dispatched
    again after a pause). None when the call was never applied."""
    from . import shared
    sid = shared.current_session_id()
    if not sid or not call_id:
        return None
    for task in shared.load_tasks(sid):
        if call_id in (task.get("calls") or ()):
            if task.get("status") != "open":
                return {"task": None}
            return _owner(task, shared.steps_of(shared.load(sid), str(task["id"])))
    return None


def _title(value: Any) -> str:
    from . import shared
    return " ".join(str(value or "").split())[:shared.MAX_TITLE].strip()


def _number(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def apply(value: Any, call_id: str) -> tuple[Optional[dict], str]:
    """Apply one call's ``task_progress``; ``call_id`` names this call's
    occurrence, unique per response. Returns ``(effect, note)``: the
    task and step the call and what follows it belong to (None when nothing
    moved), and a sentence for the model when the progress could not be
    applied (empty when it was, or when it changed nothing)."""
    if not isinstance(value, dict):
        return None, f"{KEY} ignored: it is an object such as {{\"step\": 2}}."
    title = _title(value.get("task"))
    number = _number(value.get("step"))
    result = " ".join(str(value.get("result") or "").split())
    if number == 0 or (number is not None and number < 0):
        return None, f"{KEY} ignored: step is the number of a step, 1 for the first."
    if not title and number is None:
        return None, ""
    seen = _applied(call_id)
    if seen is not None:
        return seen, ""
    _, task, steps = _open()
    effect: Optional[dict] = None
    if title and (task is None or _title(task.get("title")).casefold() != title.casefold()):
        from .task_start.task_start import _task_start_impl
        listed = value.get("steps")
        started = _task_start_impl(title, "", listed if isinstance(listed, (list, str)) else None, call_id=call_id)
        effect = _effect(started)
        if effect is None:
            return None, f"{KEY} not applied: {_text(started)}"
        _, task, steps = _open()
        if number in (None, 1):
            return effect, ""
    if task is None:
        return None, (f"{KEY} not applied: no task is open. Name the task with "
                      "task and steps on the first call of the work.")
    if number is None:
        return effect, ""
    from . import shared
    running = shared.current_step(steps)
    if 1 <= number <= len(steps) and steps[number - 1] is running:
        if result:
            # The step goes on; its result so far is kept on it.
            _keep_result(str(running["id"]), result)
        return effect, ""
    if number <= len(steps) and steps[number - 1].get("status") in ("completed", "dropped"):
        # A step already finished or given up is not taken up again: a
        # parallel call written for the step before, sent after the call
        # that moved on, would otherwise finish the step in progress.
        return effect, ""
    if number > len(steps):
        if running is not None and running is steps[-1]:
            # No such step: the call is more of the last step's work.
            return effect, ""
        return effect, (f"{KEY}: the task has steps 1 to {len(steps)}, not {number}; "
                        "the block stays where it was.")
    from .task_step.task_step import _task_step_impl
    moved = _task_step_impl(False, number, None, None, result, "", call_id=call_id)
    return _effect(moved) or effect, ""


def _keep_result(step_id: str, result: str) -> None:
    from . import shared
    sid = shared.current_session_id()
    if not sid:
        return
    with shared.lock():
        todos = shared.load(sid)
        step = next((t for t in todos if str(t.get("id")) == step_id), None)
        if step is not None and step.get("result") != result:
            step["result"] = result
            shared.save(sid, todos)
