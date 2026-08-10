"""A real owner-authenticated listener, for tests that live outside Python.

The CLI's vitest suite stubs ``fetch``, so it can assert which headers the
TUI builds but never whether the server accepts them. That gap is what let
a 403 ship. Running this module starts a real Uvicorn listener guarded by
the real :class:`OwnerAuthMiddleware` on an ephemeral port and prints one
JSON line — ``{"port": ..., "token": ...}`` — so a Node test can drive the
genuine server with the genuine header set.

    python3 -m tests.helpers.owner_auth_listener
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute

from openprogram.webui.owner_auth import OwnerAuthMiddleware, OwnerAuthState


OWNER_PRINCIPAL_ID = "owner/install/0123456789abcdef"


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _app(state: OwnerAuthState) -> OwnerAuthMiddleware:
    async def api(request: Request):
        return JSONResponse(
            {"tier": request.scope["state"]["authority"]["authority_tier"]}
        )

    async def socket_route(ws):
        await ws.accept()
        await ws.send_text(ws.scope["state"]["authority"]["authority_tier"])
        await ws.close()

    return OwnerAuthMiddleware(
        Starlette(routes=[
            Route("/api/x", api, methods=["GET", "POST"]),
            WebSocketRoute("/ws", socket_route),
        ]),
        auth_state=state,
    )


def main() -> None:
    import uvicorn

    port = _free_port()
    state = OwnerAuthState.start(
        state_dir=Path(tempfile.mkdtemp()),
        bind_host="127.0.0.1",
        port=port,
        allowed_origins=(),
        owner_principal_id=OWNER_PRINCIPAL_ID,
    )
    config = uvicorn.Config(
        _app(state),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        access_log=False,
        proxy_headers=False,
    )
    server = uvicorn.Server(config)

    async def serve() -> None:
        task = asyncio.ensure_future(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)
        # Announce only once the socket is accepting, so the reader never
        # races the bind.
        print(json.dumps({"port": port, "token": state.token}), flush=True)
        await task

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    finally:
        state.close()


if __name__ == "__main__":
    sys.exit(main())
