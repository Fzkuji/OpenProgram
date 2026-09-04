"""Bounded, single-sender delivery for one WebSocket connection."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Literal

from starlette.websockets import WebSocketDisconnect


MAX_QUEUE_FRAMES = 256
MAX_QUEUE_BYTES = 4 * 1024 * 1024
CRITICAL_RESERVE_FRAMES = 64
CRITICAL_RESERVE_BYTES = 1024 * 1024
CONTROL_RESERVE_FRAMES = 8
CONTROL_RESERVE_BYTES = 64 * 1024
CONTROL_QUEUE_FRAMES = MAX_QUEUE_FRAMES - CRITICAL_RESERVE_FRAMES
CONTROL_QUEUE_BYTES = MAX_QUEUE_BYTES - CRITICAL_RESERVE_BYTES
NORMAL_QUEUE_FRAMES = CONTROL_QUEUE_FRAMES - CONTROL_RESERVE_FRAMES
NORMAL_QUEUE_BYTES = CONTROL_QUEUE_BYTES - CONTROL_RESERVE_BYTES

_TERMINAL_JOB_STATUSES = {"completed", "cancelled", "errored"}
_TERMINAL_CHAT_TYPES = {"result", "error", "cancelled"}
_TERMINAL_EXECUTION_STATUSES = {
    "cancelled",
    "completed",
    "error",
    "errored",
    "failed",
    "interrupted",
}
_SNAPSHOT_TYPES = {
    "agents_list",
    "branches_list",
    "channels_list",
    "functions_list",
    "jobs_list",
    "permission_rules",
    "provider_info",
    "sessions_list",
    "settings",
    "working_dirs",
    "worktrees_list",
}
_REPLACEABLE_TYPES = {
    "context_stats",
    "execution.updated",
    "goal_update",
    "progress",
    "tree_update",
}


@dataclass
class _Frame:
    payload: str
    size: int
    kind: Literal["critical", "append", "replace", "snapshot", "control"]
    key: tuple[str, ...]
    enqueued_at: float
    waiters: list[asyncio.Future[None]]


def _frame_metadata(payload: str) -> tuple[str, tuple[str, ...]]:
    """Classify a wire frame conservatively; unknown frames are critical."""
    try:
        envelope = json.loads(payload)
    except (TypeError, ValueError):
        return "critical", ("invalid",)
    if not isinstance(envelope, dict):
        return "critical", ("unknown",)

    frame_type = str(envelope.get("type") or "")
    data = envelope.get("data")
    if not isinstance(data, dict):
        data = {}
    session_id = str(data.get("session_id") or envelope.get("session_id") or "")
    entity_id = str(
        data.get("msg_id")
        or data.get("job_id")
        or data.get("request_id")
        or data.get("id")
        or envelope.get("msg_id")
        or envelope.get("job_id")
        or envelope.get("request_id")
        or envelope.get("id")
        or ""
    )
    execution = data.get("execution") or envelope.get("execution")
    if isinstance(execution, dict):
        session_id = str(execution.get("session_id") or session_id)
        entity_id = str(
            execution.get("execution_id") or execution.get("id") or entity_id
        )

    if frame_type == "pong":
        return "control", ("control", "pong")
    if frame_type == "job_status":
        if not entity_id:
            return "critical", (frame_type, session_id)
        if str(data.get("status") or "") in _TERMINAL_JOB_STATUSES:
            return "critical", (frame_type, session_id, entity_id)
        return "replace", (frame_type, session_id, entity_id)
    if frame_type == "chat_response":
        response_type = str(data.get("type") or "")
        if response_type in _TERMINAL_CHAT_TYPES:
            return "critical", (frame_type, response_type, session_id, entity_id)
        tree = data.get("tree")
        if (
            response_type == "tree_update"
            and isinstance(tree, dict)
            and str(tree.get("status") or "") in _TERMINAL_EXECUTION_STATUSES
        ):
            return "critical", (frame_type, response_type, session_id, entity_id)
        if response_type == "stream_event":
            event = data.get("event")
            event_type = str(event.get("type") or "") if isinstance(event, dict) else ""
            if event_type in {"text", "thinking"}:
                return "append", (frame_type, event_type, session_id, entity_id)
        if response_type in _REPLACEABLE_TYPES:
            return "replace", (frame_type, response_type, session_id, entity_id)
        return "critical", (frame_type, response_type, session_id, entity_id)
    if frame_type in _REPLACEABLE_TYPES:
        if frame_type == "tree_update":
            tree = data.get("tree") or envelope.get("tree")
            if (
                isinstance(tree, dict)
                and str(tree.get("status") or "")
                in _TERMINAL_EXECUTION_STATUSES
            ):
                return "critical", (frame_type, session_id, entity_id)
        if (
            frame_type == "execution.updated"
            and isinstance(execution, dict)
            and str(execution.get("status") or "")
            in _TERMINAL_EXECUTION_STATUSES
        ):
            return "critical", (frame_type, session_id, entity_id)
        if frame_type == "execution.updated" and not entity_id:
            return "critical", (frame_type, session_id)
        return "replace", (frame_type, session_id, entity_id)
    if frame_type in _SNAPSHOT_TYPES:
        return "snapshot", (frame_type, session_id)
    if (
        "error" in frame_type
        or frame_type.startswith("question.")
        or frame_type.endswith("_result")
        or frame_type in {"chat_ack", "operation_error"}
    ):
        return "critical", (frame_type, session_id, entity_id)
    return "critical", (frame_type or "unknown", session_id, entity_id)


def _merge_adjacent(previous: _Frame, current: _Frame) -> _Frame | None:
    if previous.kind != "append" or previous.key != current.key:
        return None
    try:
        old = json.loads(previous.payload)
        new = json.loads(current.payload)
        old_event = old["data"]["event"]
        new_event = new["data"]["event"]
        old_event["text"] = str(old_event.get("text") or "") + str(
            new_event.get("text") or ""
        )
        payload = json.dumps(old, ensure_ascii=False, separators=(",", ":"))
    except (KeyError, TypeError, ValueError):
        return None
    return _Frame(
        payload=payload,
        size=len(payload.encode("utf-8")),
        kind="append",
        key=previous.key,
        enqueued_at=previous.enqueued_at,
        waiters=[*previous.waiters, *current.waiters],
    )


class QueuedWebSocket:
    """Proxy a WebSocket while giving all writes one bounded sender task."""

    __slots__ = (
        "_raw_websocket",
        "_loop",
        "_queue",
        "_queue_bytes",
        "_inflight",
        "_wake",
        "_sender_task",
        "_closing",
        "_close_task",
        "coalesced",
        "dropped",
        "send_failures",
        "disconnect_reason",
        "recovery_mode",
    )

    def __init__(self, websocket: Any, loop: asyncio.AbstractEventLoop) -> None:
        object.__setattr__(self, "_raw_websocket", websocket)
        object.__setattr__(self, "_loop", loop)
        object.__setattr__(self, "_queue", [])
        object.__setattr__(self, "_queue_bytes", 0)
        object.__setattr__(self, "_inflight", None)
        object.__setattr__(self, "_wake", asyncio.Event())
        object.__setattr__(self, "_sender_task", None)
        object.__setattr__(self, "_closing", False)
        object.__setattr__(self, "_close_task", None)
        object.__setattr__(self, "coalesced", 0)
        object.__setattr__(self, "dropped", 0)
        object.__setattr__(self, "send_failures", 0)
        object.__setattr__(self, "disconnect_reason", None)
        object.__setattr__(self, "recovery_mode", None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._raw_websocket, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            setattr(self._raw_websocket, name, value)

    def start(self) -> None:
        if self._sender_task is None:
            self._sender_task = self._loop.create_task(
                self._sender(), name="openprogram-websocket-sender"
            )

    async def send_text(self, payload: str) -> None:
        if self._closing:
            raise WebSocketDisconnect(1013)
        waiter = self._loop.create_future()
        if not self._enqueue(payload, waiter):
            if not waiter.done():
                waiter.set_exception(WebSocketDisconnect(1013))
        await waiter

    def enqueue_text_threadsafe(self, payload: str) -> bool:
        if self._closing or self._loop.is_closed():
            return False
        try:
            self._loop.call_soon_threadsafe(self._enqueue, payload)
        except RuntimeError:
            return False
        return True

    @property
    def delivery_metrics(self) -> dict[str, int | float | str | None]:
        total_frames = len(self._queue) + int(self._inflight is not None)
        total_bytes = self._queue_bytes + (
            self._inflight.size if self._inflight is not None else 0
        )
        oldest = [frame.enqueued_at for frame in self._queue]
        if self._inflight is not None:
            oldest.append(self._inflight.enqueued_at)
        oldest_age = (
            max(0.0, time.monotonic() - min(oldest))
            if oldest
            else 0.0
        )
        return {
            "queue_frames": total_frames,
            "queue_bytes": total_bytes,
            "oldest_age": oldest_age,
            "coalesced": self.coalesced,
            "dropped": self.dropped,
            "send_failures": self.send_failures,
            "disconnect_reason": self.disconnect_reason,
            "recovery_mode": self.recovery_mode,
        }

    def _enqueue(
        self,
        payload: str,
        waiter: asyncio.Future[None] | None = None,
    ) -> bool:
        if self._closing:
            return False
        kind, key = _frame_metadata(payload)
        frame = _Frame(
            payload=payload,
            size=len(payload.encode("utf-8")),
            kind=kind,  # type: ignore[arg-type]
            key=key,
            enqueued_at=time.monotonic(),
            waiters=[waiter] if waiter is not None else [],
        )

        if kind == "append" and self._queue:
            merged = _merge_adjacent(self._queue[-1], frame)
            if merged is not None:
                queued_bytes = self._queue_bytes + merged.size - self._queue[-1].size
                total_bytes = self._total_bytes() + merged.size - self._queue[-1].size
                if total_bytes <= NORMAL_QUEUE_BYTES:
                    self._queue_bytes = queued_bytes
                    self._queue[-1] = merged
                    self.coalesced += 1
                    self._wake.set()
                    return True
                self.dropped += 1
                if frame.waiters:
                    self._require_recovery()
                self._reject(frame, 1013)
                return False

        if kind in {"replace", "snapshot", "control"}:
            for index in range(len(self._queue) - 1, -1, -1):
                queued = self._queue[index]
                if queued.kind == kind and queued.key == key:
                    self._queue_bytes -= queued.size
                    frame.waiters = [*queued.waiters, *frame.waiters]
                    del self._queue[index]
                    self.coalesced += 1
                    break

        if kind == "control":
            if (
                self._total_frames() >= CONTROL_QUEUE_FRAMES
                or self._total_bytes() + frame.size > CONTROL_QUEUE_BYTES
            ):
                self.dropped += 1
                if frame.waiters:
                    self._require_recovery()
                self._reject(frame, 1013)
                return False
        elif kind != "critical":
            if (
                self._total_frames() >= NORMAL_QUEUE_FRAMES
                or self._total_bytes() + frame.size > NORMAL_QUEUE_BYTES
            ):
                self.dropped += 1
                if frame.waiters:
                    self._require_recovery()
                self._reject(frame, 1013)
                return False
        else:
            self._evict_replaceable_for(frame.size)
            if (
                self._total_frames() >= MAX_QUEUE_FRAMES
                or self._total_bytes() + frame.size > MAX_QUEUE_BYTES
            ):
                self._require_recovery()
                self._reject(frame, 1013)
                return False

        self._queue.append(frame)
        self._queue_bytes += frame.size
        self._wake.set()
        return True

    def _evict_replaceable_for(self, incoming_bytes: int) -> None:
        index = 0
        while (
            self._total_frames() >= MAX_QUEUE_FRAMES
            or self._total_bytes() + incoming_bytes > MAX_QUEUE_BYTES
        ) and index < len(self._queue):
            frame = self._queue[index]
            if frame.kind == "critical":
                index += 1
                continue
            self._queue_bytes -= frame.size
            del self._queue[index]
            self.dropped += 1
            self._reject(frame, 1013)

    def _require_recovery(self) -> None:
        self.disconnect_reason = "queue_overflow"
        self.recovery_mode = "state_recovery_required"
        self._closing = True
        for frame in self._queue:
            self._reject(frame, 1013)
        self._queue.clear()
        self._queue_bytes = 0
        self._wake.set()
        if self._sender_task is not None and not self._sender_task.done():
            self._sender_task.cancel()
        self._close_task = self._loop.create_task(self._close_for_recovery())

    async def _close_for_recovery(self) -> None:
        try:
            await self._raw_websocket.close(
                code=1013, reason="state_recovery_required"
            )
        except Exception:
            pass

    async def _sender(self) -> None:
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                if self._closing:
                    return
                while self._queue:
                    frame = self._queue.pop(0)
                    self._queue_bytes -= frame.size
                    self._inflight = frame
                    try:
                        await self._raw_websocket.send_text(frame.payload)
                        self._resolve(frame)
                    except asyncio.CancelledError:
                        self._reject(frame, 1001)
                        raise
                    except Exception:
                        self.send_failures += 1
                        self.disconnect_reason = "send_failure"
                        self.recovery_mode = "state_recovery_required"
                        self._closing = True
                        self._reject(frame, 1011)
                        for queued in self._queue:
                            self._reject(queued, 1011)
                        self._queue.clear()
                        self._queue_bytes = 0
                        try:
                            await self._raw_websocket.close(
                                code=1011, reason="state_recovery_required"
                            )
                        except Exception:
                            pass
                        return
                    finally:
                        self._inflight = None
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        self._closing = True
        for frame in self._queue:
            self._reject(frame, 1001)
        self._queue.clear()
        self._queue_bytes = 0
        self._wake.set()
        task = self._sender_task
        if task is not None and task is not asyncio.current_task() and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        close_task = self._close_task
        if close_task is not None and not close_task.done():
            await asyncio.gather(close_task, return_exceptions=True)

    @staticmethod
    def _resolve(frame: _Frame) -> None:
        for waiter in frame.waiters:
            if not waiter.done():
                waiter.set_result(None)

    def _total_frames(self) -> int:
        return len(self._queue) + int(self._inflight is not None)

    def _total_bytes(self) -> int:
        return self._queue_bytes + (
            self._inflight.size if self._inflight is not None else 0
        )

    @staticmethod
    def _reject(frame: _Frame, code: int) -> None:
        for waiter in frame.waiters:
            if not waiter.done():
                waiter.set_exception(WebSocketDisconnect(code))


def send_to_connection(ws: Any, payload: str, loop: asyncio.AbstractEventLoop | None) -> bool:
    """Thread-safe enqueue for managed sockets; retain test-double compatibility."""
    enqueue = getattr(ws, "enqueue_text_threadsafe", None)
    if callable(enqueue):
        return bool(enqueue(payload))
    if loop is None or loop.is_closed():
        return False
    try:
        asyncio.run_coroutine_threadsafe(ws.send_text(payload), loop)
    except Exception:
        return False
    return True
