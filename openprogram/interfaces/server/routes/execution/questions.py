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
        from openprogram.runtime.execution import default_store
        from openprogram.runtime.execution.waits import DurableWaitStore, question_frame_data
        from openprogram.interfaces.server.routes.execution.lifecycle import _actor_and_session, _authorize_read

        actor, bound_session = _actor_and_session(request)
        if bound_session is not None:
            if session_id is not None and session_id != bound_session:
                return JSONResponse(content={"questions": []}, status_code=404)
            session_id = bound_session

        # A reconnect request without a trusted session binding must carry an
        # explicit session scope. Returning an empty list here would make the
        # unscoped endpoint an execution/wait enumerator.
        if session_id is None:
            scoped_sessions = actor.get("session_ids") if isinstance(actor, dict) else None
            if not isinstance(scoped_sessions, (list, tuple, set, frozenset)) or not scoped_sessions:
                return JSONResponse(content={"questions": []}, status_code=404)

        store = default_store()
        waits = [
            wait for wait in DurableWaitStore(store).list_open(session_id=session_id)
            if wait.kind != "system_access"
        ]
        if session_id is None:
            waits = [
                wait for wait in waits
                if (
                    (execution := store.get_execution(wait.execution_id)) is not None
                    and str(execution.session_id)
                    in {str(item) for item in scoped_sessions}
                )
            ]
        visible = []
        versions = {}
        for wait in waits:
            execution = store.get_execution(wait.execution_id)
            if execution is None or not _authorize_read(
                actor, session_id, execution, "execution.snapshot"
            ):
                continue
            visible.append(wait)
            versions[wait.execution_id] = (execution.session_id, execution.status_version)
        return JSONResponse(content={"questions": [
            question_frame_data(
                q, session_id=versions[q.execution_id][0],
                expected_version=versions[q.execution_id][1],
            )
            for q in visible
        ]})
