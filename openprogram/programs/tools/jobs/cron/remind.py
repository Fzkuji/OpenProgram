"""remind — set a reminder for the user, or schedule the agent to act later.

A thin, resident front for Scheduler ``reminder`` tasks: one call names
when (a local time, minutes from now, or a cron expression) and what. The
reply states the absolute local time, so the model can tell the user
exactly when, since its prompt carries only today's date. Delivery lives in
``openprogram/runtime/scheduler/reminders.py``.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

from openprogram.programs._host.runtime import function

NAME = "remind"

DESCRIPTION = (
    "Set a reminder at a specific time, or schedule yourself to act later. Use it when "
    "the user asks to be reminded, or when something should happen later (\"tomorrow at "
    "9, check the CI\"). By default the user gets a notification with `message` and "
    "it is posted to this conversation's chat channel; with wake_agent=true a turn "
    "starts here at that time with `message` as its instruction, so you do the work "
    "then. Give exactly one of at, in_minutes, or cron. Times without a timezone are "
    "the user's local time. List or cancel reminders with the scheduler tool."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": "What to remind the user of, or, with wake_agent, what you will do then.",
        },
        "at": {
            "type": "string",
            "description": "When, once: \"HH:MM\" (next occurrence), \"YYYY-MM-DD HH:MM\", or ISO 8601.",
        },
        "in_minutes": {
            "type": "number",
            "description": "When, once: this many minutes from now.",
        },
        "cron": {
            "type": "string",
            "description": "Repeat on a five-field cron expression in local time, e.g. \"0 9 * * 1-5\".",
        },
        "wake_agent": {
            "type": "boolean",
            "description": "Start a turn in this conversation at that time so you act on `message`.",
        },
    },
    "required": ["message"],
}


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def resolve_at(value: str, now: dt.datetime) -> dt.datetime:
    """A one-time ``at`` as an aware datetime; a bare time is its next occurrence."""
    text = value.strip()
    try:
        clock = dt.time.fromisoformat(text)
    except ValueError:
        clock = None
    if clock is not None:
        when = now.replace(hour=clock.hour, minute=clock.minute, second=0, microsecond=0)
        return when if when > now else when + dt.timedelta(days=1)
    try:
        when = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("`at` must be HH:MM, YYYY-MM-DD HH:MM, or ISO 8601") from exc
    return when.astimezone() if when.tzinfo else when.replace(tzinfo=now.tzinfo)


def next_cron(expr: str, now: dt.datetime, days: int = 8) -> dt.datetime | None:
    """The next minute ``expr`` fires within ``days``, in local time."""
    from openprogram.programs.tools.jobs.cron.worker import match

    minute = now.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
    for _ in range(days * 24 * 60):
        if match(expr, minute.replace(tzinfo=None)):
            return minute
        minute += dt.timedelta(minutes=1)
    return None


def _describe(when: dt.datetime, now: dt.datetime) -> str:
    minutes = max(0, round((when - now).total_seconds() / 60))
    span = f"{minutes} min" if minutes < 120 else f"{minutes / 60:.1f} h" if minutes < 48 * 60 else f"{minutes // 1440} days"
    return f"{when:%a %Y-%m-%d %H:%M %Z} (in {span})"


def remind(
    message: str,
    at: str | None = None,
    in_minutes: float | None = None,
    cron: str | None = None,
    wake_agent: bool | None = None,
) -> str:
    """Create a Scheduler reminder task and say when it fires."""
    from openprogram.programs.tools.jobs.cron import cron as cron_tool
    from openprogram.runtime.scheduler import service

    message = (message or "").strip()
    if not message:
        return "Error: `message` is required."
    given = [name for name, value in (("at", at), ("in_minutes", in_minutes), ("cron", cron))
             if value not in (None, "")]
    if len(given) != 1:
        return "Error: give exactly one of `at`, `in_minutes`, or `cron`."
    authority, error = cron_tool._authorize("create")
    if error:
        return error
    now = _now()
    try:
        if cron:
            when, task_type, schedule = next_cron(cron, now), "recurring", {"cron": cron}
        else:
            if in_minutes is not None:
                if in_minutes <= 0:
                    return "Error: `in_minutes` must be positive."
                when = now + dt.timedelta(minutes=in_minutes)
            else:
                when = resolve_at(str(at), now)
            if when <= now:
                return f"Error: {when:%Y-%m-%d %H:%M %Z} has already passed (now {now:%Y-%m-%d %H:%M %Z})."
            task_type, schedule = "once", {"run_at": when.isoformat()}
        task = service.create_task(
            title=message[:80], task_type=task_type, reminder=message,
            wake=bool(wake_agent), authority=authority, **schedule,
        )
    except ValueError as exc:
        return f"Error: {exc}"
    action = "you act on it here" if wake_agent else "the user is notified"
    timing = (f"fires {_describe(when, now)}" if task_type == "once" else
              f"repeats on `{cron}` (local time)" + (f", next {_describe(when, now)}" if when else ""))
    return (f"Reminder `{task['id']}` set: {timing}; {action}. "
            f"Now is {now:%a %Y-%m-%d %H:%M %Z}. Cancel with scheduler delete id={task['id']}.")


# Registered as an AgentTool; ``remind`` stays a plain callable.
function(name=NAME, description=DESCRIPTION, parameters=PARAMETERS, toolset=["core"])(remind)


__all__ = ["NAME", "DESCRIPTION", "PARAMETERS", "remind", "resolve_at", "next_cron"]
