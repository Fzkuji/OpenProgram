"""Bedrock's synchronous boto3 stream is read off the event loop.

A local endpoint speaks the ConverseStream event-stream framing. When it
sends one event and then nothing, the stream idle bound (shortened to a
fraction of a second) ends the attempt at the bound, the event loop keeps
running meanwhile, and the reader thread is released instead of waiting for
botocore's read timeout. A complete reply still streams through to ``done``.
The SDK transport policy that keeps Bedrock disabled in the product is
lifted for the test only.
"""
from __future__ import annotations

import asyncio
import binascii
import json
import socket
import struct
import threading
import time

import pytest

from openprogram.models.providers.adapters.amazon_bedrock import amazon_bedrock
from openprogram.models.providers.streaming.stream import _metered
from openprogram.models.providers.types import Context, Model, SimpleStreamOptions, UserMessage
from openprogram.models.providers.utils.stream_idle import StreamIdleTimeout

IDLE_S = 0.5


def _header(name: str, value: str) -> bytes:
    n, v = name.encode(), value.encode()
    return struct.pack("!B", len(n)) + n + b"\x07" + struct.pack("!H", len(v)) + v


def _event(kind: str, payload: dict) -> bytes:
    headers = (_header(":event-type", kind) + _header(":content-type", "application/json")
               + _header(":message-type", "event"))
    body = json.dumps(payload).encode()
    prelude = struct.pack("!II", 12 + len(headers) + len(body) + 4, len(headers))
    prelude += struct.pack("!I", binascii.crc32(prelude) & 0xFFFFFFFF)
    message = prelude + headers + body
    return message + struct.pack("!I", binascii.crc32(message) & 0xFFFFFFFF)


def _chunk(data: bytes) -> bytes:
    return b"%x\r\n" % len(data) + data + b"\r\n"


class _Endpoint:
    """Answer each request with ``events``; then end the reply, or stall when ``stall``."""

    def __init__(self, events: list[bytes], stall: bool) -> None:
        self.events = events
        self.stall = stall
        self.released = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        threading.Thread(target=self._serve, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._answer, args=(conn,), daemon=True).start()

    def _answer(self, conn: socket.socket) -> None:
        with conn:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            head, body = data.split(b"\r\n\r\n", 1)
            length = next(int(line.split(b":")[1]) for line in head.split(b"\r\n")
                          if line.lower().startswith(b"content-length"))
            while len(body) < length:
                body += conn.recv(65536)
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/vnd.amazon.eventstream\r\n"
                         b"Transfer-Encoding: chunked\r\n\r\n")
            for event in self.events:
                conn.sendall(_chunk(event))
            if not self.stall:
                conn.sendall(b"0\r\n\r\n")
                return
            conn.settimeout(30)
            try:
                # The client's release shows here as the connection ending.
                while conn.recv(65536):
                    pass
            except OSError:
                pass
            self.released.set()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def endpoint(monkeypatch):
    made: list[_Endpoint] = []

    def make(events, stall=False):
        server = _Endpoint(events, stall)
        made.append(server)
        monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", server.url)
        return server

    monkeypatch.setenv("AWS_BEDROCK_SKIP_AUTH", "1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setattr("openprogram.host.security.safe_http.require_active_sdk_transport",
                        lambda *_args, **_kwargs: None)
    yield make
    for server in made:
        server.close()


def _model() -> Model:
    return Model(id="anthropic.claude-test-v1", name="Bedrock test", api="bedrock-converse-stream",
                 provider="amazon-bedrock", base_url="https://bedrock-runtime.us-east-1.amazonaws.com")


async def _read(idle_s):
    """Events with their times, the error if any, and the longest event-loop pause."""
    pauses: list[float] = []

    async def heartbeat():
        last = time.monotonic()
        while True:
            await asyncio.sleep(0.02)
            now = time.monotonic()
            pauses.append(now - last)
            last = now

    beat = asyncio.create_task(heartbeat())
    started = time.monotonic()
    events, error = [], None
    try:
        async for event in _metered(
            lambda m, c, o: amazon_bedrock.stream_bedrock(m, c, {}),
            _model(), Context(messages=[UserMessage(content="hi", timestamp=0)]),
            SimpleStreamOptions(), None, idle_s=idle_s,
        ):
            events.append((time.monotonic() - started, event))
    except Exception as exc:  # noqa: BLE001 -- returned for the assertions
        error = (time.monotonic() - started, exc)
    beat.cancel()
    return events, error, max(pauses, default=0.0)


def test_a_stalled_stream_ends_at_the_bound_without_freezing_the_loop(endpoint):
    # botocore loads its service model on the first client (about 0.2s idle,
    # several times that on a loaded machine): a first reply before the
    # stalled one keeps that load out of the bound being measured.
    endpoint([_event("messageStart", {"role": "assistant"}),
              _event("messageStop", {"stopReason": "end_turn"})])
    assert asyncio.run(_read(30))[1] is None
    server = endpoint([_event("messageStart", {"role": "assistant"})], stall=True)
    events, error, longest_pause = asyncio.run(_read(IDLE_S))
    assert [event.type for _, event in events] == ["start"]
    assert error is not None and isinstance(error[1], StreamIdleTimeout)
    assert error[0] - events[0][0] == pytest.approx(IDLE_S, abs=0.4)
    # The loop ran on while the read was stalled (generous for a loaded CI).
    assert longest_pause < IDLE_S
    assert server.released.wait(5)


def test_a_complete_reply_streams_to_done(endpoint):
    endpoint([
        _event("messageStart", {"role": "assistant"}),
        _event("contentBlockDelta", {"contentBlockIndex": 0, "delta": {"text": "answer"}}),
        _event("contentBlockStop", {"contentBlockIndex": 0}),
        _event("messageStop", {"stopReason": "end_turn"}),
    ])
    events, error, _ = asyncio.run(_read(IDLE_S))
    assert error is None
    done = events[-1][1]
    assert done.type == "done"
    assert [block.text for block in done.message.content if block.type == "text"] == ["answer"]
