"""Settings > Versions: the kept versions, undo, direct edits and notices.

Undo and bringing direct edits back start a conversation in which the Agent
calls ``self_update``, so the update shows there as an ordinary tool call and
its progress is the tool's output.
"""
from __future__ import annotations

import logging
from typing import Any, Literal
import uuid

from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from openprogram.interfaces.server.routes.self_updates import require_owner
from openprogram.operations.self_update.control.projection import ProjectionAccessError
from openprogram.operations.self_update.store import SelfUpdateStore
from openprogram.operations.self_update.versions import layout as paths
from openprogram.operations.self_update.versions import overview
from openprogram.operations.self_update.versions.pointer import PointerError

_log = logging.getLogger(__name__)
_HEADERS = {"Cache-Control": "no-store"}
_NOT_MANAGED = {"managed": False, "versions": [], "unapplied": []}


class UndoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: str = Field(min_length=1, max_length=128)
    how: Literal["go_back", "undo_change"]


class ApplyEdits(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    stamp: str = Field(min_length=1, max_length=128)


class NoticeAck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    ids: list[str] = Field(max_length=50)


def _refusal(status: int, error: str) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status, headers=_HEADERS)


def _layout():
    runtime = paths.current_runtime()
    return None if runtime is None else runtime.layout


def _start_conversation(title: str, text: str, *, tools: list[str] | None) -> str:
    """Open a conversation and start the owner's turn in it."""
    from openprogram.runtime.agent_registry.manager import get_default
    from openprogram.runtime.context.session_db import default_db
    from openprogram.runtime.conversation.owner_turn import start_owner_turn
    from openprogram.runtime.conversation.session_model import ensure_session_chat_model

    spec = get_default()
    agent_id = spec.id if spec is not None else "main"
    session_id = f"versions_{uuid.uuid4().hex[:16]}"
    default_db().create_session(session_id, agent_id, title=title, source="web")
    try:
        ensure_session_chat_model(session_id, agent_id)
    except Exception:
        # The turn pins the chat model again at dispatch.
        _log.debug("chat model not pinned for versions session %s", session_id, exc_info=True)
    start_owner_turn(session_id, agent_id, text, tools=tools, name="versions-action",
                     reason_code="versions_action_failed")
    return session_id


def _label(layout, manifest: dict[str, Any]) -> str:
    return overview.version_label(layout, str(manifest["id"])) or str(manifest["id"])


def _undo_turn(layout, manifest: dict[str, Any], how: str) -> tuple[str, str]:
    label = _label(layout, manifest)
    if how == "go_back":
        target = str(manifest["id"])
        return (f"Go back to {label}",
                f"Go back to {label}. Call self_update with action \"undo\" and target \"{target}\" "
                "exactly once, then say in one short sentence what happens next.")
    target = str(manifest["commit"])
    return (f"Undo the change in {label}",
            f"Undo the change that made {label}, and keep everything after it. Call self_update with "
            f"action \"undo\" and target \"{target}\" exactly once, then say in one short sentence what "
            "happens next.")


def _apply_turn(entry: dict[str, Any]) -> tuple[str, str]:
    path = entry["path"]
    return ("Apply saved edits",
            f"Bring the direct edits saved in {path} into a new version. {path}/changes.patch has them "
            "with repository-relative paths and the changed files are under files/. Start a task with "
            "self_update action \"begin\", apply the patch in its working directory, run the tests for "
            "what changed, commit, and install it with self_update action \"prepare\". After the update "
            f"has succeeded, delete {path}.")


def register(app):
    @app.get("/api/versions")
    def api_versions(request: Request):
        try:
            require_owner(request)
        except ProjectionAccessError:
            return _refusal(403, "Only the owner can see versions.")
        layout = _layout()
        if layout is None:
            return JSONResponse(_NOT_MANAGED, headers=_HEADERS)
        try:
            return JSONResponse(overview.read(layout), headers=_HEADERS)
        except (PointerError, OSError, ValueError) as exc:
            _log.warning("cannot read the version store: %s", exc)
            return _refusal(503, "The list of versions could not be read.")

    @app.post("/api/versions/undo")
    def api_versions_undo(request: Request, body: UndoRequest):
        try:
            require_owner(request)
        except ProjectionAccessError:
            return _refusal(403, "Only the owner can change versions.")
        layout = _layout()
        if layout is None:
            return _refusal(409, "This install does not keep versions.")
        manifest = overview.find_version(layout, body.version)
        if manifest is None:
            return _refusal(404, "That version is no longer kept.")
        title, text = _undo_turn(layout, manifest, body.how)
        return JSONResponse({"session_id": _start_conversation(title, text, tools=["self_update"])},
                            headers=_HEADERS)

    @app.post("/api/versions/edits/apply")
    def api_versions_apply_edits(request: Request, body: ApplyEdits):
        try:
            require_owner(request)
        except ProjectionAccessError:
            return _refusal(403, "Only the owner can change versions.")
        layout = _layout()
        if layout is None:
            return _refusal(409, "This install does not keep versions.")
        entry = overview.find_unapplied(layout, body.stamp)
        if entry is None:
            return _refusal(404, "Those saved edits are gone.")
        title, text = _apply_turn(entry)
        return JSONResponse({"session_id": _start_conversation(title, text, tools=None)}, headers=_HEADERS)

    @app.get("/api/versions/notices")
    def api_version_notices(request: Request):
        try:
            require_owner(request)
        except ProjectionAccessError:
            return _refusal(403, "Only the owner can see notices.")
        layout = _layout()
        if layout is None:
            return JSONResponse({"notices": []}, headers=_HEADERS)
        return JSONResponse({"notices": overview.pending_notices(layout, SelfUpdateStore())}, headers=_HEADERS)

    @app.post("/api/versions/notices/ack")
    def api_version_notices_ack(request: Request, body: NoticeAck):
        try:
            require_owner(request)
        except ProjectionAccessError:
            return _refusal(403, "Only the owner can dismiss notices.")
        layout = _layout()
        if layout is not None:
            overview.acknowledge(layout, body.ids)
        return JSONResponse({"ok": True}, headers=_HEADERS)
