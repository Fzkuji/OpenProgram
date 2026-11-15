"""Switch the Agent a conversation uses.

The composer's Agent menu calls this for a conversation that already
exists; a draft keeps its pick locally and sends it with the first
message. Only the branch's Agent changes: a model or effort the branch set
for the old Agent no longer applies, and from the next turn the branch
follows the new Agent's model (read live at each turn, never copied onto
the branch). The conversation joins always-on right away when the Agent
is always-on.
"""
from __future__ import annotations

import logging
import asyncio
import json
import uuid

from fastapi.responses import JSONResponse

from openprogram.runtime.events import emit_ws_frame

_log = logging.getLogger(__name__)


def _switch(session_id: str, agent_id: str, guard: dict | None = None) -> tuple[int, dict]:
    """Switch the Agent of one branch: ``branch_id`` in the body names it (the
    branch the pane shows), else the selected one. Another branch's switch
    leaves the selected branch's live runtime and always-on membership alone."""
    from openprogram.runtime.conversation import always_on
    from openprogram.runtime.agent_registry import manager
    from openprogram.runtime.context.session_db import default_db
    from openprogram.interfaces.server import server as _s

    from openprogram.interfaces.server.branch_settings import check_branch, write_branch_settings
    from openprogram.state.store.session.branch_config import BranchConfigConflict, config_for_branch
    body = guard or {}
    named = body.get("branch_id") if isinstance(body.get("branch_id"), str) and body.get("branch_id") else None
    try:
        if named is None:
            check_branch(session_id, body)
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code, "message": str(exc)}
    spec = manager.get(agent_id)
    if spec is None:
        return 404, {"error": "agent not found"}
    db = default_db()
    if not db.is_created(session_id):
        return 404, {"error": "session not found"}
    session = db.get_session(session_id) or {}
    if session.get("agent_trial") and session.get("agent_id") != spec.id:
        return 409, {"error": "trial_agent_immutable"}
    try:
        current = config_for_branch(session, named or session.get("active_branch_id"))
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code, "message": str(exc)}
    model = {"provider": spec.model.provider or "", "id": spec.model.id or ""}
    if current.get("agent_id") == spec.id:
        return 200, {"session_id": session_id, "agent_id": spec.id, "model": model,
                     "branch_id": named or session.get("active_branch_id"),
                     "active_branch_id": session.get("active_branch_id")}

    # Only this branch is held: a turn on another branch keeps running.
    target = named or session.get("active_branch_id")
    refusal, owners = _s.reserve_branches_operation(
        session_id, [target] if target else [], "branch", f"agent-{uuid.uuid4().hex}")
    if refusal is not None:
        return 409, {"error": refusal.get("error") or "A turn is running on this branch.",
                     "code": refusal.get("code"), "blocking": refusal.get("blocking")}
    # The old Agent's model choice ends with it (the branch now follows the
    # new Agent's model); nothing of the new Agent is copied onto the branch.
    fields = {"agent_id": spec.id, "provider_override": None, "model_override": None}
    try:
        row, selected = write_branch_settings(session_id, body, fields)
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code, "message": str(exc)}
    finally:
        _s.release_branches_operation(session_id, owners)
    if selected:
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            old = conv.pop("runtime", None) if conv is not None else None
        if old is not None and hasattr(old, "close"):
            try:
                old.close()
            except Exception:
                _log.debug("closing runtime of session %s failed", session_id, exc_info=True)
        always_on.join(session_id)
        _s._broadcast(json.dumps({
            "type": "session_updated",
            "data": {"id": session_id, "agent_id": row.get("agent_id"), "active_branch_id": row.get("active_branch_id")},
        }))
        if model["provider"] and model["id"]:
            emit_ws_frame({"type": "provider_changed", "data": _s._get_provider_info(session_id)})
    return 200, {"session_id": session_id, "agent_id": row.get("agent_id"),
                 # The model the branch follows now: its new Agent's.
                 "model": model,
                 "branch_id": row.get("branch_id"),
                 "active_branch_id": row.get("active_branch_id")}



def register(app) -> None:
    @app.post("/api/sessions/{session_id}/agent")
    async def set_session_agent(session_id: str, body: dict | None = None):
        agent_id = str((body or {}).get("agent_id") or "").strip()
        if not agent_id:
            return JSONResponse(content={"error": "Missing agent_id"}, status_code=400)
        status, payload = await asyncio.to_thread(_switch, session_id, agent_id, body)
        return JSONResponse(content=payload, status_code=status)

    @app.post("/api/sessions/{session_id}/branch_settings")
    async def set_branch_settings(session_id: str, body: dict | None = None):
        """Write settings onto one branch. ``branch_id`` names it (the branch
        the pane shows), whatever is selected; an old client's
        ``expected_branch_id`` still guards the selected one.

        A value sets that setting on the branch. ``reset_fields`` lists the
        settings put back to inherit, as the Agent API's does; a JSON
        ``null`` value is refused, so clearing has one spelling."""
        from openprogram.runtime.context.session_db import default_db
        from openprogram.runtime.conversation.session_config import VALID_SERVICE_TIER, VALID_THINKING
        from openprogram.interfaces.server.branch_settings import write_branch_settings
        from openprogram.state.store.session.branch_config import BranchConfigConflict, snapshot
        body = body or {}
        keys = ("thinking_effort", "web_search", "service_tier", "tools_override", "sandbox_enabled")
        fields = {key: body[key] for key in keys if key in body}
        reset = body.get("reset_fields", [])
        if (any(value is None for value in fields.values())
                or not isinstance(reset, list) or not all(isinstance(key, str) and key in keys for key in reset)
                or set(reset) & set(fields)):
            return JSONResponse(content={"error": "invalid_branch_settings"}, status_code=400)
        if ("thinking_effort" in fields and (not isinstance(fields["thinking_effort"], str) or fields["thinking_effort"] not in VALID_THINKING)
                or "web_search" in fields and type(fields["web_search"]) is not bool
                or "service_tier" in fields and (not isinstance(fields["service_tier"], str) or fields["service_tier"] not in VALID_SERVICE_TIER)
                or "sandbox_enabled" in fields and type(fields["sandbox_enabled"]) is not bool
                or "tools_override" in fields and (not isinstance(fields["tools_override"], list) or not all(isinstance(v, str) for v in fields["tools_override"]))):
            return JSONResponse(content={"error": "invalid_branch_settings"}, status_code=400)
        fields.update({key: None for key in reset})
        def save():
            db = default_db()
            if not db.is_created(session_id):
                return 404, {"error": "session_not_found"}
            if "web_search" in fields and fields["web_search"] is not None:
                # A switch turned to what the branch would have anyway (its
                # Agent's choice) stores nothing: the branch keeps following
                # its Agent, so a later change to the Agent still reaches it.
                try:
                    inherited = _resolved(session_id, body).inherited
                except BranchConfigConflict:
                    inherited = {}
                if inherited.get("web_search") is fields["web_search"]:
                    fields["web_search"] = None
            try:
                row, _selected = write_branch_settings(session_id, body, fields)
            except BranchConfigConflict as exc:
                return 409, {"error": "branch_config_conflict", "code": exc.code,
                             "active_branch_id": (db.get_session(session_id) or {}).get("active_branch_id")}
            # Every pane that shows this conversation rereads its own branch.
            emit_ws_frame({"type": "agent_settings_changed", "data": {}})
            return 200, {"session_id": session_id, "branch_id": row.get("branch_id"),
                         "active_branch_id": row.get("active_branch_id"), "settings": snapshot(row),
                         **_resolved_reply(session_id, row.get("branch_id"))}
        status, payload = await asyncio.to_thread(save)
        return JSONResponse(content=payload, status_code=status)

    @app.get("/api/sessions/{session_id}/branch_settings")
    async def get_branch_settings(session_id: str, branch_id: str | None = None):
        """One branch's settings as they resolve now: ``values`` (what a
        turn would use), ``sources`` (``branch``, ``agent``, ``project`` or
        ``global``) and ``inherited`` (what each setting is when the branch
        does not set it: the "Agent default" a menu shows)."""
        from openprogram.runtime.context.session_db import default_db
        from openprogram.state.store.session.branch_config import BranchConfigConflict

        def read():
            if not default_db().is_created(session_id):
                return 404, {"error": "session_not_found"}
            try:
                return 200, {"session_id": session_id, **_resolved_reply(session_id, branch_id, strict=True)}
            except BranchConfigConflict as exc:
                return 409, {"error": "branch_config_conflict", "code": exc.code}
        status, payload = await asyncio.to_thread(read)
        return JSONResponse(content=payload, status_code=status)


def _resolved(session_id: str, body: dict):
    from openprogram.runtime.conversation.session_config import resolve_branch_settings
    named = body.get("branch_id") if isinstance(body.get("branch_id"), str) and body.get("branch_id") else None
    return resolve_branch_settings(session_id, named)


def _resolved_reply(session_id: str, branch_id: str | None, *, strict: bool = False) -> dict:
    from openprogram.interfaces.server.branch_settings import resolved_settings
    return resolved_settings(session_id, branch_id, strict=strict)
