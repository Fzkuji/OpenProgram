"""Execution cancellation endpoint for a running conversation turn.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only emits the resulting status envelope onto the
event bus (``ws.frame`` → server's WS forwarder).
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from openprogram.events import emit_ws_frame

def register(app):
    @app.post("/api/execution/cancel")
    async def api_execution_cancel(body: dict = None):
        """Cancel one execution inside the default worker process."""
        payload = body or {}
        execution_id = (payload.get("execution_id") or "").strip()
        if not execution_id:
            return JSONResponse(
                content={"error": "missing execution_id"},
                status_code=400,
            )
        from openprogram.agent.production_driver import cancel_canonical_execution
        canonical = await cancel_canonical_execution(execution_id)
        if canonical is None:
            return JSONResponse(content={"error": "ExecutionNotFound"}, status_code=404)
        execution = canonical.execution.to_dict()
        emit_ws_frame({"type": "execution.updated", "execution": execution})
        from openprogram.webui import server as _s
        _s._release_session_occupancy_for_execution(execution)
        return JSONResponse(content={"execution": execution})
