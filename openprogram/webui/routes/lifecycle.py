"""Pause / Resume / Stop endpoints for a running conversation turn.

Touches server module state heavily: cancel flags, follow-up queues,
running-tasks map. Each handler delegates the actual work to server-
module helpers and only re-broadcasts the resulting status envelope.
"""
from __future__ import annotations

import json

from fastapi.responses import JSONResponse


def register(app):
    @app.post("/api/pause")
    async def api_pause():
        from openprogram.webui import server as _s
        _s.pause_execution()
        _s._broadcast(json.dumps({"type": "status", "paused": True}))
        return JSONResponse(content={"paused": True})

    @app.post("/api/resume")
    async def api_resume():
        from openprogram.webui import server as _s
        _s.resume_execution()
        _s._broadcast(json.dumps({"type": "status", "paused": False}))
        return JSONResponse(content={"paused": False})

    @app.post("/api/stop")
    async def api_stop(body: dict = None):
        """Stop the currently running task for a conversation.

        Flow: mark cancel flag → resume (in case paused) → kill exec
        subprocess → unblock any pending ask_user queue → force-clear
        server-side run state → broadcast terminal envelopes.
        """
        from openprogram.webui import server as _s
        session_id = (body or {}).get("session_id")
        if not session_id:
            return JSONResponse(
                content={"stopped": False, "error": "missing session_id"},
                status_code=400,
            )
        _s._mark_cancelled(session_id)
        _s.resume_execution()
        _s._kill_active_runtime(session_id)
        with _s._follow_up_lock:
            q = _s._follow_up_queues.get(session_id)
        if q is not None:
            try:
                q.put_nowait({"_cancelled": True})
            except Exception:
                pass
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)
        try:
            _s._unregister_active_runtime(session_id)
        except Exception:
            pass
        try:
            _s._unregister_cancel_event(session_id)
        except Exception:
            pass
        _s._broadcast(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "cancelled",
                "session_id": session_id,
                "content": "Execution stopped by user.",
                "cancelled": True,
            },
        }))
        _s._broadcast(json.dumps({
            "type": "status",
            "paused": False,
            "stopped": True,
            "session_id": session_id,
        }))
        return JSONResponse(content={"stopped": True})
