"""Small Activity rows for tool calls that have started but have not returned."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping


def active_tool_calls(store, executions):
    from .effects import EffectStore, EffectStatus

    effects = EffectStore(store)
    rows = []
    for execution in executions:
        if execution.status.value not in {'running', 'pausing', 'cancelling', 'reconciliation_required'}:
            continue
        for effect in effects.list_unresolved(execution.execution_id):
            if effect.metadata.get('kind') != 'tool.before':
                continue
            payload = effect.metadata.get('payload')
            if not isinstance(payload, Mapping):
                continue
            arguments = payload.get('arguments')
            arguments = arguments if isinstance(arguments, Mapping) else {}
            running = (effect.status is EffectStatus.DISPATCHED
                       and execution.current_attempt_id == effect.attempt_id
                       and execution.status.value in {'running', 'pausing', 'cancelling'})
            rows.append({
                'id': effect.effect_id, 'execution_id': execution.execution_id,
                'session_id': execution.session_id,
                'tool_call_id': payload.get('tool_call_id'),
                'name': str(payload.get('tool_name') or 'function'),
                'description': str(arguments.get('description') or '')[:300],
                'command': str(arguments.get('command') or '')[:4000],
                'prompt': str(arguments.get('prompt') or '')[:4000],
                'started_at': effect.dispatched_at or effect.created_at,
                'status': 'running' if running else 'unknown',
            })
    return sorted(rows, key=lambda row: row['started_at'])



_JOB_OWNER = re.compile(r"^(j_[0-9a-f]+)_")


def agent_calls(store, session_id, executions, read_messages):
    """Every returned ``agent`` call in the conversation's transcripts,
    paired with the sub-agent (Job) it started.

    A Job records the reply that spawned it and the prompt it was given;
    the call with the same caller and prompt started it. When prompts do
    not line up, a caller left with one Job and one call that did not
    fail pairs those. A call paired with none never started a sub-agent.
    ``owner_job_id`` names the sub-agent whose branch made the call, null
    on the main conversation.
    """
    jobs = []
    for execution in executions:
        if not execution.execution_id.startswith("j_"):
            continue
        payload = store.get_job_agent_input(execution.execution_id) or {}
        request = payload.get("turn_request") if isinstance(payload, Mapping) else None
        if not isinstance(request, Mapping):
            continue
        jobs.append({
            "id": execution.execution_id,
            "caller": request.get("spawn_caller"),
            "prompt": str(request.get("user_text") or "").strip(),
            "at": float(getattr(execution, "created_at", 0) or 0),
        })
    jobs.sort(key=lambda job: job["at"])
    rows = []
    for sid in dict.fromkeys([session_id, *(execution.session_id for execution in executions)]):
        for message in read_messages(sid) or []:
            if message.get("function") != "agent" or message.get("role") != "tool":
                continue
            extra = message.get("extra")
            try:
                extra = json.loads(extra) if isinstance(extra, str) else extra
                arguments = ((extra or {}).get("tool_use") or {}).get("arguments")
                arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
            except (TypeError, ValueError, AttributeError):
                arguments = None
            arguments = arguments if isinstance(arguments, Mapping) else {}
            caller = str(message.get("caller") or "")
            owner = _JOB_OWNER.match(caller)
            content = str(message.get("content") or "").strip()
            # A stopped call is an error too, but not a failed start. Calls
            # from before failed starts were marked so carry only the text.
            failed = message.get("outcome") == "failed" or content.startswith("[agent error]")
            try:
                at = float(message.get("timestamp") or message.get("created_at") or 0)
            except (TypeError, ValueError):
                at = 0.0
            rows.append({
                "id": str(message.get("id") or ""), "caller": caller,
                "owner_job_id": owner.group(1) if owner else None, "job_id": None,
                "description": str(arguments.get("description") or "")[:300],
                "prompt": str(arguments.get("prompt") or "")[:4000],
                "_prompt": str(arguments.get("prompt") or "").strip(),
                "at": at, "outcome": message.get("outcome"), "failed": failed,
                "error": content.splitlines()[0][:300] if failed and content else "",
            })
    rows.sort(key=lambda row: row["at"])
    free = list(jobs)
    # A call that failed may share its prompt with the retry that started
    # the Job; the calls that did not fail pair first.
    for row in sorted(rows, key=lambda row: row["failed"]):
        job = next((job for job in free if job["caller"] == row["caller"]
                    and job["prompt"] and job["prompt"] == row["_prompt"]), None)
        if job:
            row["job_id"] = job["id"]
            free.remove(job)
    for caller in {job["caller"] for job in free}:
        left = [job for job in free if job["caller"] == caller]
        open_rows = [row for row in rows if row["caller"] == caller and not row["job_id"] and not row["failed"]]
        if len(left) == 1 and len(open_rows) == 1:
            open_rows[0]["job_id"] = left[0]["id"]
    for row in rows:
        del row["_prompt"]
    return rows
