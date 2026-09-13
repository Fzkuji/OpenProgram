from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.webui import server
from openprogram.webui.ws_delivery import QueuedWebSocket


class _SlowWebSocket:
    def __init__(self) -> None:
        self.accepted = asyncio.Event()
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()
        self.disconnect = asyncio.Event()
        self.sent: list[dict] = []
        self.inflight = 0
        self.max_inflight = 0
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted.set()

    async def send_text(self, payload: str) -> None:
        self.inflight += 1
        self.max_inflight = max(self.max_inflight, self.inflight)
        try:
            if len(self.sent) >= 2:
                self.blocked.set()
                await self.release.wait()
            self.sent.append(json.loads(payload))
        finally:
            self.inflight -= 1

    async def receive_text(self) -> str:
        await self.disconnect.wait()
        raise WebSocketDisconnect(1000)

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        self.release.set()
        self.disconnect.set()


class _FailingWebSocket(_SlowWebSocket):
    async def send_text(self, payload: str) -> None:
        if len(self.sent) >= 2:
            raise RuntimeError("transport failed")
        self.sent.append(json.loads(payload))


class _BlockedTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def send_text(self, _payload: str) -> None:
        self.started.set()
        await asyncio.Event().wait()


class _GateTransport:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.sent: list[dict] = []
        self.closed: tuple[int, str] | None = None

    async def send_text(self, payload: str) -> None:
        self.started.set()
        await self.release.wait()
        self.sent.append(json.loads(payload))

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        self.release.set()


class _FailedTransport:
    def __init__(self) -> None:
        self.closed: tuple[int, str] | None = None

    async def send_text(self, _payload: str) -> None:
        raise RuntimeError("transport failed")

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


@pytest.fixture(autouse=True)
def _isolated_server_state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_loop", None)
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})


def test_slow_client_has_one_sender_for_threadsafe_broadcasts() -> None:
    async def scenario() -> None:
        server._loop = asyncio.get_running_loop()
        ws = _SlowWebSocket()
        handler = asyncio.create_task(server._websocket_handler(ws))
        await asyncio.wait_for(ws.accepted.wait(), 1)

        # Let the two connection bootstrap frames finish, then block the
        # transport while a worker-style producer broadcasts many updates.
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        def produce() -> None:
            for revision in range(400):
                server._broadcast(json.dumps({
                    "type": "context_stats",
                    "data": {
                        "session_id": "s1",
                        "msg_id": "m1",
                        "revision": revision,
                    },
                }))

        await asyncio.to_thread(produce)
        await asyncio.wait_for(ws.blocked.wait(), 1)
        await asyncio.sleep(0)

        managed_ws = server._ws_connections[0]
        metrics = managed_ws.delivery_metrics
        assert metrics["queue_frames"] <= 256
        assert metrics["queue_bytes"] <= 4 * 1024 * 1024
        assert metrics["coalesced"] >= 398

        for _ in range(100):
            server._broadcast(json.dumps({
                "type": "chat_response",
                "data": {
                    "type": "stream_event",
                    "session_id": "s1",
                    "msg_id": "m1",
                    "event": {"type": "text", "text": "x"},
                },
            }))
        server._broadcast(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "result",
                "session_id": "s1",
                "msg_id": "m1",
                "content": "done",
            },
        }))
        await asyncio.sleep(0)

        try:
            assert ws.max_inflight == 1
            ws.release.set()
            for _ in range(100):
                if any(
                    frame.get("type") == "chat_response"
                    and frame.get("data", {}).get("type") == "result"
                    for frame in ws.sent
                ):
                    break
                await asyncio.sleep(0)
            assert any(
                frame.get("type") == "chat_response"
                and frame.get("data", {}).get("type") == "result"
                for frame in ws.sent
            )
            merged_text = "".join(
                frame.get("data", {}).get("event", {}).get("text", "")
                for frame in ws.sent
                if frame.get("type") == "chat_response"
                and frame.get("data", {}).get("type") == "stream_event"
            )
            assert merged_text == "x" * 100
            terminal_index = next(
                index
                for index, frame in enumerate(ws.sent)
                if frame.get("type") == "chat_response"
                and frame.get("data", {}).get("type") == "result"
            )
            assert all(
                frame.get("type") != "context_stats"
                for frame in ws.sent[terminal_index + 1:]
            )
        finally:
            ws.disconnect.set()
            await asyncio.wait_for(handler, 1)

    asyncio.run(scenario())


def test_send_failure_closes_and_records_recovery() -> None:
    async def scenario() -> None:
        server._loop = asyncio.get_running_loop()
        ws = _FailingWebSocket()
        handler = asyncio.create_task(server._websocket_handler(ws))
        await asyncio.wait_for(ws.accepted.wait(), 1)
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        managed_ws = server._ws_connections[0]

        server._broadcast(json.dumps({
            "type": "context_stats",
            "data": {"session_id": "s1", "msg_id": "m1"},
        }))
        for _ in range(100):
            if ws.closed is not None:
                break
            await asyncio.sleep(0)

        assert ws.closed == (1011, "state_recovery_required")
        assert managed_ws.delivery_metrics["send_failures"] == 1
        assert managed_ws.delivery_metrics["recovery_mode"] == "state_recovery_required"
        await asyncio.wait_for(handler, 1)

    asyncio.run(scenario())


def test_stop_releases_inflight_send_waiter() -> None:
    async def scenario() -> None:
        raw = _BlockedTransport()
        managed = QueuedWebSocket(raw, asyncio.get_running_loop())
        managed.start()
        send = asyncio.create_task(managed.send_text(json.dumps({
            "type": "chat_ack",
            "data": {"session_id": "s1", "msg_id": "m1"},
        })))
        await asyncio.wait_for(raw.started.wait(), 1)

        await managed.stop()

        assert send.done()
        with pytest.raises(WebSocketDisconnect):
            await send

    asyncio.run(scenario())


def test_awaited_send_surfaces_transport_failure() -> None:
    async def scenario() -> None:
        raw = _FailedTransport()
        managed = QueuedWebSocket(raw, asyncio.get_running_loop())
        managed.start()

        with pytest.raises(WebSocketDisconnect) as exc_info:
            await managed.send_text(json.dumps({
                "type": "chat_ack",
                "data": {"session_id": "s1", "msg_id": "m1"},
            }))

        assert exc_info.value.code == 1011
        assert raw.closed == (1011, "state_recovery_required")
        assert managed.delivery_metrics["send_failures"] == 1
        await managed.stop()

    asyncio.run(scenario())


def test_terminal_updates_use_critical_reserve() -> None:
    async def scenario() -> None:
        raw = _GateTransport()
        managed = QueuedWebSocket(raw, asyncio.get_running_loop())
        managed.start()

        for execution_id in range(184):
            managed.enqueue_text_threadsafe(json.dumps({
                "type": "execution.updated",
                "execution": {
                    "execution_id": f"running-{execution_id}",
                    "session_id": "s1",
                    "status": "running",
                },
            }))
        await asyncio.wait_for(raw.started.wait(), 1)
        await asyncio.sleep(0)
        assert managed.delivery_metrics["queue_frames"] == 184

        managed.enqueue_text_threadsafe(json.dumps({
            "type": "execution.updated",
            "execution": {
                "execution_id": "terminal-execution",
                "session_id": "s1",
                "status": "cancelled",
            },
        }))
        managed.enqueue_text_threadsafe(json.dumps({
            "type": "chat_response",
            "data": {
                "type": "tree_update",
                "session_id": "s1",
                "msg_id": "terminal-tree",
                "tree": {"status": "completed"},
            },
        }))
        await asyncio.sleep(0)

        assert managed.delivery_metrics["queue_frames"] == 186
        assert managed.delivery_metrics["recovery_mode"] is None
        raw.release.set()
        for _ in range(300):
            if len(raw.sent) == 186:
                break
            await asyncio.sleep(0)
        assert any(
            frame.get("execution", {}).get("status") == "cancelled"
            for frame in raw.sent
        )
        assert any(
            frame.get("data", {}).get("tree", {}).get("status") == "completed"
            for frame in raw.sent
        )
        await managed.stop()

    asyncio.run(scenario())


def test_inflight_frame_counts_toward_byte_budget() -> None:
    async def scenario() -> None:
        raw = _GateTransport()
        managed = QueuedWebSocket(raw, asyncio.get_running_loop())
        managed.start()
        payload = json.dumps({
            "type": "chat_ack",
            "data": {
                "session_id": "s1",
                "msg_id": "large-1",
                "content": "x" * (3 * 1024 * 1024),
            },
        })
        first = asyncio.create_task(managed.send_text(payload))
        await asyncio.wait_for(raw.started.wait(), 1)

        assert managed.delivery_metrics["queue_frames"] == 1
        assert managed.delivery_metrics["queue_bytes"] == len(payload.encode("utf-8"))

        second = asyncio.create_task(managed.send_text(json.dumps({
            "type": "chat_ack",
            "data": {
                "session_id": "s1",
                "msg_id": "large-2",
                "content": "y" * (2 * 1024 * 1024),
            },
        })))
        for _ in range(100):
            if managed.delivery_metrics["recovery_mode"] is not None:
                break
            await asyncio.sleep(0)

        assert managed.delivery_metrics["recovery_mode"] == "state_recovery_required"
        for _ in range(100):
            if raw.closed is not None:
                break
            await asyncio.sleep(0)
        assert raw.closed == (1013, "state_recovery_required")
        with pytest.raises(WebSocketDisconnect):
            await first
        with pytest.raises(WebSocketDisconnect):
            await second
        await managed.stop()

    asyncio.run(scenario())


def test_critical_overflow_closes_with_recovery_required() -> None:
    async def scenario() -> None:
        server._loop = asyncio.get_running_loop()
        ws = _SlowWebSocket()
        handler = asyncio.create_task(server._websocket_handler(ws))
        await asyncio.wait_for(ws.accepted.wait(), 1)
        while len(ws.sent) < 2:
            await asyncio.sleep(0)

        server._broadcast(json.dumps({
            "type": "chat_ack",
            "data": {"session_id": "s1", "msg_id": "m0"},
        }))
        await asyncio.wait_for(ws.blocked.wait(), 1)
        for request in range(1, 300):
            server._broadcast(json.dumps({
                "type": "chat_ack",
                "data": {
                    "session_id": "s1",
                    "msg_id": f"m{request}",
                },
            }))
        for _ in range(100):
            if ws.closed is not None:
                break
            await asyncio.sleep(0)

        assert ws.closed == (1013, "state_recovery_required")
        await asyncio.wait_for(handler, 1)

    asyncio.run(scenario())


def test_oversized_critical_frame_closes_with_recovery_required() -> None:
    async def scenario() -> None:
        server._loop = asyncio.get_running_loop()
        ws = _SlowWebSocket()
        handler = asyncio.create_task(server._websocket_handler(ws))
        await asyncio.wait_for(ws.accepted.wait(), 1)
        while len(ws.sent) < 2:
            await asyncio.sleep(0)

        server._broadcast(json.dumps({
            "type": "chat_ack",
            "data": {
                "session_id": "s1",
                "msg_id": "oversized",
                "content": "x" * (4 * 1024 * 1024),
            },
        }))
        for _ in range(100):
            if ws.closed is not None:
                break
            await asyncio.sleep(0)

        assert ws.closed == (1013, "state_recovery_required")
        await asyncio.wait_for(handler, 1)

    asyncio.run(scenario())
