"""Same Wi-Fi: a second listener on this computer's local-network address.

The Worker always listens on loopback. Switching Same Wi-Fi on starts one
more listener, in the same process and on the same app, bound to the
address :func:`openprogram.host.security.remote_access.lan_address` finds,
and lets the running server answer that address's Origin
(``OwnerAuthState.allow_origins``). Switching it off stops that listener
and takes the Origin back. Nothing restarts, and the bind address in
``web.host`` is never touched: the choice is ``web.lan`` in the config,
and the Worker's start-up applies it again.

Being able to reach the address is not enough to sign in: a device pairs
first (``routes/identity/devices.py``).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

_log = logging.getLogger(__name__)

_app: Any = None
_server: Any = None
_task: Optional[asyncio.Task] = None
_origin: str = ""
_lock = asyncio.Lock()


def configured() -> bool:
    """Whether Same Wi-Fi is switched on in the config."""
    from openprogram.state.config import read_config
    web = read_config().get("web") or {}
    return bool(isinstance(web, dict) and web.get("lan"))


def remember(on: bool) -> None:
    from openprogram.state.config import update_config

    def mutate(cfg: dict) -> None:
        web = cfg.get("web") if isinstance(cfg.get("web"), dict) else {}
        web["lan"] = bool(on)
        cfg["web"] = web

    update_config(mutate)


def origin() -> str:
    """The Origin the listener answers while it runs, else empty."""
    return _origin if _task is not None and not _task.done() else ""


async def _serve(server) -> None:
    # uvicorn ends a failed bind with sys.exit; inside a task that would end the Worker.
    try:
        await server.serve()
    except SystemExit:
        pass


async def _stop_locked(auth) -> None:
    global _server, _task, _origin
    if _server is not None:
        _server.should_exit = True
    if _task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(_task), timeout=3)
        except Exception:
            _task.cancel()
    if _origin and auth is not None:
        auth.allow_origins(removed=[_origin])
    _server, _task, _origin = None, None, ""


async def apply(auth, app: Any = None) -> str:
    """Bring the listener in line with the config and the current address; its Origin or empty.

    Called at start-up, on every switch, and when the state is read, so a
    computer that joined another network listens on its new address.
    """
    global _app, _server, _task, _origin
    if app is not None:
        _app = app
    from openprogram.host.security import remote_access as ways
    want = configured()
    async with _lock:
        address = await asyncio.to_thread(ways.lan_address) if want else None
        wanted_origin = f"http://{address}:{auth.port}" if address else ""
        if origin() and origin() == wanted_origin:
            return _origin
        await _stop_locked(auth)
        if not wanted_origin or _app is None:
            return ""
        import uvicorn
        config = uvicorn.Config(
            _app, host=address, port=auth.port, lifespan="off", log_level="warning", access_log=False,
            ws_max_size=128 * 1024 * 1024, proxy_headers=False, timeout_graceful_shutdown=1.0,
        )
        server = uvicorn.Server(config)
        auth.allow_origins(added=[wanted_origin])
        _server, _origin = server, wanted_origin
        _task = asyncio.create_task(_serve(server))
        # A port another program holds on that address ends the task at once.
        for _ in range(40):
            if _task.done() or getattr(server, "started", False):
                break
            await asyncio.sleep(0.05)
        if _task.done() or not getattr(server, "started", False):
            _log.warning("Same Wi-Fi could not listen on %s", wanted_origin)
            await _stop_locked(auth)
            return ""
        return _origin


async def stop(auth) -> None:
    async with _lock:
        await _stop_locked(auth)


__all__ = ["apply", "configured", "origin", "remember", "stop"]
