from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from openprogram.webui.office_assets import office_host_availability


def register(app) -> None:
    router = APIRouter()

    @router.get("/api/documents/office-host")
    async def office_host(request: Request):
        return JSONResponse(office_host_availability(request, request.app.state.office_assets))

    app.include_router(router)
