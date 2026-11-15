"""What a conversation keeps doing on its own, for its Activity panel.

One read gives the conversation's always-on state and reports, its open
work and the Scheduler tasks set from it (routines, reminders, webhooks).
The writes are the panel's own controls: wake now, add or remove a report
preference, and finish, reopen or drop an open-work item. One more read,
across conversations, lists the reports still waiting for the user (the
pending inbox).
"""
from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

_OPEN_WORK_STATUSES = ("open", "done")


def task_belongs_to_session(task: dict[str, Any], session_id: str) -> bool:
    execution = task.get("execution") or {}
    return session_id in (task.get("session_id"), execution.get("session_id"))


def routines(session_id: str) -> list[dict[str, Any]]:
    """Scheduler tasks set from this conversation, without their signed spec."""
    from openprogram.runtime.scheduler import hooks, service, task_runs

    rows = []
    for task in service.list_tasks():
        if not task_belongs_to_session(task, session_id):
            continue
        row = {key: value for key, value in task.items() if key != "execution"}
        if task.get("type") == "webhook":
            row["hook_url"] = hooks.url(str(task["id"]))
        row["runs"] = task_runs.recent(str(task.get("id")))
        rows.append(row)
    rows.sort(key=lambda row: row.get("created_at") or 0, reverse=True)
    return rows


def _mark_reports_in_chat(session_id: str, reports: list[dict[str, Any]]) -> None:
    """Say which reports reached the conversation: a restart can leave one
    saved but never posted, and Activity is then the only place that has it."""
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.always_on.store import report_message_id

    posted = default_db().read_index(session_id, lambda index: {
        str(row.get("id")) for row in reports if index.get(report_message_id(row)) is not None
    })
    if posted is None:
        return
    for row in reports:
        row["in_chat"] = str(row.get("id")) in posted


def snapshot(session_id: str) -> dict[str, Any] | None:
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.conversation import open_work

    try:
        state = always_on.status(session_id)
    except always_on.WakeRefused:
        return None
    _mark_reports_in_chat(session_id, state.get("reports") or [])
    return {
        "session_id": session_id,
        "always_on": state,
        "open_work": open_work.load(session_id),
        "routines": routines(session_id),
    }


def register(app) -> None:
    @app.get("/api/always-on/pending")
    def get_pending():
        from openprogram.runtime.conversation.always_on import pending

        return JSONResponse(content={"items": pending.items()})

    @app.get("/api/sessions/{session_id}/work")
    def get_work(session_id: str):
        body = snapshot(session_id)
        if body is None:
            return JSONResponse(content={"error": "session not found"}, status_code=404)
        return JSONResponse(content=body)

    @app.post("/api/sessions/{session_id}/always-on/wake")
    def wake(session_id: str):
        from openprogram.runtime.conversation import always_on

        try:
            result = always_on.wake(session_id, reason="manual")
        except always_on.WakeRefused as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=409)
        return JSONResponse(content=result)

    @app.post("/api/sessions/{session_id}/always-on/preferences")
    def add_preference(session_id: str, body: dict = None):
        from openprogram.runtime.conversation import always_on

        text = (body or {}).get("text")
        if not isinstance(text, str) or not text.strip():
            return JSONResponse(content={"error": "text is required"}, status_code=400)
        try:
            row = always_on.add_preference(session_id, text)
        except always_on.WakeRefused as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=409)
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=400)
        return JSONResponse(content=row)

    @app.delete("/api/sessions/{session_id}/always-on/preferences/{preference_id}")
    def remove_preference(session_id: str, preference_id: str):
        from openprogram.runtime.conversation import always_on

        try:
            removed = always_on.remove_preference(session_id, preference_id)
        except always_on.WakeRefused as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=409)
        if not removed:
            return JSONResponse(content={"error": "preference not found"}, status_code=404)
        return JSONResponse(content={"ok": True})

    @app.patch("/api/sessions/{session_id}/open-work/{item_id}")
    def update_open_work(session_id: str, item_id: str, body: dict = None):
        from openprogram.runtime.conversation import open_work

        status = (body or {}).get("status")
        if status not in _OPEN_WORK_STATUSES:
            return JSONResponse(content={"error": "status must be open or done"}, status_code=400)
        try:
            item = open_work.update(session_id, item_id, status=status)
        except KeyError:
            return JSONResponse(content={"error": "item not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=404)
        return JSONResponse(content=item)

    @app.delete("/api/sessions/{session_id}/open-work/{item_id}")
    def remove_open_work(session_id: str, item_id: str):
        from openprogram.runtime.conversation import open_work

        try:
            open_work.remove(session_id, item_id)
        except KeyError:
            return JSONResponse(content={"error": "item not found"}, status_code=404)
        except ValueError as exc:
            return JSONResponse(content={"error": str(exc)}, status_code=404)
        return JSONResponse(content={"ok": True})
