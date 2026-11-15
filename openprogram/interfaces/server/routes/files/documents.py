"""Owner-authenticated document bytes and project-owned manual file history."""
from __future__ import annotations

import asyncio
import hashlib
import re
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from openprogram.state.store.document_history import (
    DocumentHistory, DocumentHistoryError, MAX_BYTES, resolve_document,
)


def _error(exc: DocumentHistoryError) -> JSONResponse:
    status = {"CONFLICT": 409, "PAYLOAD_TOO_LARGE": 413, "NOT_FOUND": 404,
              "INVALID_REQUEST": 400, "HISTORY_CORRUPT": 503,
              "PROJECT_LOCATION_UNAVAILABLE": 409, "RECOVERY_REQUIRED": 503}.get(exc.code, 500)
    return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=status)


def _result(value: dict) -> JSONResponse:
    if value.get("ok"):
        return JSONResponse(value)
    return JSONResponse(value, status_code=409 if value.get("error_code") == "CONFLICT" else 503)


def _baseline(value) -> str:
    if not isinstance(value, str) or (value != "absent" and not re.fullmatch(r"[a-f0-9]{64}", value)):
        raise DocumentHistoryError("a valid baseline revision is required", "INVALID_REQUEST")
    return value


def _key(value) -> str:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise DocumentHistoryError("idempotency key is required", "INVALID_REQUEST")
    return value


async def _raw_body(request: Request, maximum: int = MAX_BYTES) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > maximum):
        raise DocumentHistoryError("request body exceeds size limit", "PAYLOAD_TOO_LARGE")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > maximum:
            raise DocumentHistoryError("request body exceeds size limit", "PAYLOAD_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)


def disk_version(target: Path) -> str:
    info = target.stat()
    return f"{info.st_dev}:{info.st_ino}:{info.st_size}:{info.st_mtime_ns}:{info.st_ctime_ns}"


def _current_content(project_id: str, path: str) -> Response:
    target, relative = resolve_document(project_id, path)
    before = disk_version(target)
    raw, mode = DocumentHistory._read_bounded(target)
    version = before if before == disk_version(target) else ""
    return Response(raw, media_type="application/octet-stream", headers={
        "X-Document-Path": quote(relative, safe="/"), "X-Document-Version": version,
        "X-Document-Revision": hashlib.sha256(raw).hexdigest(), "X-Document-Mode": str(mode),
        "Content-Disposition": "inline; filename*=UTF-8''" + quote(target.name, safe=""),
        "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
        "Cache-Control": "no-store",
    })


def register(app):
    router = APIRouter()

    @router.get("/api/documents/content")
    async def get_content(project_id: str, path: str):
        try:
            return await asyncio.to_thread(_current_content, project_id, path)
        except DocumentHistoryError as exc:
            return _error(exc)
        except FileNotFoundError:
            return _error(DocumentHistoryError("document not found", "NOT_FOUND"))

    @router.get("/api/documents/stat")
    async def get_stat(path: str, project_id: str = "", session_id: str = ""):
        def metadata():
            if project_id:
                target, _ = resolve_document(project_id, path)
            else:
                from openprogram.state import attachments
                target = attachments.resolve_session_attachment(
                    path, session_id or None, attachments.readable_roots(session_id or None),
                    allow_missing=True,
                )
                if target is None:
                    raise HTTPException(status_code=403, detail="path not allowed")
            if not target.is_file():
                raise HTTPException(status_code=404, detail="not a file")
            return {"version": disk_version(target)}
        try:
            return JSONResponse(await asyncio.to_thread(metadata), headers={"Cache-Control": "no-store"})
        except DocumentHistoryError as exc:
            return _error(exc)
        except OSError:
            raise HTTPException(status_code=404, detail="file metadata unavailable")

    @router.put("/api/documents/content")
    async def put_content(request: Request, project_id: str, path: str):
        try:
            baseline = _baseline(request.headers.get("x-baseline-revision"))
            key = _key(request.headers.get("idempotency-key"))
            raw = await _raw_body(request)
            result = await asyncio.to_thread(DocumentHistory().publish, project_id, path, raw,
                editor_id=request.headers.get("x-editor-id", "manual"),
                baseline_revision=baseline, idempotency_key=key,
                close=request.headers.get("x-history-close") == "true")
            return _result(result)
        except DocumentHistoryError as exc:
            return _error(exc)
        except (OSError, RuntimeError, ValueError):
            return _error(DocumentHistoryError("document publication could not be confirmed", "RECOVERY_REQUIRED"))

    @router.get("/api/documents/history")
    async def get_history(project_id: str, path: str, limit: int = 50, cursor: str = "0"):
        try:
            return JSONResponse(await asyncio.to_thread(DocumentHistory().list, project_id, path, limit=limit, cursor=cursor))
        except DocumentHistoryError as exc:
            return _error(exc)

    @router.get("/api/documents/history/content")
    async def get_history_content(project_id: str, path: str, version: str, side: str = "after"):
        try:
            raw = await asyncio.to_thread(DocumentHistory().content, project_id, path, version, side)
            return Response(raw, media_type="application/octet-stream", headers={
                "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
                "Cache-Control": "no-store", "X-Document-Revision": hashlib.sha256(raw).hexdigest()})
        except DocumentHistoryError as exc:
            return _error(exc)

    @router.post("/api/documents/history/restore")
    async def restore(request: Request):
        try:
            import json
            payload = json.loads(await _raw_body(request, 16 * 1024))
            if not isinstance(payload, dict):
                raise ValueError("object required")
            result = await asyncio.to_thread(DocumentHistory().restore,
                payload.get("project_id"), payload.get("path"), payload.get("version"),
                side=payload.get("side", "after"), baseline_revision=_baseline(payload.get("baseline_revision")),
                idempotency_key=_key(payload.get("idempotency_key")), editor_id=payload.get("editor_id", "manual"))
            return _result(result)
        except DocumentHistoryError as exc:
            return _error(exc)
        except (ValueError, TypeError, AttributeError):
            return _error(DocumentHistoryError("invalid restore request", "INVALID_REQUEST"))
        except (OSError, RuntimeError):
            return _error(DocumentHistoryError("document restore could not be confirmed", "RECOVERY_REQUIRED"))

    app.include_router(router)
