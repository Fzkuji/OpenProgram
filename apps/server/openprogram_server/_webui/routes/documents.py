"""Owner-authenticated document bytes and project-owned manual file history."""
from __future__ import annotations

import asyncio
import hashlib
import re
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from openprogram.store.document_history import (
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


def _current_content(project_id: str, path: str) -> Response:
    target, relative = resolve_document(project_id, path)
    raw, mode = DocumentHistory._read_bounded(target)
    return Response(raw, media_type="application/octet-stream", headers={
        "X-Document-Path": quote(relative, safe="/"),
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
