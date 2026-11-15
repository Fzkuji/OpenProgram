"""Real server timings at 300,000 nodes; not part of routine test runs.

    python -m tests.support.transcript.server_benchmark            # compare
    python -m tests.support.transcript.server_benchmark --record   # write baseline

Later steps of the transcript model compare against the baseline file. Runs on
a real SessionStore and the real chat path in a temporary home. A full run
takes tens of minutes: writing 300,000 nodes one at a time is most of it.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import platform
import resource
import statistics
import subprocess
import sys
import tempfile
import time
import tracemalloc
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

BASELINE = Path(__file__).with_name("server_benchmark_baseline.json")
PAGE_READS = 20
REPLY_DELTAS = 400
LONG_TURN_STEPS = 2_000
# One process per core of constant small writes, each followed by an fsync.
BUSY_LOOP = """
import os, sys
path = os.path.join(sys.argv[1], f"busy-{os.getpid()}")
with open(path, "wb") as handle:
    while True:
        handle.write(os.urandom(4096)); handle.flush(); os.fsync(handle.fileno())
        sum(range(20000))
"""


@contextmanager
def _counted_fsync(*, skip: bool = False) -> Iterator[list[int]]:
    """Count fsync calls for the block; ``skip`` also leaves the disk alone."""
    real = os.fsync
    count = [0]

    def counting(descriptor: int) -> None:
        count[0] += 1
        if not skip:
            real(descriptor)

    os.fsync = counting
    try:
        yield count
    finally:
        os.fsync = real


def _populate(sessions, session_id: str, nodes: int) -> str:
    """User, reply and a tool row per turn: the mix a long working session has.

    Each row goes through the store's own append. Durability is not what is
    measured here, so these writes skip the fsync; the bytes on disk are the same.
    """
    head = "ROOT"
    with _counted_fsync(skip=True):
        for turn in range(nodes // 3 + (nodes % 3 > 0)):
            user = f"u{turn}"
            reply = f"{user}_reply"
            now = 1_700_000_000.0 + turn
            sessions.append_messages(session_id, [
                {"id": user, "role": "user", "content": f"question {turn} " * 8, "timestamp": now, "predecessor": head, "source": "web"},
                {"id": reply, "role": "assistant", "content": f"answer {turn} " * 40, "timestamp": now + 0.5, "predecessor": user,
                 "extra": json.dumps({"blocks": [
                     {"type": "text", "text": f"answer {turn} " * 40},
                     {"type": "tool", "tool": "lookup", "tool_call_id": f"call{turn}", "input": "{}", "result": "ok", "is_error": False},
                 ]})},
                {"id": f"{reply}_t_call{turn}", "role": "tool", "content": "ok " * 30, "timestamp": now + 0.6, "predecessor": reply,
                 "caller": reply, "function": "lookup", "tool_call_id": f"call{turn}"},
            ])
            head = reply
        sessions.set_head(session_id, head)
    return head


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _socket(sink: list):
    from tests.support.transcript.live_server import RecordingSocket

    return RecordingSocket(sink.append)


def _open(session_id: str, socket, **request) -> None:
    from openprogram.interfaces.server.ws_actions.session import handle_load_session

    asyncio.run(handle_load_session(socket, {"session_id": session_id, **request}))


def _rss_mb() -> float:
    # ru_maxrss is bytes on macOS and kilobytes on Linux.
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(value / (1024 * 1024 if platform.system() == "Darwin" else 1024), 1)


def _fresh_store(chat):
    """A new process's store, nothing cached, serving every read from here on."""
    from openprogram.runtime.context import session_db
    from openprogram.state.store.session import session_store as session_store_module
    from openprogram.state.store.session.session_store import SessionStore

    fresh = SessionStore(chat.sessions.root_path)
    session_db.default_db = lambda: fresh
    session_store_module.shared._default_store = fresh
    with chat.server._sessions_lock:
        chat.server._sessions.pop(chat.session_id, None)
    return fresh


class StreamedReply:
    """A provider reply that arrives as many small text deltas."""

    requires_credentials = False

    def __init__(self, count: int) -> None:
        self.deltas = [f"token {index:05d} " for index in range(count)]
        self.sent: list[float] = []

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        from openprogram.models.providers.types import (
            AssistantMessage,
            EventDone,
            EventStart,
            EventTextDelta,
            EventTextEnd,
            EventTextStart,
            TextContent,
        )

        def message(text: str) -> AssistantMessage:
            return AssistantMessage(content=[TextContent(text=text)], api=model.api, provider=model.provider,
                                    model=model.id, stop_reason="stop", timestamp=0)

        partial = message("")
        yield EventStart(partial=partial)
        yield EventTextStart(content_index=0, partial=partial)
        for delta in self.deltas:
            self.sent.append(time.perf_counter())
            yield EventTextDelta(content_index=0, delta=delta, partial=partial)
        full = "".join(self.deltas)
        yield EventTextEnd(content_index=0, content=full, partial=message(full))
        yield EventDone(reason="stop", message=message(full))


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))], 3)


def _measure_stream(root: Path, label: str) -> dict[str, float]:
    """One reply through the real chat path: provider delta to outgoing frame."""
    from openprogram.models.providers.catalog import api_registry
    from tests.support.transcript.live_server import _API_NAME, live_chat

    root.mkdir()
    reply = StreamedReply(REPLY_DELTAS)
    arrivals: list[tuple[float, int]] = []
    with live_chat(root) as chat:
        api_registry.register_api_provider(_API_NAME, reply)
        deliver = chat._frame

        def timed(frame: dict) -> None:
            data = frame.get("data") or {}
            event = data.get("event") or {}
            if data.get("type") == "stream_event" and event.get("type") == "text":
                arrivals.append((time.perf_counter(), len(event.get("text") or "")))
            deliver(frame)

        chat._frame = timed
        chat.socket.sink = timed
        with _counted_fsync() as fsyncs:
            chat.send("stream a long reply")
            chat.wait_finished()
    # A frame may carry several deltas; a delta is out once its last character is.
    latencies: list[float] = []
    sent_characters = 0
    frame = 0
    frame_characters = arrivals[0][1]
    for delta, at in zip(reply.deltas, reply.sent):
        sent_characters += len(delta)
        while frame_characters < sent_characters:
            frame += 1
            frame_characters += arrivals[frame][1]
        latencies.append((arrivals[frame][0] - at) * 1000)
    gaps = [(later[0] - earlier[0]) * 1000 for earlier, later in zip(arrivals, arrivals[1:])]
    return {
        f"delta_latency_{label}_median_ms": _percentile(latencies, 0.5),
        f"delta_latency_{label}_p95_ms": _percentile(latencies, 0.95),
        f"delta_gap_{label}_median_ms": _percentile(gaps, 0.5),
        f"delta_gap_{label}_p95_ms": _percentile(gaps, 0.95),
        f"fsync_per_reply_{label}": fsyncs[0],
    }


@contextmanager
def _busy_machine(directory: Path) -> Iterator[None]:
    """Every core kept busy with small durable writes, as on a working laptop."""
    directory.mkdir()
    workers = [subprocess.Popen([sys.executable, "-c", BUSY_LOOP, str(directory)]) for _ in range(os.cpu_count() or 4)]
    try:
        yield
    finally:
        for worker in workers:
            worker.kill()
            worker.wait()


def _measure_long_turn(root: Path) -> dict[str, float]:
    """One autonomous turn: a single reply carrying thousands of tool steps."""
    from tests.support.transcript.live_server import SESSION_ID, live_chat

    root.mkdir()
    blocks: list[dict] = []
    tool_rows: list[dict] = []
    reply = "long_reply"
    for step in range(LONG_TURN_STEPS):
        call = f"call{step}"
        blocks += [
            {"type": "text", "text": f"step {step} " * 100},
            {"type": "tool", "tool": "lookup", "tool_call_id": call, "input": "{}", "result": "x" * 4000, "is_error": False},
        ]
        tool_rows.append({"id": f"{reply}_t_{call}", "role": "tool", "content": "x" * 4000, "timestamp": 1_700_000_001.0 + step,
                          "predecessor": reply, "caller": reply, "function": "lookup",
                          "extra": json.dumps({"tool_use": {"tool_call_id": call}})})
    with live_chat(root) as chat:
        with _counted_fsync(skip=True):
            chat.sessions.append_messages(SESSION_ID, [
                {"id": "long", "role": "user", "content": "work for a long time", "timestamp": 1_700_000_000.0,
                 "predecessor": "ROOT", "source": "web"},
                {"id": reply, "role": "assistant", "content": "done", "timestamp": 1_700_000_000.5, "predecessor": "long",
                 "extra": json.dumps({"blocks": blocks})},
                *tool_rows,
            ])
            chat.sessions.set_head(SESSION_ID, reply)
        _fresh_store(chat)
        frames: list[dict] = []
        started = time.perf_counter()
        _open(SESSION_ID, _socket(frames))
        elapsed = _ms(started)
    return {"long_turn_open_ms": elapsed, "long_turn_reply_kib": round(len(json.dumps(frames, default=str)) / 1024, 1)}


def _view_memory(session_id: str) -> dict[str, float]:
    """What one open view holds on the server, and what its open needs at most.

    A view holds what closing it gives back. An open also replaces session-wide
    copies, which outlive the view, and sends frames, which it does not keep.
    """
    from openprogram.interfaces.server.transcript.turn_index import forget_views

    forget_views()
    tracemalloc.start()
    before, _ = tracemalloc.get_traced_memory()
    frames: list[dict] = []
    viewer = _socket(frames)
    _open(session_id, viewer)
    _, peak = tracemalloc.get_traced_memory()
    frames.clear()
    gc.collect()
    held, _ = tracemalloc.get_traced_memory()
    forget_views()
    gc.collect()
    closed, _ = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {"memory_per_view_kib": round((held - closed) / 1024, 1),
            "memory_open_peak_kib": round((peak - before) / 1024, 1)}


def measure(root: Path, nodes: int) -> dict:
    from openprogram.interfaces.server.transcript.turn_index import forget_views
    from openprogram.state.store.session.session_store import SessionStore
    from tests.support.transcript.live_server import SESSION_ID, live_chat

    results: dict[str, float] = {}
    results.update(_measure_stream(root / "stream-quiet", "quiet"))
    with _busy_machine(root / "busy"):
        results.update(_measure_stream(root / "stream-busy", "busy"))
    results.update(_measure_long_turn(root / "long-turn"))
    large = root / "large"
    large.mkdir()
    with live_chat(large) as chat:
        started = time.perf_counter()
        _populate(chat.sessions, SESSION_ID, nodes)
        results["populate_ms"] = _ms(started)

        # First open: a new process's store, nothing cached.
        fresh = _fresh_store(chat)
        reply: list[dict] = []
        reader = _socket(reply)
        started = time.perf_counter()
        _open(SESSION_ID, reader)
        results["first_open_ms"] = _ms(started)
        window = next(f for f in reply if f["type"] == "session_loaded")["data"]["window"]

        # Page reads against the view the open built.
        timings = []
        cursor = window["before"]
        for _ in range(PAGE_READS):
            page: list[dict] = []
            reader.sink = page.append
            started = time.perf_counter()
            _open(SESSION_ID, reader, history_before=cursor)
            timings.append(_ms(started))
            cursor = next(f for f in page if f["type"] == "session_history_page")["data"]["window"]["before"]
        results["page_read_median_ms"] = round(statistics.median(timings), 2)
        results["page_read_max_ms"] = round(max(timings), 2)

        # Evicted: the store dropped its cache and the server its conversation.
        fresh.invalidate_cache(SESSION_ID)
        with chat.server._sessions_lock:
            chat.server._sessions.pop(SESSION_ID, None)
        forget_views()
        reply = []
        reader.sink = reply.append
        started = time.perf_counter()
        _open(SESSION_ID, reader)
        results["evicted_open_ms"] = _ms(started)

        # Foreign write: another store writes, this one rebuilds on its next read.
        other = SessionStore(chat.sessions.root_path)
        head = fresh.get_session(SESSION_ID)["head_id"]
        other.append_message(SESSION_ID, {"id": "foreign-row", "role": "user", "content": "from another process",
                                          "timestamp": time.time(), "predecessor": head})
        other.set_head(SESSION_ID, "foreign-row")
        with chat.server._sessions_lock:
            chat.server._sessions.pop(SESSION_ID, None)
        reply = []
        reader.sink = reply.append
        started = time.perf_counter()
        _open(SESSION_ID, reader)
        results["foreign_write_rebuild_ms"] = _ms(started)
        loaded = next(f for f in reply if f["type"] == "session_loaded")["data"]
        if loaded["window"]["head_id"] != "foreign-row":
            raise AssertionError("the store did not rebuild after the foreign write")

        results.update(_view_memory(SESSION_ID))
        results["process_peak_rss_mb"] = _rss_mb()
    return results


def main() -> None:  # pragma: no cover - a command for people
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nodes", type=int, default=300_000)
    parser.add_argument("--record", action="store_true", help="write the baseline file")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="openprogram-benchmark-") as home:
        os.environ["HOME"] = home
        results = measure(Path(home), args.nodes)
    report = {"nodes": args.nodes, "python": platform.python_version(), "machine": platform.machine(),
              "system": platform.system(), "results": results}
    print(json.dumps(report, indent=2))  # noqa: T201 - command output
    if args.record:
        BASELINE.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    elif BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))["results"]
        for key, value in results.items():
            if base.get(key):
                print(f"{key}: {value} vs baseline {base[key]} ({value / base[key]:.2f}x)")  # noqa: T201 - command output


if __name__ == "__main__":
    main()
