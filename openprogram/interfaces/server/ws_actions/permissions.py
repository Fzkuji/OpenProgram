"""Authenticated, versioned session permission settings."""
from __future__ import annotations

import asyncio
import json


async def handle_set_permission(ws, cmd: dict):
    """Read or atomically update the authenticated session permission mode."""
    from openprogram.runtime.permissions import (
        PermissionUpdateError, permission_state, update_permission,
        reconcile_permission_waits,
    )
    from openprogram.runtime.context.session_db import default_db
    from openprogram.interfaces.server.ws_actions.runtime import _trusted_runtime_actor
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.branch_settings import branch_target, current_branch
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    from types import SimpleNamespace
    # ``branch_id`` names the branch the pane shows, whatever is selected.
    target = branch_target(cmd)
    named = target.pop("branch_id")

    def current_snapshot(sid):
        row = default_db().get_session(sid) or {}
        branch = {"branch_id": named} if named else {}
        try:
            snapshot = permission_state(sid, db=SimpleNamespace(get_session=lambda _: row), **branch)
        except BranchConfigConflict as exc:
            # The pane names a branch with no record: answer, as a write does.
            raise PermissionUpdateError("branch_config_conflict") from exc
        return {"mode": snapshot["mode"], "version": snapshot["version"],
                "branch_id": named or row.get("active_branch_id"),
                "active_branch_id": row.get("active_branch_id")}

    sid = cmd.get("session_id")
    request_id = cmd.get("request_id")
    actor = _trusted_runtime_actor(ws)
    data = {"session_id": sid, "request_id": request_id, "action": "set_permission"}
    state = getattr(ws, "scope", {}).get("state", {})

    def apply() -> bool:
        """Check, write and read back the mode into ``data``; ``False`` when refused.
        Every step reads the conversation under its lock, so it runs off the loop."""
        try:
            if not actor or actor.get("interaction") != "interactive":
                raise PermissionUpdateError("permission_owner_required")
            if not isinstance(sid, str) or not sid:
                raise PermissionUpdateError("invalid_session")
            if actor.get("session_ids") is not None and sid not in actor["session_ids"]:
                raise PermissionUpdateError("permission_scope_denied")
            if state.get("session_id") and state["session_id"] != sid:
                raise PermissionUpdateError("permission_scope_denied")
            if actor.get("project_ids") is not None:
                from openprogram.state.store.project import project_store
                project = project_store.project_for_session(sid)
                if project is None or project.id not in actor["project_ids"]:
                    raise PermissionUpdateError("permission_scope_denied")
            if default_db().get_session(sid) is None:
                raise PermissionUpdateError("session_not_found")
            if "mode" in cmd:
                update_permission(sid, cmd["mode"], cmd.get("expected_version"), actor,
                                  branch_id=named, **target)
            data.update(current_snapshot(sid))
            return True
        except PermissionUpdateError as exc:
            data["error"] = exc.code
            data["active_branch_id"] = current_branch(sid)
            if exc.code == "branch_config_conflict":
                data["code"] = getattr(exc.__cause__, "code", None)
            if exc.code == "permission_version_conflict":
                try:
                    data.update(current_snapshot(sid))
                except PermissionUpdateError:
                    pass
            return False

    if not await asyncio.to_thread(apply):
        await ws.send_text(json.dumps({"type": "permission_changed", "data": data}))
        return
    frame = json.dumps({"type": "permission_changed", "data": data})
    await ws.send_text(frame)
    if "mode" in cmd:
        _s._broadcast(frame)
        await reconcile_permission_waits(sid)


ACTIONS = {"set_permission": handle_set_permission}
