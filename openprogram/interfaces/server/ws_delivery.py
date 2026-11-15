"""Bounded, single-sender delivery for one WebSocket connection."""

from __future__ import annotations

import logging
import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Literal

from starlette.websockets import WebSocketDisconnect

_log = logging.getLogger(__name__)


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
    "frontend_build",
    "functions_list",
    "jobs_list",
    "permission_rules",
    "provider_info",
    "sessions_list",
    "settings",
    "working_dirs",
    "worktrees_list",
    "worker_capabilities",
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
    kind: Literal["critical", "append", "replace", "snapshot", "control", "transcript"]
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

    # A correlated reply acknowledges one request. A later broadcast must
    # never replace it, even when both frames contain the same snapshot.
    request_id = data.get("request_id") or envelope.get("request_id")
    if request_id:
        return "critical", (frame_type, "reply", str(request_id))

    if frame_type == "transcript_update":
        return "transcript", (frame_type, session_id)
    if frame_type == "branches_changed":
        return "replace", (frame_type, session_id)
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
        if response_type == "execution_stream":
            op = str(data.get("op") or "")
            node_id = str(data.get("node_id") or entity_id)
            if op in {"snapshot", "node_finished", "sync_required", "attempt_started",
                      "attempt_finished", "block_started", "block_finished"}:
                return "critical", (frame_type, "execution_stream", op, session_id, node_id)
            if op == "block_delta":
                block_id = str(data.get("block_id") or "")
                return "append", (
                    frame_type, "execution_stream", "block_delta",
                    session_id, node_id, block_id,
                )
            return "critical", (frame_type, "execution_stream", op, session_id, node_id)
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
        if frame_type == "permission_rules":
            return "snapshot", (frame_type, str(data.get("project_id") or ""))
        return "snapshot", (frame_type, session_id)
    if (
        "error" in frame_type
        or frame_type.startswith("question.")
        or frame_type.endswith("_result")
        or frame_type in {"chat_ack", "operation_error"}
    ):
        return "critical", (frame_type, session_id, entity_id)
    return "critical", (frame_type or "unknown", session_id, entity_id)


def _text_frames_continue(old_data: dict, new_data: dict) -> bool:
    """Whether a text frame starts exactly where the one before it ends.

    Merging across a gap would hide the gap from the offset check.
    """
    if "offset" not in old_data and "offset" not in new_data:
        return True
    if "offset" not in old_data or "offset" not in new_data:
        return False
    text = str(old_data["event"].get("text") or "")
    return (
        old_data.get("row_id") == new_data.get("row_id")
        and old_data.get("item_id") == new_data.get("item_id")
        and int(new_data["offset"]) == int(old_data["offset"]) + len(text.encode("utf-16-le", "surrogatepass")) // 2
    )


def _merge_adjacent(previous: _Frame, current: _Frame) -> _Frame | None:
    if previous.kind != "append" or previous.key != current.key:
        return None
    try:
        old = json.loads(previous.payload)
        new = json.loads(current.payload)
        old_data = old["data"]
        new_data = new["data"]
        if old_data.get("type") == "execution_stream" and old_data.get("op") == "block_delta":
            # Merge only same-block continuous revision interval.
            if (
                old_data.get("block_id") == new_data.get("block_id")
                and old_data.get("node_id") == new_data.get("node_id")
                and int(new_data.get("base_revision") or -1)
                == int(old_data.get("revision") or -2)
            ):
                old_data["delta"] = str(old_data.get("delta") or "") + str(
                    new_data.get("delta") or ""
                )
                old_data["revision"] = new_data.get("revision")
                payload = json.dumps(old, ensure_ascii=False, separators=(",", ":"))
            else:
                return None
        else:
            old_event = old_data["event"]
            new_event = new_data["event"]
            if not _text_frames_continue(old_data, new_data):
                return None
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


MAX_PARKED_FRAMES = 256


_V3_DROPPED_STREAM_EVENTS = {"tool_use", "tool_result"}
_V3_DROPPED_RESPONSES = {"tree_update", "user_message"}


def frame_is_for_older_clients(ws: Any, payload: str) -> bool:
    """A row-writing frame a version 3 socket now learns from transcript updates."""
    if getattr(ws, "_history_protocol", 0) != 3:
        return False
    if '"chat_response"' not in payload:
        return False
    try:
        envelope = json.loads(payload)
    except ValueError:
        return False
    if not isinstance(envelope, dict):
        return False
    data = envelope.get("data")
    if envelope.get("type") != "chat_response" or not isinstance(data, dict):
        return False
    if data.get("type") in _V3_DROPPED_RESPONSES:
        return True
    event = data.get("event")
    return data.get("type") == "stream_event" and isinstance(event, dict) and event.get("type") in _V3_DROPPED_STREAM_EVENTS


def is_row_writing_frame(envelope: dict) -> bool:
    """A frame a transcript read must not be overtaken by."""
    frame_type = envelope.get("type")
    if frame_type in ("transcript_update", "transcript_gap"):
        return True
    data = envelope.get("data")
    if frame_type != "chat_response" or not isinstance(data, dict):
        return False
    return data.get("type") == "stream_event"


class ReadParking:
    """Holds a session's row-writing frames while a read of it is being sent."""

    def __init__(self, deliver: Callable[[str], Any]) -> None:
        self._deliver = deliver
        self._depth: dict[str, int] = {}
        self._frames: dict[str, list[_Frame]] = {}
        self._overflowed: set[str] = set()

    def begin(self, session_id: str) -> Callable[[], None]:
        """Park from now; the returned call ends this read, once."""
        self._depth[session_id] = self._depth.get(session_id, 0) + 1
        ended = False

        def end() -> None:
            nonlocal ended
            if not ended:
                ended = True
                self._end(session_id)

        return end

    def offer(self, payload: str) -> bool:
        """True when this frame is parked instead of sent."""
        if not self._depth:
            return False
        try:
            envelope = json.loads(payload)
        except ValueError:
            return False
        if not isinstance(envelope, dict) or not is_row_writing_frame(envelope):
            return False
        data = envelope.get("data") or {}
        session_id = str(data.get("session_id") or envelope.get("session_id") or "")
        if self._depth.get(session_id, 0) <= 0:
            return False
        kind, key = _frame_metadata(payload)
        frame = _Frame(payload, len(payload.encode("utf-8")), kind, key, time.monotonic(), [])  # type: ignore[arg-type]
        parked = self._frames.setdefault(session_id, [])
        merged = _merge_adjacent(parked[-1], frame) if parked and kind == "append" else None
        if merged is not None:
            parked[-1] = merged
        elif session_id not in self._overflowed:
            parked.append(frame)
            if len(parked) > MAX_PARKED_FRAMES:
                self._overflowed.add(session_id)
                parked.clear()
        return True

    def _end(self, session_id: str) -> None:
        depth = self._depth.get(session_id, 0) - 1
        if depth > 0:
            self._depth[session_id] = depth
            return
        self._depth.pop(session_id, None)
        frames = self._frames.pop(session_id, [])
        if session_id in self._overflowed:
            self._overflowed.discard(session_id)
            self._deliver(json.dumps({"type": "transcript_gap", "data": {"session_id": session_id}}))
            return
        for frame in frames:
            self._deliver(frame.payload)

    def clear(self) -> None:
        self._depth.clear()
        self._frames.clear()
        self._overflowed.clear()


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
        "_parking",
        "_transcript_gaps",
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
        object.__setattr__(self, "_parking", ReadParking(self._enqueue))
        object.__setattr__(self, "_transcript_gaps", set())

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
        if getattr(self, "_history_protocol", 0) == 3:
            try:
                envelope = json.loads(payload)
                data = envelope.get("data") or {}
                if (envelope.get("type") == "branches_list" and isinstance(data, dict)
                        and data.get("session_id") != getattr(self, "_history_graph_session", None)):
                    data.pop("graph", None)
                    payload = json.dumps(envelope, ensure_ascii=False, default=str)
            except (ValueError, AttributeError):
                pass
        if (getattr(self, "_history_protocol", 0) == 3
                and len(payload.encode("utf-8")) > 128 * 1024):
            try:
                kind = json.loads(payload).get("type")
            except (ValueError, AttributeError):
                kind = None
            if kind in {"session_loaded", "session_history_page", "sub_agent_transcript", "branches_list", "full_tool_output"}:
                transfer = uuid.uuid4().hex
                for index, start in enumerate(range(0, len(payload), 16 * 1024)):
                    end = start + 16 * 1024
                    fragment = json.dumps({"type": "history_fragment", "data": {
                        "id": transfer, "index": index, "text": payload[start:end],
                        "final": end >= len(payload),
                    }})
                    await self._send_frame(fragment)
                return
        await self._send_frame(payload)

    async def _send_frame(self, payload: str) -> None:
        if self._closing:
            raise WebSocketDisconnect(1013)
        waiter = self._loop.create_future()
        if not self._enqueue(payload, waiter):
            if not waiter.done():
                waiter.set_exception(WebSocketDisconnect(1013))
        await waiter

    def park_read(self, session_id: str) -> Callable[[], None]:
        """Park this session's row frames; call the result when the read is queued."""
        return self._parking.begin(session_id)

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
        if self._parking.offer(payload):
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
            return True
        if frame_is_for_older_clients(self, payload):
            if waiter is not None and not waiter.done():
                waiter.set_result(None)
            return True
        kind, key = _frame_metadata(payload)
        frame = _Frame(
            payload=payload,
            size=len(payload.encode("utf-8")),
            kind=kind,  # type: ignore[arg-type]
            key=key,
            enqueued_at=time.monotonic(),
            waiters=[waiter] if waiter is not None else [],
        )

        if kind == "transcript":
            return self._enqueue_transcript(frame)

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

    def _enqueue_transcript(self, frame: _Frame) -> bool:
        session_id = frame.key[1]
        if session_id in self._transcript_gaps:
            self.dropped += 1
            self._resolve(frame)
            return True
        if (
            self._total_frames() >= NORMAL_QUEUE_FRAMES
            or self._total_bytes() + frame.size > NORMAL_QUEUE_BYTES
        ):
            self._resolve(frame)
            self._collapse_to_gap(session_id)
            return True
        self._queue.append(frame)
        self._queue_bytes += frame.size
        self._wake.set()
        return True

    def _collapse_to_gap(self, session_id: str) -> None:
        """Replace a session's queued updates with the one frame that makes it read."""
        kept: list[_Frame] = []
        for frame in self._queue:
            if frame.kind == "transcript" and frame.key[1] == session_id:
                self._queue_bytes -= frame.size
                self.dropped += 1
                self._resolve(frame)
            else:
                kept.append(frame)
        self._queue[:] = kept
        if session_id in self._transcript_gaps:
            return
        self._transcript_gaps.add(session_id)
        payload = json.dumps({"type": "transcript_gap", "data": {"session_id": session_id}})
        self._enqueue(payload)

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
            if frame.kind == "transcript":
                self._collapse_to_gap(frame.key[1])
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
            # The socket may already be closed; recovery only needs it gone.
            _log.debug("websocket close for recovery failed", exc_info=True)

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
                    if frame.key[:1] == ("transcript_gap",):
                        self._transcript_gaps.discard(frame.key[1])
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
                            # The socket may already be closed; recovery only
                            # needs it gone.
                            _log.debug(
                                "websocket close after persistence failure failed", exc_info=True)
                        return
                    finally:
                        self._inflight = None
        except asyncio.CancelledError:
            return

    async def stop(self) -> None:
        self._parking.clear()
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
