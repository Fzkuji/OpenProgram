from __future__ import annotations

import asyncio
import zipfile

import httpx

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, Response

from openprogram.office_assets import OFFICE_PATCH_SHA256
from openprogram.webui.office_assets import office_host_availability, office_install_rejection, serve_asset


class _ParentModuleResponse(Response):
    def __init__(self, pack, port):
        super().__init__()
        self.pack = pack
        self.port = port

    async def __call__(self, scope, receive, send):
        # Reuse the immutable inventory and open-file validation. The request
        # cannot select another pack entry or bypass the main API auth layer.
        await serve_asset({**scope, "path": "/npm/public-api.js"}, receive, send,
                          self.pack, self.port, "'none'")


def register(app) -> None:
    router = APIRouter()
    install_lock = asyncio.Lock()

    @router.get("/api/documents/office-host")
    async def office_host(request: Request):
        return JSONResponse(office_host_availability(request, request.app.state.office_assets))

    @router.post("/api/documents/office-install")
    async def office_install(request: Request):
        rejection = office_install_rejection(request)
        if rejection:
            return JSONResponse(rejection, status_code=403)
        async with install_lock:
            if not request.app.state.office_assets.available:
                from openprogram.office_install import download_office_pack
                try:
                    pack = await run_in_threadpool(download_office_pack)
                except (OSError, ValueError, RuntimeError, httpx.HTTPError, zipfile.BadZipFile):
                    return JSONResponse({"error": "Office installation failed. Please retry.", "code": "office_install_failed"}, status_code=503)
                request.app.state.office_assets = pack
        return JSONResponse({"installed": True})

    @router.get("/api/documents/office-module/{version}.js")
    async def office_module(request: Request, version: str):
        if version != OFFICE_PATCH_SHA256:
            return Response(status_code=404)
        return _ParentModuleResponse(request.app.state.office_assets, request.app.state.owner_auth.port)

    app.include_router(router)
