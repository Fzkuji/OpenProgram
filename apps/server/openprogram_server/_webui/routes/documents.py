"""Authenticated project scoped manual document publication and history."""
from __future__ import annotations

import asyncio
from urllib.parse import quote
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from openprogram.store.document_history import (
    DocumentHistory, DocumentHistoryError, MAX_BYTES,
)


def _error(exc: DocumentHistoryError) -> JSONResponse:
    status = {"CONFLICT": 409, "PAYLOAD_TOO_LARGE": 413,
              "NOT_FOUND": 404, "INVALID_REQUEST": 400,
              "HISTORY_CORRUPT": 503}.get(exc.code, 500)
    return JSONResponse({"error": exc.code, "message": str(exc)}, status_code=status)


async def _raw_body(request: Request) -> bytes:
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BYTES):
        raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
    chunks, total = [], 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_BYTES:
            raise DocumentHistoryError("content exceeds 64 MiB", "PAYLOAD_TOO_LARGE")
        chunks.append(chunk)
    return b"".join(chunks)


def register(app):
    router = APIRouter()

    @router.get("/api/documents/content")
    async def get_content(project_id: str, path: str):
        try:
            target, relative = __import__("openprogram.store.document_history", fromlist=["resolve_document"]).resolve_document(project_id, path)
            raw, mode = await asyncio.to_thread(DocumentHistory._read_bounded, target)
            import hashlib
            return Response(raw, media_type="application/octet-stream", headers={
                "X-Document-Path": quote(relative, safe="/"), "X-Document-Revision": hashlib.sha256(raw).hexdigest(),
                "X-Document-Mode": str(mode), "Content-Disposition": f"inline; filename*=UTF-8''{quote(target.name)}",
            })
        except DocumentHistoryError as exc: return _error(exc)

    @router.put("/api/documents/content")
    async def put_content(request: Request, project_id: str, path: str):
        try:
            raw = await _raw_body(request)
            result = await asyncio.to_thread(DocumentHistory().publish, project_id, path, raw,
                editor_id=request.headers.get("x-editor-id", "manual"),
                baseline_revision=request.headers.get("x-baseline-revision"),
                idempotency_key=request.headers.get("idempotency-key"),
                close=request.headers.get("x-history-close") == "true")
            return JSONResponse(result)
        except DocumentHistoryError as exc: return _error(exc)

    @router.get("/api/documents/history")
    async def get_history(project_id: str, path: str, limit: int = 50, cursor: int = 0):
        try: return JSONResponse(await asyncio.to_thread(DocumentHistory().list, project_id, path, limit=limit, cursor=cursor))
        except DocumentHistoryError as exc: return _error(exc)

    @router.get("/api/documents/history/content")
    async def get_history_content(project_id: str, path: str, version: str, side: str = "after"):
        try: return Response(await asyncio.to_thread(DocumentHistory().content, project_id, path, version, side), media_type="application/octet-stream")
        except DocumentHistoryError as exc: return _error(exc)

    @router.post("/api/documents/history/restore")
    async def restore(request: Request):
        try:
            payload = await request.json()
            result = await asyncio.to_thread(DocumentHistory().restore, payload.get("project_id"), payload.get("path"), payload.get("version"),
                side=payload.get("side", "after"), baseline_revision=payload.get("baseline_revision", ""),
                idempotency_key=payload.get("idempotency_key", ""), editor_id=payload.get("editor_id", "manual"))
            return JSONResponse(result)
        except (ValueError, TypeError, AttributeError): return JSONResponse({"error": "INVALID_REQUEST"}, status_code=400)
        except DocumentHistoryError as exc: return _error(exc)

    app.include_router(router)
