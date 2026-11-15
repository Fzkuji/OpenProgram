"""Owner REST API for scheduled tasks."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


def _public(task: dict[str, Any]) -> dict[str, Any]:
    """Keep frozen signatures and sandbox details out of the browser model."""
    return {key: value for key, value in task.items() if key != "execution"}


def _error(message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        content={"error": {"code": "INVALID_ARGUMENT", "message": message}},
        status_code=status,
    )


async def _body(request: Request) -> dict[str, Any] | None:
    try:
        value = await request.json()
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def register(app) -> None:
    from openprogram.runtime.scheduler import service

    router = APIRouter()

    @router.get("/api/scheduler/tasks")
    def list_tasks():
        return JSONResponse(content=[_public(row) for row in service.list_tasks()])

    @router.post("/api/scheduler/tasks")
    async def create_task(request: Request):
        payload = await _body(request)
        if payload is None:
            return _error("request body must be a JSON object")
        try:
            from openprogram.runtime.permissions.authority import local_owner_authority

            task = service.create_task(
                title=payload.get("title"),
                task_type=payload.get("type"),
                prompt=payload.get("prompt"),
                command=payload.get("command"),
                reminder=payload.get("reminder"),
                cron=payload.get("cron"),
                run_at=payload.get("run_at"),
                enabled=payload.get("enabled", True),
                memory_refs=payload.get("memory_refs"),
                notes=payload.get("notes") or "",
                cwd=payload.get("cwd"),
                authority=local_owner_authority(),
                watch=payload.get("watch"),
            )
        except ValueError as exc:
            return _error(str(exc))
        return JSONResponse(content=_public(task), status_code=201)

    @router.get("/api/scheduler/tasks/{task_id}")
    def get_task(task_id: str):
        task = service.get_task(task_id)
        if task is None:
            return _error("task not found", 404)
        return JSONResponse(content=_public(task))

    @router.patch("/api/scheduler/tasks/{task_id}")
    async def update_task(task_id: str, request: Request):
        payload = await _body(request)
        if payload is None:
            return _error("request body must be a JSON object")
        try:
            from openprogram.runtime.permissions.authority import local_owner_authority

            task = service.update_task(
                task_id, payload, authority=local_owner_authority()
            )
        except KeyError:
            return _error("task not found", 404)
        except ValueError as exc:
            return _error(str(exc))
        return JSONResponse(content=_public(task))

    @router.delete("/api/scheduler/tasks/{task_id}")
    def delete_task(task_id: str):
        if not service.delete_task(task_id):
            return _error("task not found", 404)
        return JSONResponse(content={"ok": True})

    @router.post("/hooks/{task_id}")
    async def call_hook(task_id: str, request: Request):
        """A webhook task's address. The owner login does not guard it
        (``owner_auth``); the task's key does, in ``hooks.receive``."""
        import asyncio
        from openprogram.runtime.scheduler import hooks

        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > hooks.BODY_MAX:
                return JSONResponse(content={"error": "body_too_large"}, status_code=413)
        headers = {key.lower(): value for key, value in request.headers.items()}
        status, content = await asyncio.to_thread(hooks.receive, task_id, headers, bytes(body))
        return JSONResponse(content=content, status_code=status)

    app.include_router(router)
