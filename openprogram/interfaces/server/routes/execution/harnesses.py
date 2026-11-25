"""The outside agents (Codex) a conversation can run in, and a branch's choice of one.

``GET /api/harnesses`` lists them as this computer has them; a conversation
on another machine asks that machine through the ``list_harnesses``
WebSocket action, which is forwarded like any action naming its
conversation. ``POST /api/sessions/{id}/harness`` switches a branch's engine
(to the ``model`` the pane read from the conversation's machine, else this
computer's default) and ``POST /api/sessions/{id}/harness_agent`` its agent
choice; both write the branch the pane shows (``branch_id``), like the
other run settings.
"""
from __future__ import annotations

import asyncio
import contextlib

from fastapi.responses import JSONResponse

from openprogram.runtime.events import emit_ws_frame

#: OpenProgram's own engine; the stored setting is ``None`` for it.
OPENPROGRAM = "openprogram"
#: The settings a switch to a harness replaces and a switch back restores.
RETURN_FIELDS = ("provider_override", "model_override", "thinking_effort")


def _branch_config(session_id: str, body: dict) -> dict:
    from openprogram.interfaces.server.branch_settings import branch_target
    from openprogram.runtime.context.session_db import default_db
    from openprogram.state.store.session.branch_config import config_for_branch
    row = default_db().get_session(session_id) or {}
    named = branch_target(body)["branch_id"]
    return config_for_branch(row, named or row.get("active_branch_id"))


def harness_values(current: dict, harness: str, entry: dict | None) -> dict:
    """The branch settings a switch to ``harness`` writes.

    A switch to a harness takes its default model and effort and its
    default agent, keeping the branch's OpenProgram model and effort in
    ``harness_return``; a switch back to OpenProgram puts those back.
    """
    from openprogram.runtime.harnesses import provider_of
    was = current.get("harness") or OPENPROGRAM
    kept = current.get("harness_return") if was != OPENPROGRAM else {key: current.get(key) for key in RETURN_FIELDS}
    kept = kept if isinstance(kept, dict) else {}
    if harness == OPENPROGRAM:
        return {"harness": None, "harness_agent": None, "harness_return": None,
                **{key: kept.get(key) for key in RETURN_FIELDS}}
    models = (entry or {}).get("models") or []
    model = next((m for m in models if m.get("default")), models[0] if models else None)
    return {"harness": harness, "harness_agent": None, "harness_return": kept,
            "provider_override": provider_of(harness), "model_override": (model or {}).get("id"),
            "thinking_effort": None}


def _unavailable(entry: dict) -> str | None:
    if not entry.get("installed"):
        return "not_installed"
    if not entry.get("signedIn"):
        return "not_signed_in"
    if not entry.get("models"):
        return "no_models"
    return None


def switch_harness(session_id: str, body: dict) -> tuple[int, dict]:
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.branch_settings import write_branch_settings
    from openprogram.interfaces.server.routes.execution.runtime import picked_reply
    from openprogram.runtime.harnesses import HARNESS_IDS
    from openprogram.runtime.harnesses.models import read_harness
    from openprogram.state.store.session.branch_config import BranchConfigConflict

    harness = str(body.get("harness") or "").strip()
    if harness not in (OPENPROGRAM, *HARNESS_IDS.values()):
        return 400, {"error": "unknown_harness"}
    try:
        current = _branch_config(session_id, body)
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code}
    entry = None
    model = body.get("model")
    if harness != OPENPROGRAM and isinstance(model, str) and model.strip():
        # The pane read the harness on the conversation's machine and names its default model.
        entry = {"models": [{"id": model.strip(), "default": True}]}
    elif harness != OPENPROGRAM:
        entry = read_harness(harness)
        why = _unavailable(entry)
        if why:
            return 409, {"error": "harness_unavailable", "code": why}
    if (current.get("harness") or OPENPROGRAM) == harness:
        return 200, {"session_id": session_id, "unchanged": True}
    try:
        row, selected = write_branch_settings(session_id, body, harness_values(current, harness, entry))
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code}
    if selected:
        # The next turn builds the runtime of the model the branch now has.
        with _s._sessions_lock:
            conv = _s._sessions.get(session_id)
            old = conv.pop("runtime", None) if conv is not None else None
        if old is not None and hasattr(old, "close"):
            with contextlib.suppress(Exception):
                old.close()
    emit_ws_frame({"type": "agent_settings_changed", "data": {}})
    if selected:
        # The sidebar marks the conversations an outside agent runs.
        from openprogram.interfaces.server.ws_actions.session.listing import broadcast_sessions_list
        broadcast_sessions_list()
    return 200, {"session_id": session_id, **picked_reply(row)}


def choose_agent(session_id: str, body: dict) -> tuple[int, dict]:
    from openprogram.interfaces.server.branch_settings import write_branch_settings
    from openprogram.state.store.session.branch_config import BranchConfigConflict, snapshot
    agent = body.get("agent")
    if not isinstance(agent, str) or not agent.strip():
        return 400, {"error": "missing_agent"}
    agent = agent.strip()
    try:
        row, _selected = write_branch_settings(
            session_id, body, {"harness_agent": None if agent == "default" else agent})
    except BranchConfigConflict as exc:
        return 409, {"error": "branch_config_conflict", "code": exc.code}
    emit_ws_frame({"type": "agent_settings_changed", "data": {}})
    return 200, {"session_id": session_id, "branch_id": row.get("branch_id"),
                 "active_branch_id": row.get("active_branch_id"), "settings": snapshot(row)}


def register(app):
    @app.get("/api/harnesses")
    async def list_harnesses(refresh: bool = False):
        from openprogram.runtime.harnesses.models import list_harnesses as read
        return JSONResponse(content={"harnesses": await asyncio.to_thread(read, refresh=refresh)})

    @app.post("/api/harnesses/{harness}/update")
    async def update_harness(harness: str):
        from openprogram.runtime.harnesses.models import update_harness as run
        return JSONResponse(content=await asyncio.to_thread(run, harness))

    @app.post("/api/sessions/{session_id}/harness")
    async def set_session_harness(session_id: str, body: dict | None = None):
        status, payload = await asyncio.to_thread(switch_harness, session_id, body or {})
        return JSONResponse(content=payload, status_code=status)

    @app.post("/api/sessions/{session_id}/harness_agent")
    async def set_harness_agent(session_id: str, body: dict | None = None):
        status, payload = await asyncio.to_thread(choose_agent, session_id, body or {})
        return JSONResponse(content=payload, status_code=status)
