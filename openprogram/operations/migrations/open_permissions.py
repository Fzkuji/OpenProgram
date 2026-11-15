"""One-time rewrite of a profile's stored permission data to the open defaults.

The worker calls :func:`run` before anything is served, and the server finishes
with :func:`resume_open_approvals` once executions have been recovered. The
marker is written last, so an interrupted run starts over, and every step leaves
already-changed data alone. After the marker exists nothing here runs again,
which keeps an owner's later choice.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from openprogram.state.paths import get_state_dir
from openprogram.host._platform.filesystem import replace_file

_log = logging.getLogger(__name__)

# Set when this process rewrote the stored data and still owes the open approvals.
_resume_owed = False


def _marker(state: Path) -> Path:
    return state / "migrations" / "open-permissions.done"


def run() -> bool:
    """Rewrite the stored data if this profile has not finished. Returns whether it did."""
    global _resume_owed
    state = get_state_dir()
    if _marker(state).exists():
        return False
    _release_agent_holds(state / "agents")
    _clear_old_default_from_conversations()
    _remove_old_default_from_projects()
    _resume_owed = True
    return True


async def resume_open_approvals() -> None:
    """Resume the approvals the new policy no longer asks about, then mark the profile done.

    A session that cannot be resumed keeps its open card for the owner to
    answer, and does not hold the marker back.
    """
    global _resume_owed
    if not _resume_owed:
        return
    from openprogram.runtime.execution import default_control_service
    from openprogram.runtime.execution.waits import DurableWaitStore
    from openprogram.runtime.permissions import reconcile_permission_waits

    service = default_control_service()
    sessions = set()
    for wait in DurableWaitStore(service.executions).list_open():
        execution = service.executions.get_execution(wait.execution_id) if wait.kind == "approval" else None
        if execution is not None:
            sessions.add(execution.session_id)
    for session_id in sorted(sessions):
        try:
            await reconcile_permission_waits(session_id, service=service, force=True)
        except Exception:  # noqa: BLE001
            _log.warning("open permissions: approvals in %s not resumed", session_id, exc_info=True)
    marker = _marker(get_state_dir())
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("done\n", encoding="utf-8")
    _resume_owed = False


def _release_agent_holds(agents: Path) -> None:
    """Every stored ``hold_outbound: true`` came from the old default.

    ``revision`` stays as it is: this is not an edit anyone made to the Agent.
    """
    if not agents.is_dir():
        return
    for path in sorted(agents.glob("*/agent.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            _log.warning("open permissions: skipped %s: %s", path, exc)
            continue
        if not isinstance(data, dict) or data.get("hold_outbound") is not True:
            continue
        data["hold_outbound"] = False
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        replace_file(temporary, path)


def _was_old_default(meta: dict) -> bool:
    """``auto`` on a conversation nobody has set a mode for is the old default;
    a conversation the owner changed has a ``permission_state``."""
    return meta.get("permission_mode") == "auto" and meta.get("permission_state") is None


def _clear_old_default_from_conversations() -> None:
    from openprogram.runtime.context.session_db import default_db

    db = default_db()
    for row in db.list_sessions(include_archived=True, limit=10**9):
        session_id = row.get("id")
        meta = db.read_disk_meta(session_id)
        if meta is not None and _was_old_default(meta):
            db.clear_session_field(session_id, "permission_mode", when=_was_old_default)


def _remove_old_default_from_projects() -> None:
    from openprogram.state.store.project.project_store import (
        list_projects, load_project_settings, save_project_settings,
    )

    for project in list_projects():
        settings = load_project_settings(project.id)
        if settings.get("permission_mode") == "auto":
            del settings["permission_mode"]
            save_project_settings(project.id, settings)
