"""Remote access: whether other devices reach this computer, and through which addresses.

* ``GET /api/auth/remote-access`` — Same Wi-Fi and Tailscale: each one's
  address and whether it is on
* ``PUT /api/auth/remote-access`` — ``{"lan": bool}`` or ``{"tailscale": bool}``

The ways in themselves are ``openprogram.host.security.remote_access``.
Same Wi-Fi is a second listener on the local-network address
(``interfaces/server/lan_listener.py``); both apply at once, without a
restart. Every route here already runs as the owner.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from openprogram.host.security import remote_access as ways
from openprogram.host.security.backend_endpoint import canonicalize_origin, is_loopback_host

router = APIRouter(tags=["devices"])


class _Switch(BaseModel):
    lan: bool | None = None
    tailscale: bool | None = None


def _auth(request: Request):
    return request.app.state.owner_auth


def _web_config() -> tuple[str, list[str]]:
    from openprogram.state.config import read_config
    web = read_config().get("web") or {}
    origins = web.get("allowed_origins") if isinstance(web.get("allowed_origins"), list) else []
    return str(web.get("host") or "127.0.0.1"), [str(origin) for origin in origins]


def _canonical(origin: str) -> str:
    try:
        return canonicalize_origin(origin)
    except Exception:
        return ""


def _write_web(host: str, origins: list[str]) -> None:
    from openprogram.state.config import update_config

    def mutate(cfg: dict) -> None:
        web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
        web["host"] = host
        web["allowed_origins"] = origins
        cfg["web"] = web

    update_config(mutate)


async def _lan_state(request: Request) -> dict:
    from openprogram.interfaces.server import lan_listener
    auth = _auth(request)
    running = await lan_listener.apply(auth, request.app)
    address = running or await asyncio.to_thread(ways.lan_origin, auth.port)
    return {"on": bool(running), "address": address}


def _tailscale_state(request: Request) -> dict:
    auth = _auth(request)
    status = ways.tailscale_status()
    origin = status["origin"]
    on = bool(origin) and ways.tailscale_serving(auth.port) and _canonical(origin) in auth.effective_origins
    return {"installed": status["installed"], "running": status["running"], "on": on, "address": origin}


def routes_for_pairing(request: Request) -> list[str]:
    """The addresses another device can reach now, this network's first."""
    auth = _auth(request)
    found: list[str] = []
    from openprogram.interfaces.server import lan_listener
    lan = lan_listener.origin()
    if lan:
        found.append(_canonical(lan))
    status = ways.tailscale_status()
    if status["origin"] and _canonical(status["origin"]) in auth.effective_origins:
        found.append(_canonical(status["origin"]))
    for origin in sorted(auth.effective_origins):
        from urllib.parse import urlsplit
        host = urlsplit(origin).hostname or ""
        if origin not in found and host != "localhost" and not is_loopback_host(host):
            found.append(origin)
    return found


@router.get("/api/auth/remote-access")
async def remote_access(request: Request) -> dict:
    lan, tailscale = await asyncio.gather(_lan_state(request), asyncio.to_thread(_tailscale_state, request))
    return {"lan": lan, "tailscale": tailscale}


async def _switch_lan(request: Request, on: bool) -> None:
    from openprogram.interfaces.server import lan_listener
    auth = _auth(request)
    if on and not await asyncio.to_thread(ways.lan_origin, auth.port):
        raise HTTPException(status_code=409, detail="no_local_network")
    await asyncio.to_thread(lan_listener.remember, on)
    if not await lan_listener.apply(auth, request.app) and on:
        await asyncio.to_thread(lan_listener.remember, False)
        raise HTTPException(status_code=409, detail="lan_port_taken")


def _switch_tailscale(request: Request, on: bool) -> None:
    auth = _auth(request)
    status = ways.tailscale_status()
    if on and not status["running"]:
        raise HTTPException(status_code=409, detail="tailscale_not_running")
    problem = ways.set_tailscale_serve(auth.port, on)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    origin = status["origin"]
    if not origin:
        return
    host, origins = _web_config()
    kept = [item for item in origins if _canonical(item) != _canonical(origin)]
    _write_web(host, kept + [origin] if on else kept)
    if on:
        auth.allow_origins(added=[origin])
    else:
        auth.allow_origins(removed=[origin])


@router.put("/api/auth/remote-access")
async def switch_remote_access(body: _Switch, request: Request) -> dict:
    if body.lan is not None:
        await _switch_lan(request, body.lan)
    if body.tailscale is not None:
        await asyncio.to_thread(_switch_tailscale, request, body.tailscale)
    return await remote_access(request)
