"""Office document routes, served through the ``office_editor`` service.

While the ``office_editor`` application is disabled, the service is absent:
the host answers ``office_editor_disabled`` and installation and the parent
module are not served.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

#: The answer while the Office editor application is not loaded.
OFFICE_EDITOR_DISABLED = {"available": False, "reason": "office_editor_disabled"}


def office_editor():
    """The ``office_editor`` service, or ``None`` while the application is disabled."""
    from openprogram.programs._host import services as _services
    return _services.lookup("office_editor")


def register(app, office=office_editor) -> None:
    router = APIRouter()
    install_lock = asyncio.Lock()

    @router.get("/api/documents/office-host")
    async def office_host(request: Request):
        service = office()
        if service is None:
            return JSONResponse(OFFICE_EDITOR_DISABLED)
        await run_in_threadpool(service.load)
        return JSONResponse(service.availability(request))

    @router.post("/api/documents/office-install")
    async def office_install(request: Request):
        service = office()
        if service is None:
            return JSONResponse({"error": "The Office editor is turned off.", "code": "office_editor_disabled"},
                                status_code=404)
        rejection = service.install_rejection(request)
        if rejection:
            return JSONResponse(rejection, status_code=403)
        async with install_lock:
            try:
                await run_in_threadpool(service.install)
            except service.UNPUBLISHED_ERRORS as error:
                # Retrying cannot help: no release this version reads carries the files.
                return JSONResponse({"error": str(error), "code": "office_release_missing",
                                     "version": error.version, "updateTo": error.update_to}, status_code=503)
            except service.INSTALL_ERRORS:
                return JSONResponse({"error": "Office installation failed. Please retry.", "code": "office_install_failed"}, status_code=503)
        return JSONResponse({"installed": True})

    @router.get("/api/documents/office-module/{version}.js")
    async def office_module(request: Request, version: str):
        service = office()
        if service is None or version != service.module_version:
            return Response(status_code=404)
        return service.module_response(request.app.state.owner_auth.port)

    app.include_router(router)
