"""process tool — manage background shell sessions started with the ``start`` action.

Separate from ``bash`` (which is synchronous and returns only when the command
exits). Use this when you need long-running servers, watchers, or any command
whose output you want to poll over time.
"""

from __future__ import annotations

from typing import Any

from openprogram.programs._runtime import function


NAME = "process"

DESCRIPTION = (
    "Manage long-running background shell sessions. Pair with `bash` (which is "
    "foreground/blocking) when you need to start a dev server, poll logs, "
    "or write to a subprocess' stdin.\n"
    "\n"
    "Actions:\n"
    "  start    — launch a command in the background, returns sessionId\n"
    "  list     — show this conversation’s managed processes (running + exited)\n"
    "  poll     — status + exit code + new output since last poll\n"
    "  log      — retained stdout/stderr (bounded; truncation is reported)\n"
    "  write    — send a line to the session's stdin\n"
    "  kill     — SIGTERM the process (waits up to 5s, then SIGKILL)\n"
    "  remove   — kill + discard the session\n"
)

SPEC: dict[str, Any] = {
    "name": NAME,
    "description": DESCRIPTION,
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "list", "poll", "log", "write", "kill", "remove"],
                "description": "What to do.",
            },
            "command": {
                "type": "string",
                "description": "Shell command — required for action=start.",
            },
            "session_id": {
                "type": "string",
                "description": "Target session — required for poll/log/write/kill/remove.",
            },
            "input": {
                "type": "string",
                "description": "Data to send to stdin (appended with a trailing newline) — for action=write.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory for action=start. Default: current process cwd.",
            },
        },
        "required": ["action"],
    },
}


def execute(
    action: str,
    command: str | None = None,
    session_id: str | None = None,
    input: str | None = None,
    cwd: str | None = None,
    **_: Any,
) -> str:
    from openprogram.processes import ProcessStore, control, current_owner, scoped_records, start

    try:
        owner_session, _, _ = current_owner()
        store = ProcessStore()
        if action == "start":
            record = start(command, cwd=cwd, store=store)
            if record["status"] == "failed":
                return f"Error: process launch failed; session_id={record['id']}; inspect log"
            return (f"started session_id={record['id']} pid={record['pid']} "
                    f"status={record['status']} backend={record['backend_id']}")
        records = scoped_records(store, owner_session)
        if action == "list":
            return "\n".join(
                f"{item['id']}  {item['status']}  pid={item['pid']}  "
                f"exit_code={item['exit_code']} truncated={int(item['truncated'])}  {item['command']}"
                for item in records
            ) or "(no sessions)"
        if not session_id:
            return f"Error: action={action} requires session_id"
        record = next((item for item in records if item["id"] == session_id), None)
        if record is None:
            return "Error: managed process not found"
        if action in {"poll", "log"}:
            output = store.output(session_id, poll=action == "poll")
            return (f"status={record['status']} exit_code={record['exit_code']} "
                    f"truncated={int(record['truncated'])}\n{output}")
        if action == "write":
            if input is None:
                return "Error: action=write requires input"
            control(store, session_id, "write", input)
            return f"wrote {len(input.encode('utf-8'))} bytes to {session_id}"
        if action in {"kill", "remove"}:
            record = control(store, session_id, "stop")
            if action == "remove":
                store.remove(session_id)
                return f"removed {session_id}"
            return f"stopped {session_id} (status={record['status']})"
        return f"Error: unknown action {action!r}"
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"


# Register as an AgentTool. ``execute`` stays a plain callable so any
# existing import-and-call sites keep working; the return value (an
# AgentTool) is discarded — it's already in the registry.
function(
    name=NAME,
    description=DESCRIPTION,
    parameters=SPEC["parameters"],
    toolset=['core'],
    unsafe_in=['wechat', 'telegram', 'plan'],
    # Exempt: same as bash — the command string is not a surface path.
    # Child sessions go through the backend / OS sandbox.
    path_params={},
    url_params=[],
)(execute)
