"""Files dropped into the file tree from the operating system.

``POST /api/files/upload?project_id=…&path=…`` streams one file's bytes to
a project-relative path. The browser reads the dropped file, so no local
path crosses the wire and nothing outside the project is opened. Missing
parent folders are created (a dropped folder arrives as its files), and
an existing entry is never overwritten.
"""
from __future__ import annotations

import asyncio
import os
import tempfile

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


def _fail(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse({"ok": False, "code": code, "error_code": code, "error": message},
                        status_code=status)


def _prepare(project_id: str, path: str) -> tuple[str | None, JSONResponse | None]:
    from openprogram.interfaces.server.ws_actions.files.shared import _resolve, _resolve_entry

    if not path or path.endswith("/") or "\\" in path:
        return None, _fail(400, "INVALID_REQUEST", "path must name a file")
    root, error = _resolve(project_id, "")
    if root is None:
        code = "PROJECT_LOCATION_UNAVAILABLE" if error and "location" in error else "NOT_FOUND"
        return None, _fail(409 if code == "PROJECT_LOCATION_UNAVAILABLE" else 404, code, error or "")
    parent = os.path.dirname(path)
    if parent:
        folder, error = _resolve(project_id, parent)
        if folder is None:
            return None, _fail(403, "INVALID_REQUEST", error or "")
        if os.path.lexists(folder) and not os.path.isdir(folder):
            return None, _fail(409, "EXISTS", f"not a folder: {parent!r}")
        os.makedirs(folder, exist_ok=True)
    target, error = _resolve_entry(project_id, path)
    if target is None:
        return None, _fail(403, "INVALID_REQUEST", error or "")
    if os.path.lexists(target):
        return None, _fail(409, "EXISTS", f"already exists: {path!r}")
    return target, None


def _publish(project_id: str, path: str, staged: str, target: str) -> JSONResponse:
    from openprogram.interfaces.server.ws_actions.files.metadata import invalidate_cached_sizes
    from openprogram.interfaces.server.ws_actions.files.shared import _workspace_mutation_lock

    with _workspace_mutation_lock(project_id):
        try:
            # A hard link publishes atomically and refuses an existing name.
            os.link(staged, target)
        except FileExistsError:
            return _fail(409, "EXISTS", f"already exists: {path!r}")
        except OSError as exc:
            return _fail(500, "IO_ERROR", f"{type(exc).__name__}: {exc}")
        invalidate_cached_sizes(project_id, [path])
    return JSONResponse({"ok": True, "status": "ready", "project_id": project_id, "path": path})


def register(app):
    router = APIRouter()

    @router.post("/api/files/upload")
    async def upload(request: Request, project_id: str = "", path: str = ""):
        length = request.headers.get("content-length")
        if length and (not length.isdigit() or int(length) > MAX_UPLOAD_BYTES):
            return _fail(413, "PAYLOAD_TOO_LARGE", "file exceeds the upload limit")
        target, failure = await asyncio.to_thread(_prepare, project_id, path)
        if failure is not None:
            return failure
        fd, staged = tempfile.mkstemp(prefix=".upload-", dir=os.path.dirname(target))
        try:
            total = 0
            with os.fdopen(fd, "wb") as output:
                async for chunk in request.stream():
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        return _fail(413, "PAYLOAD_TOO_LARGE", "file exceeds the upload limit")
                    await asyncio.to_thread(output.write, chunk)
                output.flush()
                os.fsync(output.fileno())
            # mkstemp is owner-only; a dropped file reads like any other.
            os.chmod(staged, 0o644)
            return await asyncio.to_thread(_publish, project_id, path, staged, target)
        finally:
            try:
                os.unlink(staged)
            except OSError:
                pass

    app.include_router(router)

