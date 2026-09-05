"""Authenticated, versioned session permission settings."""
from __future__ import annotations

import json


async def handle_set_permission(ws, cmd: dict):
    """Read or atomically update the authenticated session permission mode."""
    from openprogram.agent.permissions import (
        PermissionUpdateError, permission_state, update_permission,
        reconcile_permission_waits,
    )
    from openprogram.agent.session_db import default_db
    from openprogram.webui.ws_actions.runtime import _trusted_runtime_actor
    from openprogram.webui import server as _s
    sid = cmd.get("session_id")
    request_id = cmd.get("request_id")
    actor = _trusted_runtime_actor(ws)
    data = {"session_id": sid, "request_id": request_id, "action": "set_permission"}
    try:
        if not actor or actor.get("interaction") != "interactive":
            raise PermissionUpdateError("permission_owner_required")
        if not isinstance(sid, str) or not sid:
            raise PermissionUpdateError("invalid_session")
        if actor.get("session_ids") is not None and sid not in actor["session_ids"]:
            raise PermissionUpdateError("permission_scope_denied")
        state = getattr(ws, "scope", {}).get("state", {})
        if state.get("session_id") and state["session_id"] != sid:
            raise PermissionUpdateError("permission_scope_denied")
        if actor.get("project_ids") is not None:
            from openprogram.store.project import project_store
            project = project_store.project_for_session(sid)
            if project is None or project.id not in actor["project_ids"]:
                raise PermissionUpdateError("permission_scope_denied")
        if default_db().get_session(sid) is None:
            raise PermissionUpdateError("session_not_found")
        if "mode" in cmd:
            snapshot = update_permission(sid, cmd["mode"], cmd.get("expected_version"), actor)
        else:
            snapshot = permission_state(sid)
        data.update(mode=snapshot["mode"], version=snapshot["version"])
    except PermissionUpdateError as exc:
        data["error"] = exc.code
        if exc.code == "permission_version_conflict":
            snapshot = permission_state(sid)
            data.update(mode=snapshot["mode"], version=snapshot["version"])
        await ws.send_text(json.dumps({"type": "permission_changed", "data": data}))
        return
    frame = json.dumps({"type": "permission_changed", "data": data})
    await ws.send_text(frame)
    if "mode" in cmd:
        _s._broadcast(frame)
        await reconcile_permission_waits(sid)


ACTIONS = {"set_permission": handle_set_permission}
