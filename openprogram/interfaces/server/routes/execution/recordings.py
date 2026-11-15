"""Recording routes — a demonstration recorded on the execution computer.

Endpoints:
  POST /api/recordings                       {purpose} → {id, started_at, max_seconds}
  GET  /api/recordings/{id}                  state, elapsed time, step and image counts
  POST /api/recordings/{id}/stop             the summary shown in the composer
  POST /api/recordings/{id}/discard          stops and deletes
  POST /api/recordings/{id}/narration        {t, text} → appends a ``say`` event
  GET  /api/recordings/{id}/steps            the step lines and their screenshots, for the card
  GET  /api/recordings/{id}/images/{name}    one screenshot

A recording watches the local screen and input, so only the local owner
on the execution computer can start or read one. The recorder is the
built-in application ``workflow_recording``, reached through the service
it provides; while the application is disabled every route answers 404.
"""
from __future__ import annotations

import asyncio
import re

from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse

_IMAGE = re.compile(r"^\d{4}\.jpg$")


def _recorder():
    from openprogram.programs._host import services as _services
    return _services.lookup("workflow_recording")


#: The answer while the workflow recording application is not loaded.
NOT_ENABLED = {"error": "Workflow recording is not enabled.", "reason_code": "workflow_recording_not_enabled"}


def _local_owner(request: Request) -> JSONResponse | None:
    from urllib.parse import urlsplit
    from openprogram.host.security.backend_endpoint import is_loopback_host
    from openprogram.operations.self_update.control.projection import ProjectionAccessError
    from ..self_updates import require_owner
    try:
        require_owner(request)
        origin = request.headers.get("origin", "")
        forwarded = any(key.lower() == "forwarded" or key.lower().startswith("x-forwarded-")
                        for key in request.headers)
        if (not request.client or not is_loopback_host(request.client.host)
                or not is_loopback_host(request.url.hostname or "") or forwarded
                or (origin and not is_loopback_host(urlsplit(origin).hostname or ""))):
            raise ProjectionAccessError("Record on the execution computer.")
    except ProjectionAccessError:
        return JSONResponse({"error": "Recording is only available to the local owner on this computer."},
                            status_code=403)
    return None


def register(app):
    def missing(_id: str) -> JSONResponse:
        return JSONResponse({"error": "Unknown recording."}, status_code=404)

    @app.post("/api/recordings")
    async def api_recording_start(request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        try:
            body = await request.json()
        except ValueError:
            body = {}
        purpose = str((body or {}).get("purpose") or "workflow")
        try:
            started = await asyncio.to_thread(recording.start, purpose)
        except recording.RecordingError as exc:
            return JSONResponse({"error": str(exc), "capability": exc.capability}, status_code=409)
        return JSONResponse({"id": started.id, "started_at": started.started,
                             "max_seconds": recording.MAX_SECONDS})

    @app.get("/api/recordings/{recording_id}")
    async def api_recording_status(recording_id: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        live = recording.get(recording_id)
        if live is not None:
            return JSONResponse(live.status(), headers={"Cache-Control": "no-store"})
        try:
            directory = recording.directory_of(recording_id)
        except recording.RecordingError:
            return missing(recording_id)
        return JSONResponse({**recording.summary(directory), "state": "finished"},
                            headers={"Cache-Control": "no-store"})

    @app.post("/api/recordings/{recording_id}/stop")
    async def api_recording_stop(recording_id: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        live = recording.get(recording_id)
        if live is not None:
            await asyncio.to_thread(live.finish, "done")
        try:
            directory = recording.directory_of(recording_id)
        except recording.RecordingError:
            return missing(recording_id)
        return JSONResponse(recording.summary(directory))

    @app.post("/api/recordings/{recording_id}/discard")
    async def api_recording_discard(recording_id: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        live = recording.get(recording_id)
        if live is None:
            return missing(recording_id)
        await asyncio.to_thread(live.discard)
        return JSONResponse({"ok": True})

    @app.post("/api/recordings/{recording_id}/narration")
    async def api_recording_narration(recording_id: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        live = recording.get(recording_id)
        if live is None:
            return missing(recording_id)
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "Expected JSON."}, status_code=400)
        t = body.get("t")
        live.add_narration(int(t) if isinstance(t, (int, float)) else None, str(body.get("text") or ""))
        return JSONResponse({"ok": True})

    @app.get("/api/recordings/{recording_id}/steps")
    async def api_recording_steps(recording_id: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        try:
            directory = recording.directory_of(recording_id)
        except recording.RecordingError:
            return missing(recording_id)
        return JSONResponse({**recording.summary(directory), "rows": recording.step_rows(directory)},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/recordings/{recording_id}/images/{name}")
    async def api_recording_image(recording_id: str, name: str, request: Request):
        if (recording := _recorder()) is None:
            return JSONResponse(NOT_ENABLED, status_code=404)
        if (refused := _local_owner(request)) is not None:
            return refused
        if not _IMAGE.match(name):
            return missing(recording_id)
        try:
            directory = recording.directory_of(recording_id)
        except recording.RecordingError:
            return missing(recording_id)
        path = directory / "images" / name
        if not path.is_file():
            return missing(recording_id)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})
