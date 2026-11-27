"""OpenAI Responses over a WebSocket (``GET /v1/responses`` upgraded).

Ported from yetone/magpie ``internal/gateway/responses_ws.go`` (MIT
License, Copyright (c) yetone). Each ``response.create`` text frame is the
body the HTTP route takes, less ``type``, ``previous_response_id`` and
``generate``, always streamed; each server-sent event of the reply goes back
as one text frame. The socket keeps its last two replies (their input and
output items) so a turn may name the one before it with
``previous_response_id``; an id it does not hold is answered
``previous_response_not_found``, on which Codex opens a new socket and sends
the conversation whole. ``generate: false`` is a warmup: it is answered
``response.created`` and ``response.completed`` with no reply, and kept.
``response.interrupt`` ends the turn in flight as ``incomplete``. At most
``PENDING`` frames wait behind a turn; one more closes the socket with an
error frame.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from . import conversion as cv
from .conversion import GatewayError

#: How many replies a socket keeps for ``previous_response_id``.
KEEP = 2

#: How many frames may wait unread or behind the turn in flight.
PENDING = 32

_OVERFLOW = object()

Send = Callable[[str], Awaitable[None]]


def error_frame(status: int, kind: str, code: str, message: str, param: str | None = None) -> str:
    return json.dumps({"type": "error", "status": status,
                       "error": {"type": kind, "code": code, "message": message, "param": param}},
                      ensure_ascii=False)


def _gateway_error_frame(error: GatewayError) -> str:
    from .responses import error_body
    detail = error_body(error)["error"]
    status = error.status if 400 <= error.status < 600 else 500
    return error_frame(status, detail["type"], detail["code"], detail["message"], detail.get("param"))


def _input_items(raw: Any) -> Optional[list[Any]]:
    if isinstance(raw, str):
        return [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": raw}]}]
    if raw is None:
        return []
    return list(raw) if isinstance(raw, list) else None


class ResponsesSocket:
    """One client's socket: the replies it keeps and the turn in flight."""

    def __init__(self, send: Send, harness: str) -> None:
        self.send = send
        self.harness = harness
        self.held: "OrderedDict[str, list[Any]]" = OrderedDict()
        self.ids: list[str] = []

    def keep(self, response_id: str, items: list[Any]) -> None:
        self.held[response_id] = items
        while len(self.held) > KEEP:
            self.held.popitem(last=False)

    async def turn(self, frame: dict[str, Any]) -> None:
        """Serve one ``response.create``."""
        fresh = _input_items(frame.get("input"))
        if fresh is None:
            await self.send(error_frame(400, "invalid_request_error", "invalid_value",
                                        "input is neither text nor a list of items", "input"))
            return
        prior: list[Any] = []
        previous = frame.get("previous_response_id")
        if previous:
            if previous not in self.held:
                await self.send(error_frame(400, "invalid_request_error", "previous_response_not_found",
                                            f"Previous response with id '{previous}' not found.",
                                            "previous_response_id"))
                return
            prior = self.held[previous]
        if frame.get("generate") is False:
            response_id = cv.new_id("resp_")
            self.ids.append(response_id)
            response: dict[str, Any] = {"id": response_id, "object": "response", "created_at": int(time.time()),
                                        "status": "in_progress", "output": []}
            if frame.get("model"):
                response["model"] = frame["model"]
            await self.send(json.dumps({"type": "response.created", "response": response}))
            response = {**response, "status": "completed",
                        "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
            await self.send(json.dumps({"type": "response.completed", "response": response}))
            self.keep(response_id, prior + fresh)
            return
        body = {k: v for k, v in frame.items() if k not in {"type", "previous_response_id", "generate"}}
        body["input"] = prior + fresh
        body["stream"] = True
        from . import responses
        from .stream import UpstreamCall
        try:
            parsed = responses.parse_request(body)
            call = await UpstreamCall.open(parsed.model, parsed.context, parsed.options, harness=self.harness)
        except GatewayError as error:
            await self.send(_gateway_error_frame(error))
            return
        response_id, output = None, None
        async for chunk in responses.stream_events(call, parsed):
            text = chunk.decode("utf-8")
            data = next((line[6:] for line in text.split("\n") if line.startswith("data: ")), None)
            if data is None:
                continue
            event = json.loads(data)
            kind = event.get("type")
            if kind == "response.created":
                response_id = event["response"]["id"]
                self.ids.append(response_id)
            elif kind == "response.completed":
                output = event["response"].get("output") or []
            await self.send(data)
        if response_id and output is not None:
            self.keep(response_id, body["input"] + output)


def interrupt_reply(kind: str, response_id: str) -> str:
    return json.dumps({"type": kind, "response_id": response_id})


async def serve(websocket, harness: str) -> None:
    """Read frames until the client goes; one turn runs at a time, and a
    frame that comes during it waits, except ``response.interrupt``."""
    from starlette.websockets import WebSocketDisconnect

    await websocket.accept()
    socket = ResponsesSocket(websocket.send_text, harness)
    inbox: asyncio.Queue = asyncio.Queue()
    queued: list[Any] = []

    async def read() -> None:
        try:
            while True:
                text = await websocket.receive_text()
                if inbox.qsize() + len(queued) >= PENDING:
                    inbox.put_nowait(_OVERFLOW)
                    return
                inbox.put_nowait(text)
        except (WebSocketDisconnect, RuntimeError):
            inbox.put_nowait(None)

    async def overflow() -> None:
        await socket.send(error_frame(400, "invalid_request_error", "too_many_messages",
                                      f"more than {PENDING} messages are waiting on this socket"))
        await websocket.close(code=1008)

    reader = asyncio.create_task(read())
    try:
        while True:
            raw = queued.pop(0) if queued else await inbox.get()
            if raw is None:
                return
            if raw is _OVERFLOW:
                await overflow()
                return
            try:
                frame = json.loads(raw)
            except ValueError:
                frame = None
            if not isinstance(frame, dict):
                await socket.send(error_frame(400, "invalid_request_error", "invalid_json",
                                              "response.create is not a JSON object"))
                continue
            kind = frame.get("type")
            if kind == "response.interrupt":
                await socket.send(interrupt_reply("response.interrupt.failed", str(frame.get("response_id") or "")))
                continue
            if kind != "response.create":
                await socket.send(error_frame(400, "invalid_request_error", "unsupported_message",
                                              f"OpenProgram serves response.create on this socket, not {kind!r}", "type"))
                continue
            first = len(socket.ids)
            task = asyncio.create_task(socket.turn(frame))
            while not task.done():
                getter = asyncio.create_task(inbox.get())
                done, _ = await asyncio.wait({task, getter}, return_when=asyncio.FIRST_COMPLETED)
                if getter not in done:
                    getter.cancel()
                    break
                incoming = getter.result()
                if incoming is None:
                    task.cancel()
                    return
                if incoming is _OVERFLOW or len(queued) >= PENDING:
                    task.cancel()
                    await overflow()
                    return
                try:
                    message = json.loads(incoming)
                except ValueError:
                    message = None
                if not isinstance(message, dict) or message.get("type") != "response.interrupt":
                    queued.append(incoming)
                    continue
                wanted = str(message.get("response_id") or "")
                if wanted not in socket.ids[first:]:
                    await socket.send(interrupt_reply("response.interrupt.failed", wanted))
                    continue
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                await socket.send(interrupt_reply("response.interrupt.accepted", wanted))
                await socket.send(json.dumps({"type": "response.incomplete", "response": {
                    "id": wanted, "object": "response", "status": "incomplete",
                    "incomplete_details": {"reason": "interrupted"}, "output": []}}))
            if not task.cancelled():
                await task
    finally:
        reader.cancel()
