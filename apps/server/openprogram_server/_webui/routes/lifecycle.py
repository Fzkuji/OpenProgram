"""Execution cancellation endpoint for a running conversation turn.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only emits the resulting status envelope onto the
event bus (``ws.frame`` → server's WS forwarder).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from openprogram.events import emit_ws_frame

def register(app):
    @app.post("/api/execution/cancel")
    async def api_execution_cancel(request: Request, body: dict = None):
        """Cancel one execution inside the default worker process."""
        payload = body or {}
        from openprogram.webui.ws_actions.runtime import (
            submit_execution_control,
            trusted_runtime_actor,
        )

        state = request.scope.get("state") if isinstance(request.scope, dict) else None
        bound_session = state.get("session_id") if isinstance(state, dict) else None
        actor = trusted_runtime_actor(request.scope)
        if actor is None:
            auth_state = getattr(request.app.state, "owner_auth", None)
            actor = getattr(auth_state, "authority", None)
        command, execution = await submit_execution_control(
            payload,
            "cancel",
            actor=actor,
            bound_session=bound_session if isinstance(bound_session, str) else None,
        )
        command_data = command.to_dict() if hasattr(command, "to_dict") else dict(command)
        execution_data = execution.to_dict() if hasattr(execution, "to_dict") else dict(execution)
        emit_ws_frame({"type": "execution.command.updated", "command": command_data})
        emit_ws_frame({"type": "execution.updated", "execution": execution_data})
        from openprogram.webui import server as _s
        if execution_data.get("status") in {"cancelling", "cancelled"}:
            _s._release_session_occupancy_for_execution(execution_data)
        code = 200
        if command_data.get("status") == "rejected":
            code = 404 if command_data.get("rejection_code") == "not_found" else 409
            if command_data.get("rejection_code") == "invalid_command":
                code = 400
        return JSONResponse(content={"command": command_data, "execution": execution_data}, status_code=code)
