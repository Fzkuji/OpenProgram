"""Remote access: whether other devices reach this computer, and through which addresses.

* ``GET /api/auth/remote-access`` — Same Wi-Fi and anywhere: each one's
  address and whether it is on; anywhere also says what its sidecar
  reports (``state``, and the ``login_url`` or ``enable_url`` the owner
  opens next)
* ``PUT /api/auth/remote-access`` — ``{"lan": bool}`` or ``{"anywhere": bool}``

The ways in themselves are ``openprogram.host.security.remote_access``.
Same Wi-Fi is a second listener on the local-network address
(``interfaces/server/lan_listener.py``); anywhere is the embedded Tailscale
sidecar, which writes ``web.anywhere`` and changes the Origins the running
server answers. Both apply at once, without a restart. Every route here
already runs as the owner.
"""
from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openprogram.host.security import remote_access as ways
from openprogram.host.security.backend_endpoint import canonicalize_origin, is_loopback_host

router = APIRouter(tags=["devices"])


class _Switch(BaseModel):
    lan: bool | None = None
    anywhere: bool | None = None


def _auth(request: Request):
    return request.app.state.owner_auth


def _canonical(origin: str) -> str:
    try:
        return canonicalize_origin(origin)
    except Exception:
        return ""


async def _lan_state(request: Request) -> dict:
    from openprogram.interfaces.server import lan_listener
    auth = _auth(request)
    running = await lan_listener.apply(auth, request.app)
    address = running or await asyncio.to_thread(ways.lan_origin, auth.port)
    return {"on": bool(running), "address": address}


def routes_for_pairing(request: Request) -> list[str]:
    """The addresses another device can reach now: this network's, then anywhere's."""
    auth = _auth(request)
    found: list[str] = []
    from openprogram.interfaces.server import lan_listener
    lan = lan_listener.origin()
    if lan:
        found.append(_canonical(lan))
    anywhere = ways.anywhere.address()
    anywhere = _canonical(anywhere) if anywhere else ""
    if anywhere and anywhere in auth.effective_origins and anywhere not in found:
        found.append(anywhere)
    for origin in sorted(auth.effective_origins):
        host = urlsplit(origin).hostname or ""
        if origin not in found and host != "localhost" and not is_loopback_host(host):
            found.append(origin)
    return found


@router.get("/api/auth/remote-access")
async def remote_access(request: Request) -> dict:
    lan, anywhere = await asyncio.gather(_lan_state(request), asyncio.to_thread(ways.anywhere.status))
    return {"lan": lan, "anywhere": anywhere}


async def _switch_lan(request: Request, on: bool) -> None:
    from openprogram.interfaces.server import lan_listener
    auth = _auth(request)
    if on and not await asyncio.to_thread(ways.lan_origin, auth.port):
        raise HTTPException(status_code=409, detail="no_local_network")
    await asyncio.to_thread(lan_listener.remember, on)
    if not await lan_listener.apply(auth, request.app) and on:
        await asyncio.to_thread(lan_listener.remember, False)
        raise HTTPException(status_code=409, detail="lan_port_taken")


def _switch_anywhere(request: Request, on: bool) -> None:
    auth = _auth(request)
    ways.set_anywhere_configured(on)
    if on:
        ways.anywhere.start(auth.port, auth.allow_origins)
    else:
        ways.anywhere.stop()


@router.put("/api/auth/remote-access")
async def switch_remote_access(body: _Switch, request: Request) -> dict:
    if body.lan is not None:
        await _switch_lan(request, body.lan)
    if body.anywhere is not None:
        await asyncio.to_thread(_switch_anywhere, request, body.anywhere)
    return await remote_access(request)
