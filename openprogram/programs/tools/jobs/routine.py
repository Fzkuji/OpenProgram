"""save_routine — turn work done in this conversation into a routine.

A routine is an ordinary Skill, written to the user's skill directory under
``routines/<slug>/SKILL.md``: the steps the agent actually took and the
corrections the user made. With a cron schedule it is also a Scheduler
reminder that wakes this conversation with "Run the saved routine ...", so
the agent loads the Skill with the ``skill`` tool and does the work again.
Saving under the same name again rewrites that Skill and moves its schedule;
nothing here is new machinery beyond the file layout.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from openprogram.programs._host.runtime import function

NAME = "save_routine"

ROUTINE_ROOT = "routines"
CORRECTIONS_HEADING = "## Corrections from the user"

DESCRIPTION = (
    "Save work you just did in this conversation as a routine, so it can be done the same "
    "way again: when the user says \"do this every Monday\", \"save this as a routine\" or "
    "\"from now on do it like this\". It writes a Skill (`routines/<name>`) holding the steps "
    "you actually took, concrete enough to repeat without this conversation (sources, tools, "
    "file paths, output format), and the user's corrections. With `cron` it also schedules a "
    "run: at those times a turn starts in this conversation, you load the skill with the "
    "skill tool and follow it. When the user corrects how a routine was done, during a run "
    "or later, call this again with the same name, the full current steps, and the "
    "correction added to `corrections`; that updates the same Skill and keeps its schedule. "
    "Cancel a schedule with the scheduler tool."
)

PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Short name of the routine, e.g. \"weekly report\". The same name updates it.",
        },
        "description": {
            "type": "string",
            "description": "One line: what the routine does and when to use it.",
        },
        "steps": {
            "type": "array",
            "items": {"type": "string"},
            "description": "The steps, in order, as you actually did them; each one concrete enough to repeat.",
        },
        "corrections": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Every correction the user made to how this is done, as rules. Leave it out to "
                "keep the saved corrections; pass the full list to change them."
            ),
        },
        "cron": {
            "type": "string",
            "description": (
                "Run it on a five-field cron expression in local time, e.g. \"0 9 * * 1\" for "
                "Mondays at 9:00. Leave it out to keep the current schedule, or for no schedule."
            ),
        },
    },
    "required": ["name", "description", "steps"],
}


def slug(name: str) -> str:
    """The routine's directory name: lowercase words joined by hyphens."""
    text = re.sub(r"[^\w]+", "-", (name or "").strip().lower(), flags=re.UNICODE)
    return text.strip("-_")[:64].strip("-_")


def skill_name(name: str) -> str:
    return f"{ROUTINE_ROOT}/{slug(name)}"


def routine_path(name: str) -> Path:
    from openprogram.extensions.skills.loader import user_dir

    return user_dir() / ROUTINE_ROOT / slug(name) / "SKILL.md"


def _one_line(text: str) -> str:
    return " ".join(str(text or "").split())


def _clean(items: list[str] | None) -> list[str]:
    return [str(item).strip() for item in (items or []) if str(item or "").strip()]


def _indent(text: str) -> str:
    """A list item's continuation lines, indented under it."""
    first, *rest = text.splitlines()
    return "\n".join([first, *("   " + line if line.strip() else "" for line in rest)])


def saved_corrections(body: str) -> list[str]:
    """The corrections listed in a routine Skill body."""
    found: list[str] = []
    inside = False
    for line in body.splitlines():
        if line.startswith("## "):
            inside = line.strip() == CORRECTIONS_HEADING
            continue
        if inside and line.startswith("- "):
            found.append(line[2:].strip())
        elif inside and line.startswith("   ") and found:
            found[-1] += "\n" + line[3:]
    return found


def render_body(title: str, description: str, steps: list[str], corrections: list[str]) -> str:
    lines = [f"# {title}", "", description, "", "## Steps", ""]
    lines += [f"{index}. {_indent(step)}" for index, step in enumerate(steps, 1)]
    if corrections:
        lines += ["", CORRECTIONS_HEADING, ""]
        lines += [f"- {_indent(item)}" for item in corrections]
        lines += ["", "Where a correction and a step disagree, follow the correction."]
    return "\n".join(lines) + "\n"


def _wake_message(name: str) -> str:
    return (f"Run the saved routine `{name}`: load it with the skill tool, then follow its "
            "steps and corrections.")


def _schedule(name: str, title: str, cron: str, task_id: str) -> tuple[dict[str, Any] | None, str | None]:
    """Create the routine's waking reminder, or move the one it has."""
    from openprogram.programs.tools.jobs.cron import cron as cron_tool
    from openprogram.runtime.scheduler import service

    authority, error = cron_tool._authorize("create")
    if error:
        return None, error
    message = _wake_message(name)
    try:
        if task_id and service.get_task(task_id):
            task = service.update_task(
                task_id, {"cron": cron, "reminder": message, "wake": True, "title": f"Routine: {title}"},
                authority=authority,
            )
        else:
            task = service.create_task(
                title=f"Routine: {title}"[:200], task_type="recurring", reminder=message,
                wake=True, cron=cron, authority=authority,
            )
    except ValueError as exc:
        return None, f"Error: {exc}"
    return task, None


def save_routine(
    name: str,
    description: str,
    steps: list[str],
    corrections: list[str] | None = None,
    cron: str | None = None,
) -> str:
    """Write the routine's Skill and, with ``cron``, schedule its runs."""
    from openprogram.state.frontmatter import dump_frontmatter, parse_frontmatter

    title = _one_line(name)
    if not slug(title):
        return "Error: `name` must contain letters or digits."
    description = _one_line(description)
    if not description:
        return "Error: `description` is required."
    steps = _clean(steps if isinstance(steps, list) else [steps])
    if not steps:
        return "Error: `steps` must list at least one step."
    cron = _one_line(cron)

    path = routine_path(title)
    existing: dict[str, Any] = {}
    if path.is_file():
        existing, old_body = parse_frontmatter(path.read_text(encoding="utf-8"))
        if corrections is None:
            corrections = saved_corrections(old_body)
    corrections = _clean(corrections)
    full_name = skill_name(title)

    task_id = str(existing.get("routine-task") or "")
    schedule = str(existing.get("routine-schedule") or "")
    task = None
    if cron:
        task, error = _schedule(full_name, title, cron, task_id)
        if error:
            return error
        task_id, schedule = str(task["id"]), str(task.get("cron") or cron)

    front: dict[str, Any] = {"name": full_name, "description": f"Saved routine. {description}"}
    if task_id:
        front["routine-task"] = task_id
        front["routine-schedule"] = schedule
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            dump_frontmatter(front, "\n" + render_body(title, description, steps, corrections)),
            encoding="utf-8",
        )
    except OSError as exc:
        if task is not None and task_id != str(existing.get("routine-task") or ""):
            from openprogram.runtime.scheduler import service
            service.delete_task(task_id)
        return f"Error: could not write {path}: {exc}"

    verb = "Updated" if existing else "Saved"
    reply = [f"{verb} routine `{full_name}` at {path} ({len(steps)} steps, "
             f"{len(corrections)} corrections)."]
    if task is not None:
        from openprogram.programs.tools.jobs.cron.remind import next_cron

        now = dt.datetime.now().astimezone()
        when = next_cron(schedule, now)
        upcoming = f", next {when:%a %Y-%m-%d %H:%M %Z}" if when else ""
        reply.append(f"It runs on `{schedule}` (local time){upcoming}: a turn starts in this "
                     f"conversation. Scheduler task `{task_id}`; cancel with scheduler delete id={task_id}.")
    elif task_id:
        reply.append(f"Its schedule is unchanged: `{schedule}` (scheduler task `{task_id}`).")
    else:
        reply.append("No schedule; the user can run it by asking for it by name.")
    return " ".join(reply)


function(name=NAME, description=DESCRIPTION, parameters=PARAMETERS, toolset=["core"])(save_routine)


__all__ = [
    "NAME", "DESCRIPTION", "PARAMETERS", "save_routine", "slug", "skill_name",
    "routine_path", "render_body", "saved_corrections",
]
