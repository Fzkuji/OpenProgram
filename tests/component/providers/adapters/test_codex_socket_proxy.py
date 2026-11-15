"""A Codex WebSocket dialled through an HTTP proxy.

The stand-in proxy answers CONNECT the way it is told: late, with a
refusal, or by tunnelling to a stand-in backend.
"""

from __future__ import annotations

import asyncio
import socket
import struct

import pytest
from websockets.http11 import Request
from websockets.server import ServerProtocol

from openprogram.models.providers.adapters.openai_codex import websocket_transport as ws
from openprogram.models.providers.utils import http_proxy


async def _read_connect(reader: asyncio.StreamReader) -> str:
    head = await reader.readuntil(b"\r\n\r\n")
    return head.split(b"\r\n", 1)[0].decode()


def _through(monkeypatch, port: int) -> None:
    monkeypatch.setattr(http_proxy, "proxy_for_url", lambda url: f"http://127.0.0.1:{port}")
    ws._shunned.clear()


def test_a_dial_given_up_while_the_proxy_connects_ends_without_an_error(monkeypatch):
    seen: dict = {"errors": []}

    async def main():
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _loop, context: seen["errors"].append(context))
        cancelled = asyncio.Event()

        async def proxy(reader, writer):
            seen["request"] = await _read_connect(reader)
            await cancelled.wait()
            await asyncio.sleep(0.05)
            # The answer arrives after the dial was given up.
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            try:
                await writer.drain()
                seen["proxy_read"] = await asyncio.wait_for(reader.read(1), 2.0)
            except (ConnectionError, asyncio.TimeoutError):
                seen["proxy_read"] = None
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with server:
            dial = asyncio.create_task(ws._dial("ws://backend.test/codex/responses", {}, "", "backend.test"))
            while "request" not in seen:
                await asyncio.sleep(0.01)
            dial.cancel()
            with pytest.raises(asyncio.CancelledError):
                await dial
            cancelled.set()
            await asyncio.sleep(0.3)

    asyncio.run(main())
    assert seen["request"].startswith("CONNECT backend.test:80 ")
    assert seen["errors"] == []
    # The proxy connection was ended by this side.
    assert seen["proxy_read"] == b""
    assert not ws._is_shunned("backend.test")


def test_a_proxy_that_refuses_sends_the_turn_over_http(monkeypatch):
    async def main():
        async def proxy(reader, writer):
            await _read_connect(reader)
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with server:
            with pytest.raises(ws.SocketUnavailable):
                await ws._dial("ws://backend.test/codex/responses", {}, "", "backend.test")

    asyncio.run(main())
    assert ws._is_shunned("backend.test")
    ws._shunned.clear()


def test_a_socket_dials_through_the_proxy_tunnel(monkeypatch):
    async def main():
        async def backend(reader, writer):
            protocol = ServerProtocol()
            while data := await reader.read(65536):
                protocol.receive_data(data)
                for event in protocol.events_received():
                    if isinstance(event, Request):
                        protocol.send_response(protocol.accept(event))
                for chunk in protocol.data_to_send():
                    if chunk:
                        writer.write(chunk)
                await writer.drain()
            writer.close()

        async def pipe(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        backend_server = await asyncio.start_server(backend, "127.0.0.1", 0)
        backend_port = backend_server.sockets[0].getsockname()[1]

        async def proxy(reader, writer):
            request = await _read_connect(reader)
            assert request.startswith(f"CONNECT 127.0.0.1:{backend_port} ")
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", backend_port)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with backend_server, server:
            kept = await ws._dial(f"ws://127.0.0.1:{backend_port}/codex/responses", {}, "", "127.0.0.1")
            await kept.close()

    asyncio.run(main())


# ---------------------------------------------------------------------------
# A reset at each stage of a proxied dial: the dial ends, the host is spoken
# HTTP (or the turn is retried), and nothing reaches the loop's exception
# handler.
# ---------------------------------------------------------------------------


def _reset(writer: asyncio.StreamWriter) -> None:
    """End the connection with a TCP reset rather than a FIN."""
    sock = writer.get_extra_info("socket")
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    writer.transport.abort()


def _catching(errors: list) -> None:
    asyncio.get_running_loop().set_exception_handler(lambda _loop, context: errors.append(context))


@pytest.mark.parametrize("ending", ["reset", "eof"])
def test_a_reset_while_the_proxy_sets_up_the_tunnel_goes_over_http(monkeypatch, ending):
    errors: list = []

    async def main():
        _catching(errors)

        async def proxy(reader, writer):
            await _read_connect(reader)
            if ending == "reset":
                _reset(writer)
            else:
                writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with server:
            with pytest.raises(ws.SocketUnavailable):
                await ws._dial("wss://backend.test/codex/responses", {}, "", "backend.test")
            await asyncio.sleep(0.2)

    asyncio.run(main())
    assert errors == []
    assert ws._is_shunned("backend.test")
    ws._shunned.clear()


@pytest.mark.parametrize("ending", ["reset", "eof"])
def test_a_reset_right_after_the_tunnel_is_up_goes_over_http(monkeypatch, ending):
    """The far end drops the tunnel during the TLS handshake inside it."""
    errors: list = []

    async def main():
        _catching(errors)

        async def proxy(reader, writer):
            await _read_connect(reader)
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            # The ClientHello arrives; the tunnel ends instead of an answer.
            await reader.read(1)
            if ending == "reset":
                _reset(writer)
            else:
                writer.close()

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with server:
            with pytest.raises(ws.SocketUnavailable):
                await ws._dial("wss://backend.test/codex/responses", {}, "", "backend.test")
            await asyncio.sleep(0.2)

    asyncio.run(main())
    assert errors == []
    assert ws._is_shunned("backend.test")
    ws._shunned.clear()


def test_a_reset_mid_stream_through_the_proxy_retries_the_turn(monkeypatch):
    errors: list = []

    async def main():
        _catching(errors)
        tunnel: dict = {}

        async def backend(reader, writer):
            protocol = ServerProtocol()
            while data := await reader.read(65536):
                protocol.receive_data(data)
                for event in protocol.events_received():
                    if isinstance(event, Request):
                        protocol.send_response(protocol.accept(event))
                    else:
                        protocol.send_text(b'{"type": "response.created", "response": {"id": "r1"}}')
                        protocol.send_text(b'{"type": "response.output_text.delta", "delta": "hi"}')
                for chunk in protocol.data_to_send():
                    if chunk:
                        writer.write(chunk)
                await writer.drain()
            writer.close()

        async def pipe(reader, writer):
            try:
                while data := await reader.read(65536):
                    writer.write(data)
                    await writer.drain()
            except ConnectionError:
                pass
            finally:
                writer.close()

        backend_server = await asyncio.start_server(backend, "127.0.0.1", 0)
        backend_port = backend_server.sockets[0].getsockname()[1]

        async def proxy(reader, writer):
            await _read_connect(reader)
            up_reader, up_writer = await asyncio.open_connection("127.0.0.1", backend_port)
            tunnel["client"] = writer
            writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
            await writer.drain()
            await asyncio.gather(pipe(reader, up_writer), pipe(up_reader, writer))

        server = await asyncio.start_server(proxy, "127.0.0.1", 0)
        _through(monkeypatch, server.sockets[0].getsockname()[1])
        async with backend_server, server:
            kept = await ws._dial(f"ws://127.0.0.1:{backend_port}/codex/responses", {}, "", "127.0.0.1")
            turn = await ws._begin(
                kept, '{"type": "response.create"}', None, reused=False,
                receive_next=lambda connection: connection.recv(),
            )
            frames = []
            with pytest.raises(ws.ProviderStreamError) as raised:
                async for frame in turn.events(lambda connection: connection.recv()):
                    frames.append(frame["type"])
                    if frame["type"] == "response.output_text.delta":
                        _reset(tunnel["client"])
            assert frames == ["response.created", "response.output_text.delta"]
            assert raised.value.retryable
            await asyncio.sleep(0.2)

    asyncio.run(main())
    assert errors == []
    assert not ws._is_shunned("127.0.0.1")
