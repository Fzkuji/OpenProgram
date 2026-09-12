from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from openprogram.office_assets import OFFICE_PATCH_SHA256
from openprogram.webui.office_assets import office_host_availability, serve_asset


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

    @router.get("/api/documents/office-host")
    async def office_host(request: Request):
        return JSONResponse(office_host_availability(request, request.app.state.office_assets))

    @router.get("/api/documents/office-module/{version}.js")
    async def office_module(request: Request, version: str):
        if version != OFFICE_PATCH_SHA256:
            return Response(status_code=404)
        return _ParentModuleResponse(request.app.state.office_assets, request.app.state.owner_auth.port)

    app.include_router(router)
