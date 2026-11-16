"""Opening a long conversation paints at once, whatever its log weighs.

A conversation whose replies streamed for hours had a 731 MB log: every
streamed delta had rewritten its reply's whole trace. A worker's first open
of it folded the log three times (nodes, run rows, state files) and
decompressed every trace version, about 8 s on its own and 13 s beside the
start-up walk of every other conversation, while later frames on the
connection waited. The synthetic log here has the measured shape and size.
"""
from __future__ import annotations

import asyncio
import json
import random
import threading
import time

import pytest

from openprogram.interfaces.server.ws_actions import session as ws_session
from openprogram.state.store import SessionStore
from openprogram.state.store.session.files import session_log, session_state_file

SID = "long-chat"
#: The measured log: 731.6 MB; 13 replies patched 11,013 times with a
#: compressed trace, 577 node versions, 1,585 meta and 208 state lines,
#: 105 run-row lines and 451 tool results.
LOG_BYTES = 732 * 1024 * 1024
REPLIES = 13
TURNS = 280


def _packed(rng: random.Random, size: int) -> str:
    text = rng.randbytes(size // 2).hex()
    extra = json.dumps({"blocks": [{"type": "text", "text": text}], "stream_seq": size})
    return session_log.base64.b64encode(session_log.zlib.compress(extra.encode(), 1)).decode()


def _write_amplified_log(root) -> None:
    store = SessionStore(root)
    store.create_session(SID, "main")
    previous = None
    replies = []
    for turn in range(TURNS):
        user, reply = f"u{turn}", f"u{turn}_reply"
        store.append_message(SID, {"id": user, "role": "user", "content": f"question {turn}",
                                   "predecessor": previous})
        store.append_message(SID, {"id": reply, "role": "assistant", "content": f"answer {turn}",
                                   "predecessor": user})
        replies.append(reply)
        previous = reply
    store.update_session(SID, head_id=previous)
    for turn in range(208):
        store.state_file(SID, "todos.json").write_text(json.dumps([{"content": f"todo {turn}"}]))
    for turn in range(105):
        store.append_run_rows(SID, [{"table": "executions", "id": f"e{turn}"}])
    store.close()

    rng = random.Random(3)
    main = root / "default" / "chats" / SID / "main.jsonl"
    patched = replies[-REPLIES:]
    # A few versions of each reply's trace, written over and over as the
    # streaming path did, growing as the reply grew.
    versions = {reply: [_packed(rng, 8_000 + 24_000 * step) for step in range(1, 9)] for reply in patched}
    with open(main, "ab") as handle:
        written = main.stat().st_size
        n = 0
        while written < LOG_BYTES:
            reply = patched[n % REPLIES]
            packed = versions[reply][min(7, written * 8 // LOG_BYTES)]
            line = session_log.encode_record(session_log._next_timestamp(), "node.patch", {
                "id": reply, "set": {"output": f"partial {n}"}, "extra_zlib": packed})
            handle.write(line)
            written += len(line)
            n += 1
        # Each reply ends on its largest trace.
        for reply in patched:
            handle.write(session_log.encode_record(session_log._next_timestamp(), "node.patch", {
                "id": reply, "set": {"output": "final"}, "extra_zlib": versions[reply][-1]}))


class _WS:
    _history_protocol = 3

    def __init__(self) -> None:
        self.loaded_at = None
        self.frames: list[str] = []

    async def send_text(self, text: str) -> None:
        kind = json.loads(text)["type"]
        self.frames.append(kind)
        if kind == "session_loaded" and self.loaded_at is None:
            self.loaded_at = time.perf_counter()


def _open_cold(root, monkeypatch) -> tuple[float, _WS]:
    """A worker's first open: a new store and nothing of the session cached."""
    from openprogram.interfaces.server import server
    store = SessionStore(root)
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    with session_log._meta_cache_lock:
        session_log._meta_cache.clear()
    session_state_file.forget_directory(store._session_dir(SID))
    ws = _WS()
    started = time.perf_counter()
    try:
        asyncio.run(ws_session.handle_load_session(ws, {
            "action": "load_session", "session_id": SID, "request_id": "r", "history_version": 3}))
    finally:
        from openprogram.interfaces.server.transcript import publisher
        publisher.release(ws)
        with server._sessions_lock:
            server._sessions.pop(SID, None)
        store.close()
    assert ws.loaded_at is not None, ws.frames
    return ws.loaded_at - started, ws


@pytest.mark.slow
def test_a_long_conversation_opens_in_well_under_a_second(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "refresh_context_stats", lambda _sid: None)
    root = tmp_path / "projects"
    _write_amplified_log(root)
    assert (root / "default" / "chats" / SID / "main.jsonl").stat().st_size >= LOG_BYTES

    unpacked = []
    original = session_log.unpack_extra
    monkeypatch.setattr(session_log, "unpack_extra", lambda packed: (unpacked.append(1), original(packed))[1])
    # The first open after this change reads the whole log once; it never
    # unpacks a trace a later line replaced.
    first, _ = _open_cold(root, monkeypatch)
    assert len(unpacked) <= REPLIES
    assert (root / "default" / "chats" / SID / "checkpoint" / "fold.json").is_file()

    # Every later cold open reads the checkpoint, not the log: 8 s before.
    took, ws = _open_cold(root, monkeypatch)
    assert took < 1.0, f"session_loaded after {took:.2f}s (first open {first:.2f}s)"
    assert ws.frames.index("session_loaded") == 0


def test_reading_a_conversation_for_the_first_time_holds_no_other_lookup(monkeypatch):
    """The first read of a long conversation ran under the lock every
    conversation lookup takes."""
    from openprogram.interfaces.server import server
    release = threading.Event()
    reading = threading.Event()

    class SlowStore:
        def get_branch(self, session_id):
            if session_id == "slow-chat":
                reading.set()
                release.wait(5)
            return []

        def get_session(self, session_id):
            return {"agent_id": "main"}

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: SlowStore())
    with server._sessions_lock:
        server._sessions["open-chat"] = {"id": "open-chat"}
    worker = threading.Thread(target=server._get_or_create_session, args=("slow-chat",))
    worker.start()
    try:
        assert reading.wait(5)
        started = time.perf_counter()
        assert server._get_or_create_session("open-chat") == {"id": "open-chat"}
        assert time.perf_counter() - started < 1.0
    finally:
        release.set()
        worker.join(5)
        with server._sessions_lock:
            server._sessions.pop("open-chat", None)
            server._sessions.pop("slow-chat", None)
    assert not worker.is_alive()
