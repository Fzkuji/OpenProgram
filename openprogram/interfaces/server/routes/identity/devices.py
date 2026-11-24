"""Paired devices: list, rename and revoke them, and mint pairing codes.

* ``GET /api/auth/devices`` — the paired devices, and which one is asking
* ``PATCH /api/auth/devices/{id}`` — rename one
* ``DELETE /api/auth/devices/{id}`` — revoke one; its sockets close
* ``POST /api/auth/pairings`` — a single-use code, the addresses another
  device can reach (this network's first) and the link for each: the first
  address with ``#pair=<code>`` and every other address as ``&via=``
  (``openprogram.host.machines.peer``)

The device credential itself is checked and issued by the owner-auth
middleware (``owner_auth.py``, ``device_access.py``); every route here
already runs as the owner.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

router = APIRouter(tags=["devices"])


class _PairingRequest(BaseModel):
    label: str | None = Field(default=None, max_length=64)


class _RenameRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)


def _auth_state(request: Request):
    return request.app.state.owner_auth


@router.get("/api/auth/devices")
def list_devices(request: Request) -> dict:
    current = (request.scope.get("state") or {}).get("device_id")
    return {"devices": _auth_state(request).devices.list(), "current": current}


@router.patch("/api/auth/devices/{device_id}")
def rename_device(device_id: str, body: _RenameRequest, request: Request) -> Response:
    if not _auth_state(request).devices.rename(device_id, body.label):
        raise HTTPException(status_code=404, detail="device_not_found")
    return Response(status_code=204)


@router.delete("/api/auth/devices/{device_id}")
def revoke_device(device_id: str, request: Request) -> Response:
    if not _auth_state(request).devices.revoke(device_id):
        raise HTTPException(status_code=404, detail="device_not_found")
    return Response(status_code=204)


@router.post("/api/auth/pairings")
def create_pairing(body: _PairingRequest, request: Request) -> dict:
    from openprogram.host.machines.peer import build_link
    from openprogram.interfaces.server.routes.identity.remote_access import routes_for_pairing

    code, expires_at = _auth_state(request).devices.create_pairing(body.label)
    routes = routes_for_pairing(request)
    return {
        "code": code,
        "expires_at": expires_at,
        "origins": routes,
        # One link per address, each naming the others: a phone opens the
        # one it can reach; another OpenProgram keeps them all.
        "links": {route: build_link([route, *[r for r in routes if r != route]], code) for route in routes},
    }
