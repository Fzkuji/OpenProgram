"""Versioned owner session permission state and atomic updates.

SessionStore is the settings authority; canonical execution waits remain the
only approval authority. Admission identity and runtime contracts are immutable.
"""
from __future__ import annotations

from typing import Any

from openprogram.agent.session_config import VALID_PERMISSION


class PermissionUpdateError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def permission_state(session_id: str, *, db=None) -> dict[str, Any]:
    if db is None:
        from openprogram.agent.session_db import default_db
        db = default_db()
    row = db.get_session(session_id) or {}
    state = (row.get("extra_meta") or {}).get("permission_state") or row.get("permission_state")
    if state is not None:
        if not isinstance(state, dict) or state.get("mode") not in VALID_PERMISSION or type(state.get("version")) is not int:
            raise PermissionUpdateError("invalid_permission_state")
        return dict(state)
    from openprogram.agent.session_config import project_defaults
    mode = row.get("permission_mode") or project_defaults(session_id).get("permission_mode") or "ask"
    return {"mode": mode if mode in VALID_PERMISSION else "ask", "version": 0}


def update_permission(session_id: str, mode: str, expected_version: int, actor: dict, *, db=None) -> dict:
    from openprogram.agent.authority import normalize_authority
    authority = normalize_authority(actor)
    if not authority or authority["authority_tier"] != "owner" or authority["interaction"] != "interactive":
        raise PermissionUpdateError("permission_owner_required")
    if not isinstance(mode, str) or mode not in VALID_PERMISSION or type(expected_version) is not int or expected_version < 0:
        raise PermissionUpdateError("invalid_permission_update")
    if db is None:
        from openprogram.agent.session_db import default_db
        db = default_db()
    if db.get_session(session_id) is None:
        raise PermissionUpdateError("session_not_found")

    def update(current):
        if current.get("principal_id", authority["principal_id"]) != authority["principal_id"]:
            raise PermissionUpdateError("permission_owner_mismatch")
        if current.get("version", 0) != expected_version:
            raise PermissionUpdateError("permission_version_conflict")
        return {"mode": mode, "version": expected_version + 1, "principal_id": authority["principal_id"]}

    result = db.update_session_dict(session_id, "permission_state", update)
    if result is None:
        raise PermissionUpdateError("permission_update_failed")
    return result
