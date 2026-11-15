"""PKCE callback readiness through a real loopback HTTP listener."""
from __future__ import annotations

import asyncio


def test_callback_listener_is_ready_before_opening_browser():
    from openprogram.models.auth.methods.pkce_oauth import PkceConfig, _run_callback_server
    import socket
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    cfg = PkceConfig(authorize_url="https://example.com", token_url="https://example.com", client_id="test", callback_port=port)
    async def run():
        import aiohttp
        async def open_browser():
            async with aiohttp.ClientSession() as client:
                async with client.get(f"http://127.0.0.1:{port}/auth/callback?code=ok&state=S") as response:
                    assert response.status == 200
        return await asyncio.wait_for(_run_callback_server(cfg, "S", on_ready=open_browser), 30)
    assert asyncio.run(run()) == "ok"
