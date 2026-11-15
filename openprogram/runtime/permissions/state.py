"""Versioned owner session permission state and atomic updates.

SessionStore is the settings authority; canonical execution waits remain the
only approval authority. Admission identity and runtime contracts are immutable.
"""
from __future__ import annotations

from typing import Any

from openprogram.runtime.permissions.modes import VALID_PERMISSION, resolve_mode


class PermissionUpdateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def permission_state(session_id: str, *, db=None, branch_id: str | None = None) -> dict[str, Any]:
    if db is None:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
    row = db.get_session(session_id) or {}
    if branch_id:
        from openprogram.state.store.session.branch_config import config_for_branch
        row = config_for_branch(row, branch_id)
    return permission_state_from_row(session_id, row)


def permission_state_from_row(session_id: str, row: dict[str, Any]) -> dict[str, Any]:
    """Resolve permissions from one captured settings row."""
    state = (row.get("extra_meta") or {}).get("permission_state") or row.get("permission_state")
    if state is not None:
        if not isinstance(state, dict) or state.get("mode") not in VALID_PERMISSION or type(state.get("version")) is not int:
            raise PermissionUpdateError("invalid_permission_state")
        return dict(state)
    from openprogram.runtime.permissions.session_facts import facts
    return {"mode": resolve_mode(row.get("permission_mode"), facts().project_defaults(session_id).get("permission_mode")),
            "version": 0}


_BRANCH_UNSET = object()


def update_permission(session_id: str, mode: str, expected_version: int, actor: dict, *, db=None,
                      expected_branch_id=_BRANCH_UNSET, branch_id: str | None = None) -> dict:
    """Set one branch's permission mode: ``branch_id`` names it, whatever is
    selected; omitted is the selected branch (guarded by ``expected_branch_id``)."""
    from openprogram.state.authority import normalize_authority
    authority = normalize_authority(actor)
    if not authority or authority["authority_tier"] != "owner" or authority["interaction"] != "interactive":
        raise PermissionUpdateError("permission_owner_required")
    if not isinstance(mode, str) or mode not in VALID_PERMISSION or type(expected_version) is not int or expected_version < 0:
        raise PermissionUpdateError("invalid_permission_update")
    if db is None:
        from openprogram.runtime.context.session_db import default_db
        db = default_db()
    if db.get_session(session_id) is None:
        raise PermissionUpdateError("session_not_found")

    # A conversation with no stored state is in its inherited mode.
    from openprogram.state.store.session.branch_config import BranchConfigConflict
    branch_args = {"branch_id": branch_id} if branch_id else {}
    try:
        inherited = permission_state(session_id, db=db, **branch_args)["mode"] if mode == "plan" else None
    except BranchConfigConflict as exc:
        raise PermissionUpdateError("branch_config_conflict") from exc

    def update(current):
        if current.get("principal_id", authority["principal_id"]) != authority["principal_id"]:
            raise PermissionUpdateError("permission_owner_mismatch")
        if current.get("version", 0) != expected_version:
            raise PermissionUpdateError("permission_version_conflict")
        updated = {"mode": mode, "version": expected_version + 1, "principal_id": authority["principal_id"]}
        if mode == "plan":
            # exit_plan_mode returns to this.
            was = current.get("mode", inherited)
            before = current.get("before_plan") if was == "plan" else was
            if before in VALID_PERMISSION:
                updated["before_plan"] = before
        return updated

    guard = {} if expected_branch_id is _BRANCH_UNSET else {"expected_branch_id": expected_branch_id}
    try:
        result = db.update_session_dict(session_id, "permission_state", update, **guard, **branch_args)
    except BranchConfigConflict as exc:
        raise PermissionUpdateError("branch_config_conflict") from exc
    if result is None:
        raise PermissionUpdateError("permission_update_failed")
    return result
