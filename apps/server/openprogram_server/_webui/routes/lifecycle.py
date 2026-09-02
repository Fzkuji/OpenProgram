"""Canonical execution command and snapshot endpoints.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only emits the resulting status envelope onto the
event bus (``ws.frame`` → server's WS forwarder).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from openprogram.events import emit_ws_frame


def _execution_payload(execution):
    from openprogram.execution import default_store
    from openprogram.execution.public import execution_snapshot

    resource = None
    try:
        from openprogram.agent.job import get_runner

        view = get_runner().get_job_resource_view(execution.execution_id)
        resource = view.to_dict() if view is not None else None
    except Exception:
        pass
    return execution_snapshot(
        execution, store=default_store(), resource=resource,
    ).to_dict()


def _actor_and_session(request: Request):
    from openprogram.webui.ws_actions.runtime import trusted_runtime_actor

    state = request.scope.get("state") if isinstance(request.scope, dict) else None
    bound_session = state.get("session_id") if isinstance(state, dict) else None
    actor = trusted_runtime_actor(request.scope)
    if actor is None:
        auth_state = getattr(request.app.state, "owner_auth", None)
        actor = getattr(auth_state, "authority", None)
    return actor, bound_session if isinstance(bound_session, str) else None

def register(app):
    async def _command(request: Request, body: dict, operation: str):
        payload = body or {}
        from openprogram.webui.ws_actions.runtime import (
            submit_execution_control,
        )

        actor, bound_session = _actor_and_session(request)
        command, execution = await submit_execution_control(
            payload,
            operation,
            actor=actor,
            bound_session=bound_session,
        )
        command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
        execution_data = (
            _execution_payload(execution)
            if hasattr(execution, "execution_id") else dict(execution)
        )
        cursor = {
            "execution_id": execution_data.get("execution_id"),
            "next_sequence": int(execution_data.get("event_sequence") or execution_data.get("status_version") or 0) + 1,
            "snapshot_status_version": execution_data.get("status_version"),
        }
        update = {
            "type": "execution.command.updated",
            "command": command_data,
            "execution": execution_data,
            "event_cursor": cursor,
        }
        emit_ws_frame({**update, "data": update})
        emit_ws_frame({"type": "execution.updated", "execution": execution_data, "data": {"execution": execution_data}})
        from openprogram.webui import server as _s
        if execution_data.get("status") in {"cancelling", "cancelled"}:
            _s._release_session_occupancy_for_execution(execution_data)
        code = 200
        if command_data.get("status") == "rejected":
            code = 404 if command_data.get("rejection_code") == "not_found" else 409
            if command_data.get("rejection_code") == "invalid_command":
                code = 400
        return JSONResponse(content={**update, "data": update}, status_code=code)

    for operation in ("pause", "continue", "step", "cancel"):
        async def endpoint(request: Request, body: dict = None, _operation=operation):
            return await _command(request, body or {}, _operation)

        endpoint.__name__ = f"api_execution_{operation}"
        app.post(f"/api/execution/{operation}")(endpoint)

    @app.get("/api/execution/{execution_id}")
    async def api_execution_snapshot(execution_id: str, request: Request):
        from openprogram.execution import default_store

        execution = default_store().get_execution(execution_id)
        _actor, bound_session = _actor_and_session(request)
        if execution is None or bound_session is not None and bound_session != execution.session_id:
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        snapshot = _execution_payload(execution)
        return JSONResponse({"type": "execution.snapshot", "snapshot": snapshot, "data": snapshot})

    @app.get("/api/execution/{execution_id}/events")
    async def api_execution_events(execution_id: str, request: Request, after_sequence: int = 0):
        from openprogram.execution import default_store

        execution = default_store().get_execution(execution_id)
        _actor, bound_session = _actor_and_session(request)
        if execution is None or bound_session is not None and bound_session != execution.session_id:
            return JSONResponse({"error": "not_found", "execution_id": execution_id}, status_code=404)
        events = [
            {"sequence": e.sequence, "execution_id": e.execution_id, "kind": e.kind,
             "payload": dict(e.payload), "execution_version": e.execution_version,
             "command_id": e.command_id}
            for e in default_store().list_events(execution_id)
            if e.sequence > after_sequence
        ]
        snapshot = _execution_payload(execution)
        return JSONResponse({
            "execution_id": execution_id,
            "events": events,
            "event_cursor": {
                "execution_id": execution_id,
                "next_sequence": int(snapshot.get("event_sequence") or 0) + 1,
                "snapshot_status_version": snapshot.get("status_version"),
            },
        })
