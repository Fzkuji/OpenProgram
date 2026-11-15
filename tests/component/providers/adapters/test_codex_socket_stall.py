"""A Codex turn whose WebSocket stops sending ends its attempt and is retried.

A local WebSocket server stands in for chatgpt.com: it answers each
``response.create`` with ``response.created`` and then, for the stalled
turns, keeps the socket open and says nothing more. The stream idle bound,
shortened here to a fraction of a second, ends that attempt as a retryable
``StreamIdleTimeout``; the Codex retry loop shows a retry and sends the
turn again, and the next turn answers. A stop during the stall ends the
turn at once.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest
from websockets.asyncio.server import serve

from openprogram.models.providers.adapters.openai_codex import openai_codex as oc
from openprogram.models.providers.adapters.openai_codex import read_budget
from openprogram.models.providers.adapters.openai_codex import websocket_transport as ws
from openprogram.models.providers.types import Context, Model, UserMessage

IDLE_S = 0.4


@pytest.fixture(autouse=True)
def _local_socket(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "*")
    monkeypatch.setenv("OPENPROGRAM_CODEX_WEBSOCKET", "1")
    monkeypatch.setenv("OPENPROGRAM_PROVIDER_STREAM_BACKOFF_S", "0.01")
    monkeypatch.setattr(read_budget, "STREAM_DATA_STALL_TIMEOUT_S", IDLE_S)
    monkeypatch.setattr(oc, "_chatgpt_sign_in", lambda base_url, headers: True)
    ws._shunned.clear()
    yield
    ws._shunned.clear()


def _model(port: int) -> Model:
    return Model(id="gpt-test", name="test", api="openai-codex", provider="openai-codex",
                 base_url=f"http://127.0.0.1:{port}", reasoning=False)


def _context() -> Context:
    return Context(system_prompt="Be brief.", messages=[UserMessage(role="user", content="hi", timestamp=0)])


def _run(stalls: int, consume):
    turns = {"count": 0}
    closed: list[int] = []

    async def handler(connection):
        async for _raw in connection:
            turns["count"] += 1
            n = turns["count"]
            await connection.send(json.dumps({"type": "response.created", "response": {"id": f"resp_{n}"}}))
            if n <= stalls:
                # The socket stays open and says nothing until this end leaves it.
                await connection.wait_closed()
                closed.append(n)
                return
            item = {"type": "message", "role": "assistant", "id": f"msg_{n}", "status": "completed",
                    "content": [{"type": "output_text", "text": f"reply {n}", "annotations": []}]}
            await connection.send(json.dumps({"type": "response.output_item.added", "output_index": 0,
                                              "item": {**item, "content": []}}))
            await connection.send(json.dumps({"type": "response.output_item.done", "output_index": 0, "item": item}))
            await connection.send(json.dumps({"type": "response.completed", "response": {
                "id": f"resp_{n}", "status": "completed", "usage": {"input_tokens": 1, "output_tokens": 1}}}))

    async def main():
        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            try:
                return await consume(_model(port))
            finally:
                await ws.drain()

    result = asyncio.run(main())
    return result, turns["count"], closed


def _options(**extra):
    return {"api_key": "tok", "headers": {"originator": "test", "chatgpt-account-id": "acct"}, **extra}


def test_a_socket_that_stops_sending_is_retried_and_the_turn_answers():
    async def consume(model):
        started = time.monotonic()
        seen = []
        stream = oc.stream_openai_codex_responses(model, _context(), _options())
        async for event in stream:
            seen.append((round(time.monotonic() - started, 2), event))
        final = await stream.result()
        return seen, final, time.monotonic() - started

    (seen, final, took), turns, closed = _run(stalls=1, consume=consume)
    retries = [event for _, event in seen if event.type == "retry"]
    assert len(retries) == 1
    assert "StreamIdleTimeout" in retries[0].error
    retry_at = next(at for at, event in seen if event.type == "retry")
    assert IDLE_S <= retry_at < IDLE_S + 1.0, retry_at
    assert turns == 2
    assert closed == [1]  # the stalled socket was closed, not kept
    assert final.stop_reason == "stop"
    assert [block.text for block in final.content] == ["reply 2"]
    assert took < 10, took  # the retried turn answers; it does not wait out another bound


def test_a_stop_during_the_stall_ends_the_turn_at_once():
    signal = threading.Event()

    async def consume(model):
        stream = oc.stream_openai_codex_responses(model, _context(), _options(signal=signal))
        events = []

        async def read():
            async for event in stream:
                events.append(event)

        reader = asyncio.ensure_future(read())
        await asyncio.wait({reader}, timeout=IDLE_S / 4)
        stopped_at = time.monotonic()
        signal.set()
        await asyncio.wait({reader}, timeout=IDLE_S * 5)
        return events, time.monotonic() - stopped_at

    (events, stopped_in), turns, closed = _run(stalls=10, consume=consume)
    assert stopped_in < IDLE_S, stopped_in
    assert turns == 1
    assert closed == [1]
    assert events[-1].type == "error" and events[-1].reason == "aborted"


def test_a_reader_that_stops_leaves_no_task_of_the_stream_pending():
    """A stopped turn stops reading and closes the stream (``close_stream``,
    as the agent loop does). The adapter's own task, the read it has in
    flight and the socket close it starts are all finished before the close
    returns: none is left for the loop's teardown to destroy ("Task was
    destroyed but it is pending")."""
    from openprogram.models.providers.utils.event_stream import close_stream

    adapter_tasks = {"stream_openai_codex_responses.<locals>._run", "Connection.recv", "_Kept.close"}

    def pending_of_the_stream():
        return [task for task in asyncio.all_tasks() if not task.done()
                and getattr(task.get_coro(), "__qualname__", "") in adapter_tasks]

    async def consume(model):
        stream = oc.stream_openai_codex_responses(model, _context(), _options())
        first = await stream.__anext__()
        await asyncio.sleep(IDLE_S / 4)  # the reply has begun and is stalled mid-read
        running = pending_of_the_stream()
        await close_stream(stream)
        return first, running, pending_of_the_stream()

    (first, running, left), turns, closed = _run(stalls=10, consume=consume)
    assert first.type == "start"
    assert running, "the stream's task was not running when the reader stopped"
    assert left == []
    assert closed == [1]
