"""Parking and transcript frames in one socket's queue."""
from __future__ import annotations

import asyncio
import json

from openprogram.interfaces.server.ws_delivery import MAX_PARKED_FRAMES, NORMAL_QUEUE_FRAMES, QueuedWebSocket


class Raw:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        pass


def _text(session: str, text: str, offset: int) -> str:
    return json.dumps({"type": "chat_response", "data": {
        "type": "stream_event", "session_id": session, "msg_id": "m", "row_id": "m_reply", "item_id": "text:0", "offset": offset,
        "event": {"type": "text", "text": text},
    }})


def _update(session: str, revision: int) -> str:
    return json.dumps({"type": "transcript_update", "data": {"session_id": session, "view": session, "previous": revision - 1, "revision": revision, "changes": []}})


async def _drive(script) -> list[dict]:
    raw = Raw()
    ws = QueuedWebSocket(raw, asyncio.get_running_loop())
    ws.start()
    await script(ws)
    await asyncio.sleep(0)
    for _ in range(50):
        if not ws._queue and ws._inflight is None:
            break
        await asyncio.sleep(0.001)
    await ws.stop()
    return raw.sent


def _types(sent: list[dict]) -> list[str]:
    return [frame["type"] for frame in sent]


def test_parked_contiguous_text_merges_and_nested_reads_flush_at_zero() -> None:
    async def script(ws) -> None:
        outer = ws.park_read("s")
        inner = ws.park_read("s")
        ws._enqueue(_text("s", "ab", 0))
        ws._enqueue(_text("s", "cd", 2))
        inner()
        assert ws._queue == []
        outer()

    sent = asyncio.run(_drive(script))
    assert len(sent) == 1 and sent[0]["data"]["event"]["text"] == "abcd"


def test_too_many_parked_frames_become_one_gap_after_the_read() -> None:
    async def script(ws) -> None:
        end = ws.park_read("s")
        for number in range(MAX_PARKED_FRAMES + 5):
            ws._enqueue(_update("s", number + 2))
        end()

    sent = asyncio.run(_drive(script))
    assert _types(sent) == ["transcript_gap"]


def test_an_update_never_replaces_another() -> None:
    async def script(ws) -> None:
        ws._enqueue(_update("s", 2))
        ws._enqueue(_update("s", 3))

    assert [frame["data"]["revision"] for frame in asyncio.run(_drive(script))] == [2, 3]


def test_overflow_collapses_a_sessions_updates_into_exactly_one_gap() -> None:
    async def script(ws) -> None:
        ws._sender_task.cancel()
        for number in range(NORMAL_QUEUE_FRAMES + 40):
            ws._enqueue(_update("s", number + 2))
        queued = [json.loads(frame.payload)["type"] for frame in ws._queue]
        assert queued.count("transcript_gap") == 1
        assert "s" in ws._transcript_gaps
        ws._queue.clear()
        ws._queue_bytes = 0

    asyncio.run(_drive(script))
