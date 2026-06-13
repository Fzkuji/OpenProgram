"""Pending user-input questions — list / reply / reject over REST.

WS is the live path (question.asked frame → card → question_reply action).
These REST endpoints give API parity for the same registry so a non-WS
client (external integration, reconnecting tool) can enumerate and answer
pending questions. Both reply/reject funnel through the SAME collapse point
the WS handler uses (``_resolve_question``: registry.resolve + broadcast
question.replied/rejected to retract cards elsewhere), so first answer wins
across surfaces regardless of which transport carried it.

Design: docs/design/runtime/user-input-requests.md (opencode's list endpoint).
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def register(app):
    @app.get("/api/questions")
    async def api_list_questions(session_id: str | None = None):
        """Pending questions, optionally filtered by webui session. Lets a
        reconnecting client recover questions whose live frame it missed."""
        from openprogram.agent.questions import get_question_registry
        pend = get_question_registry().list_pending(session_id)
        return JSONResponse(content={"questions": [
            {
                "id": q.id, "session_id": q.session_id, "kind": q.kind,
                "prompt": q.prompt, "options": q.options, "multi": q.multi,
                "allow_custom": q.allow_custom, "detail": q.detail,
                "created_at": q.created_at, "expires_at": q.expires_at,
            }
            for q in pend
        ]})

    @app.post("/api/questions/{qid}/reply")
    async def api_reply_question(qid: str, body: dict = None):
        """Answer a pending question. ``{"answer": <str|list[str]>}``."""
        from openprogram.webui.ws_actions.session import _resolve_question
        answer = (body or {}).get("answer")
        _resolve_question(qid, "answered", answer)
        return JSONResponse(content={"ok": True})

    @app.post("/api/questions/{qid}/reject")
    async def api_reject_question(qid: str):
        """Decline a pending question (runtime.ask raises UserDeclined /
        confirm returns False)."""
        from openprogram.webui.ws_actions.session import _resolve_question
        _resolve_question(qid, "declined", None)
        return JSONResponse(content={"ok": True})
