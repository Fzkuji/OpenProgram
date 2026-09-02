"""Pause / Resume / Stop endpoints for a running conversation turn.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only emits the resulting status envelope onto the
event bus (``ws.frame`` → server's WS forwarder).
"""
from __future__ import annotations

from fastapi.responses import JSONResponse

from openprogram.events import emit_ws_frame


def register(app):
    @app.post("/api/pause")
    async def api_pause():
        from openprogram.webui import server as _s
        _s.pause_execution()
        emit_ws_frame({"type": "status", "paused": True})
        return JSONResponse(content={"paused": True})

    @app.post("/api/resume")
    async def api_resume():
        from openprogram.webui import server as _s
        _s.resume_execution()
        emit_ws_frame({"type": "status", "paused": False})
        return JSONResponse(content={"paused": False})

    @app.post("/api/stop")
    async def api_stop(body: dict = None):
        """Compatibility stop: resolve the active execution and cancel it."""
        from openprogram.agent import run_control
        run_control.set_execution_update_hook(
            lambda execution: emit_ws_frame({
                "type": "execution.updated",
                "execution": execution,
            }),
        )

        payload = body or {}
        session_id = payload.get("session_id")
        execution_id = (payload.get("execution_id") or "").strip()
        if not execution_id:
            if not session_id:
                return JSONResponse(
                    content={"error": "missing execution_id"},
                    status_code=400,
                )
            execution_id = run_control.resolve_foreground_execution(
                session_id,
            ) or ""
        if not execution_id:
            return JSONResponse(
                content={"error": "no active execution"},
                status_code=404,
            )
        from openprogram.agent.production_driver import cancel_canonical_execution
        canonical = await cancel_canonical_execution(execution_id)
        if canonical is not None:
            execution = canonical.execution.to_dict()
            emit_ws_frame({"type": "execution.updated", "execution": execution})
            from openprogram.webui import server as _s
            _s._release_session_occupancy_for_execution(execution)
            return JSONResponse(content={"execution": execution})
        try:
            execution = run_control.cancel_execution(execution_id)
        except run_control.ExecutionNotFound:
            return JSONResponse(
                content={"error": "ExecutionNotFound"},
                status_code=404,
            )
        except run_control.ExecutionNotCancellable as exc:
            return JSONResponse(
                content={
                    "error": "ExecutionNotCancellable",
                    "execution": exc.execution,
                },
                status_code=409,
            )
        emit_ws_frame({"type": "execution.updated", "execution": execution})
        from openprogram.webui import server as _s
        _s._release_session_occupancy_for_execution(execution)
        return JSONResponse(content={"execution": execution})

    @app.post("/api/execution/cancel")
    async def api_execution_cancel(body: dict = None):
        """Cancel one execution inside the default worker process."""
        from openprogram.agent import run_control
        run_control.set_execution_update_hook(
            lambda execution: emit_ws_frame({
                "type": "execution.updated",
                "execution": execution,
            }),
        )

        payload = body or {}
        execution_id = (payload.get("execution_id") or "").strip()
        if not execution_id:
            return JSONResponse(
                content={"error": "missing execution_id"},
                status_code=400,
            )
        from openprogram.agent.production_driver import cancel_canonical_execution
        canonical = await cancel_canonical_execution(execution_id)
        if canonical is not None:
            execution = canonical.execution.to_dict()
            emit_ws_frame({"type": "execution.updated", "execution": execution})
            from openprogram.webui import server as _s
            _s._release_session_occupancy_for_execution(execution)
            return JSONResponse(content={"execution": execution})
        try:
            execution = run_control.cancel_execution(execution_id)
        except run_control.ExecutionNotFound:
            return JSONResponse(
                content={"error": "ExecutionNotFound"},
                status_code=404,
            )
        except run_control.ExecutionNotCancellable as exc:
            return JSONResponse(
                content={
                    "error": "ExecutionNotCancellable",
                    "execution": exc.execution,
                },
                status_code=409,
            )
        emit_ws_frame({"type": "execution.updated", "execution": execution})
        from openprogram.webui import server as _s
        _s._release_session_occupancy_for_execution(execution)
        return JSONResponse(content={"execution": execution})
