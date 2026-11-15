"""Closing a Codex WebSocket ends once the backend answers the close frame.

The stand-in backend answers the close frame at once and then keeps the TCP
connection open, as chatgpt.com does for about two seconds.
"""

from __future__ import annotations

import asyncio
import time

from websockets.frames import Close
from websockets.http11 import Request
from websockets.server import ServerProtocol

from openprogram.models.providers.adapters.openai_codex import websocket_transport as ws

HOLD_S = 5.0


def test_a_close_does_not_wait_for_the_backend_to_drop_the_connection(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    seen: dict = {}

    async def backend(reader, writer):
        protocol = ServerProtocol()
        while True:
            data = await reader.read(65536)
            if not data:
                seen["client_ended"] = time.monotonic()
                break
            protocol.receive_data(data)
            for event in protocol.events_received():
                if isinstance(event, Request):
                    protocol.send_response(protocol.accept(event))
            for chunk in protocol.data_to_send():
                if chunk:  # an empty chunk asks to end TCP: held instead
                    writer.write(chunk)
            await writer.drain()
            if protocol.close_rcvd is not None:
                seen["close"] = protocol.close_rcvd
                try:
                    data = await asyncio.wait_for(reader.read(65536), HOLD_S)
                except (asyncio.TimeoutError, ConnectionError):
                    data = None
                if data == b"":
                    seen["client_ended"] = time.monotonic()
                break
        writer.close()

    async def main():
        server = await asyncio.start_server(backend, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            kept = await ws._dial(f"ws://127.0.0.1:{port}/codex/responses", {}, "", "127.0.0.1")
            started = time.monotonic()
            await kept.close()
            seen["closed_in"] = time.monotonic() - started
            seen["started"] = started
            await asyncio.sleep(0.2)

    asyncio.run(main())
    assert seen["close"] == Close(1000, "")
    assert seen["closed_in"] < 1.0, seen
    # The backend sees this end go, without holding it for HOLD_S.
    assert seen["client_ended"] - seen["started"] < 1.0, seen
