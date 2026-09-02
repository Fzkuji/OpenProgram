"""Pending user-input questions — list / reply / reject over REST.

WS is the live notification path; this endpoint is the reconnect read path.
Answers and declines are submitted only through the canonical
``execution.wait.answer`` / ``execution.wait.decline`` command endpoints.

Design: docs/design/runtime/user-input-requests.md (opencode's list endpoint).
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/questions")
    async def api_list_questions(request: Request, session_id: str | None = None):
        """Pending questions, optionally filtered by webui session. Lets a
        reconnecting client recover questions whose live frame it missed."""
        from openprogram.execution import default_store
        from openprogram.execution.waits import DurableWaitStore
        state = request.scope.get("state") if isinstance(request.scope, dict) else None
        bound_session = state.get("session_id") if isinstance(state, dict) else None
        if isinstance(bound_session, str) and bound_session:
            if session_id is not None and session_id != bound_session:
                return JSONResponse(content={"questions": []}, status_code=404)
            session_id = bound_session
        pend = DurableWaitStore(default_store()).list_open(session_id=session_id)
        return JSONResponse(content={"questions": [
            {
                "id": q.wait_id, "execution_id": q.execution_id,
                "wait_generation": q.claim_generation, "kind": q.kind,
                "prompt": q.request.get("prompt", ""), "options": q.request.get("options", []),
                "multi": q.request.get("multi", False),
                "allow_custom": q.request.get("allow_custom", True),
                "detail": q.request.get("detail", ""),
                # kind="form" carries its field schema; kind="ask_many" its
                # questions list — both must survive a reconnect / REST
                # recovery, not just the live WS frame. (approval's danger
                # summary rides in `detail`, already above.)
                "schema": q.request.get("schema", {}),
                "questions": q.request.get("questions", []),
                "tool": q.request.get("tool"),
                "args": q.request.get("args"),
                "risk_level": q.request.get("risk_level"),
                "created_at": q.created_at, "expires_at": q.expires_at,
            }
            for q in pend
        ]})
