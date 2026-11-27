"""Agent tool for one-time, recurring, and monitor Scheduler tasks.

Tasks are kept by the Scheduler store (``runtime.scheduler.store``) and executed by the
persistent worker. Prompt tasks may hold stable Memory references that are
resolved when the task runs and maintained through the existing Memory tools.
"""

from __future__ import annotations

import json
from typing import Any

from openprogram.programs._authoring.helpers import read_string_param
from openprogram.programs._host.runtime import function
from openprogram.runtime.scheduler import store


NAME = "scheduler"
CRON_ALIAS_NAME = "cron"

DESCRIPTION = (
    "Create, list, update, and delete one-time, recurring, monitor, or webhook "
    "tasks. A task runs a background agent prompt, a shell command, or a reminder "
    "(the `remind` tool sets reminders in fewer words). "
    "To act when something changes, give a recurring or monitor task a `watch` "
    "command: its cron is how often the command is checked, and the task runs "
    "only when the command's output differs from the last check. A `webhook` "
    "task has no schedule: it runs when an outside service POSTs to the URL "
    "the create result gives, with the key it gives. "
    "Tasks run in the persistent OpenProgram worker and may attach stable, "
    "Memory references. For a task worth retaining in long-term Memory, use "
    "memory_update to create or update its record and attach that record here; "
    "when the task closes, update the record or delete it if nothing durable "
    "remains."
)



SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "update", "delete", "get"],
                "description": "What to do.",
            },
            "cron": {
                "type": "string",
                "description": "5-field cron expression for recurring or monitor tasks.",
            },
            "run_at": {
                "type": "string",
                "description": "Timezone-aware ISO 8601 timestamp for a one-time task.",
            },
            "type": {
                "type": "string",
                "enum": ["once", "recurring", "monitor", "webhook"],
                "description": "Task type. Inferred from run_at/cron when omitted.",
            },
            "title": {
                "type": "string",
                "description": "Short task title.",
            },
            "enabled": {
                "type": "boolean",
                "description": "Whether the task may run.",
            },
            "memory_refs": {
                "type": "array",
                "maxItems": 16,
                "items": {
                    "type": "object",
                    "properties": {
                        "workspace_id": {"type": "string"},
                        "memory_id": {"type": "string"},
                    },
                    "required": ["workspace_id", "memory_id"],
                    "additionalProperties": False,
                },
                "description": "Stable Memory blocks resolved at execution time.",
            },
            "prompt": {
                "type": "string",
                "description": "The prompt / task the daemon should hand to a fresh agent when the schedule fires. Either `prompt` or `command` is required for create.",
            },
            "command": {
                "type": "string",
                "description": "Shell command to run when the schedule fires — runs directly, no agent involved. Mutually exclusive with `prompt`. Example: `python backup.py` or `rsync -a ~/src /backup/`.",
            },
            "reminder": {
                "type": "string",
                "description": "Reminder text shown to the user in this conversation when the schedule fires. Mutually exclusive with `prompt` and `command`.",
            },
            "wake": {
                "type": "boolean",
                "description": "For a reminder: also start a turn in this conversation so the agent acts on the reminder text.",
            },
            "watch": {
                "type": "string",
                "description": "For a recurring or monitor task: a shell command checked on the cron schedule (sandboxed, like `command`). The task runs only when its output changed since the previous check; the first check records the baseline. The run receives the change.",
            },
            "cwd": {
                "type": "string",
                "description": "Absolute working directory to freeze into the execution spec. Defaults to the active project directory.",
            },
            "notes": {
                "type": "string",
                "description": "Optional free-form notes for your own reference.",
            },
            "id": {
                "type": "string",
                "description": "Entry id. Required for delete / get.",
            },
        },
        "required": ["action"],
    },
}




def execute(
    action: str | None = None,
    title: str | None = None,
    type: str | None = None,
    cron: str | None = None,
    run_at: str | None = None,
    prompt: str | None = None,
    command: str | None = None,
    cwd: str | None = None,
    notes: str | None = None,
    enabled: bool | None = None,
    memory_refs: list[dict[str, str]] | None = None,
    id: str | None = None,
    reminder: str | None = None,
    wake: bool | None = None,
    watch: str | None = None,
    **kw: Any,
) -> str:
    action = action or read_string_param(kw, "action", "op")
    title = title or read_string_param(kw, "title", "name")
    task_type = type or read_string_param(kw, "type", "task_type", "kind")
    cron_expr = cron or read_string_param(kw, "cron", "schedule", "expression")
    run_at = run_at or read_string_param(kw, "run_at", "at")
    prompt = prompt or read_string_param(kw, "prompt", "task", "text")
    command = command or read_string_param(kw, "command", "cmd", "shell")
    reminder = reminder or read_string_param(kw, "reminder", "message")
    cwd = cwd or read_string_param(kw, "cwd", "working_dir")
    notes = notes or read_string_param(kw, "notes", "note", "description")
    entry_id = id or read_string_param(kw, "id", "entry_id", "slug")

    if not action:
        return "Error: `action` is required (create / list / update / delete / get)."
    action = action.lower()
    if action not in {"create", "list", "update", "delete", "get"}:
        return f"Error: unknown action {action!r}. Expected create / list / update / delete / get."
    authority, authority_error = store._authorize(action)
    if authority_error:
        return authority_error

    path = store._resolve_path()
    entries = store._load(path)

    if action == "list":
        if not entries:
            return f"No scheduled tasks in `{path}`."
        lines = [f"Scheduled tasks in `{path}`:"]
        refused = store.read_refusals()
        for e in entries:
            body = e.get("prompt") or e.get("command") or e.get("reminder") or ""
            kind = "$" if e.get("command") else "⏰" if e.get("reminder") else ">"
            schedule = e.get("run_at") or e.get("cron") or ("on POST" if e.get("type") == "webhook" else "unscheduled")
            if e.get("watch"):
                schedule += f" when `{e['watch'][:40]}` changes"
            line = f"- `{e.get('id','?')}`  {e.get('type','recurring')}  {schedule}  {kind} {body[:80]}"
            if e.get("notes"):
                line += f"   _({e['notes']})_"
            if e.get("id") in refused:
                line += f"   [last run refused: {refused[e['id']]}]"
            lines.append(line)
        return "\n".join(lines)

    if action == "get":
        if not entry_id:
            return "Error: `id` is required for get."
        for e in entries:
            if e.get("id") == entry_id:
                return json.dumps(e, indent=2, ensure_ascii=False)
        return f"Error: no entry with id {entry_id!r}."

    if action == "delete":
        if not entry_id:
            return "Error: `id` is required for delete."
        from openprogram.runtime.scheduler import service

        if not service.delete_task(entry_id, by="the cron tool"):
            return f"Error: no entry with id {entry_id!r}."
        return f"Deleted scheduler task {entry_id!r} from `{path}`."

    if action == "update":
        if not entry_id:
            return "Error: `id` is required for update."
        from openprogram.runtime.scheduler import service

        patch: dict[str, Any] = {}
        for key, value in (
            ("title", title), ("cron", cron_expr), ("run_at", run_at),
            ("notes", notes), ("enabled", enabled), ("wake", wake),
            ("watch", watch),
        ):
            if value is not None:
                patch[key] = value
        for key, value in (
            ("prompt", prompt), ("command", command), ("cwd", cwd),
            ("memory_refs", memory_refs), ("reminder", reminder),
        ):
            if value is not None:
                patch[key] = value
        if not patch:
            return "Error: update requires at least one changed field."
        try:
            updated = service.update_task(entry_id, patch, authority=authority)
        except KeyError:
            return f"Error: no task with id {entry_id!r}."
        except ValueError as exc:
            return f"Error: {exc}"
        return json.dumps(updated, indent=2, ensure_ascii=False)

    if action == "create":
        if sum(bool(value) for value in (prompt, command, reminder)) != 1:
            return "Error: pass exactly one of `prompt` (agent task), `command` (shell), or `reminder`."
        task_type = task_type or ("once" if run_at else "recurring")
        watch = watch or read_string_param(kw, "watch")
        task_title = title or notes or (prompt or command or reminder or "Scheduled task")[:80]
        from openprogram.runtime.scheduler import service

        try:
            new_entry = service.create_task(
                title=task_title,
                task_type=task_type,
                prompt=prompt,
                command=command,
                reminder=reminder,
                wake=bool(wake),
                cron=cron_expr,
                run_at=run_at,
                enabled=True if enabled is None else enabled,
                memory_refs=memory_refs,
                notes=notes or "",
                cwd=cwd,
                authority=authority,
                watch=watch,
            )
        except ValueError as exc:
            return f"Error: {exc}"
        body_label = "prompt" if prompt else "command" if command else "reminder"
        body_value = prompt or command or reminder or ""
        schedule = new_entry.get("run_at") or new_entry.get("cron")
        if new_entry.get("type") == "webhook":
            from openprogram.runtime.scheduler import hooks
            return (
                f"Created webhook task `{new_entry['id']}`:\n"
                f"  {body_label}: {body_value[:160]}\n"
                f"  URL: {hooks.url(new_entry['id'])}\n"
                f"  Key: {new_entry['hook_key']}\n"
                "Give the user both. A service starts the task with a POST to the URL that carries "
                "the key in the X-OpenProgram-Key header, or, for a GitHub webhook, with the key "
                "as the webhook secret. The request body reaches the run as outside data."
            )
        watch_line = f"  watch: {new_entry['watch'][:160]} (runs only when its output changes)\n" \
            if new_entry.get("watch") else ""
        return (
            f"Created scheduler task `{new_entry['id']}` in `{path}`:\n"
            f"  schedule: {schedule}\n"
            f"{watch_line}"
            f"  {body_label}: {body_value[:160]}\n"
            "The persistent OpenProgram worker will execute it."
        )

    raise AssertionError("validated cron action was not handled")


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=["core"],
)(execute)

# Keep the former tool name callable for existing profiles and clients while
# presenting Scheduler as the primary contract.
function(
    name=CRON_ALIAS_NAME,
    description="Compatibility alias for the scheduler tool.",
    parameters=SPEC["parameters"],
    toolset=["core"],
)(execute)


__all__ = ["NAME", "CRON_ALIAS_NAME", "SPEC", "execute", "DESCRIPTION"]
