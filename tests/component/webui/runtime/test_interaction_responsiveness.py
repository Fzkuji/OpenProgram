"""Controlled event-loop regressions for WebSocket hydration helpers."""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

from openprogram.state.store.session.graph.builder import build_session_graph
from openprogram.runtime.conversation.session_config import SessionRunConfig
from openprogram.interfaces.server.ws_actions.chat import _persist_attachments_async
from openprogram.interfaces.server.ws_actions.session import _session_io
from openprogram.interfaces.server.ws_actions.session import handle_load_session


def test_attachment_io_keeps_heartbeat_and_waits_before_cancel(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def persist(*_args):
        started.set()
        assert release.wait(2)
        finished.set()
        return "stored"

    monkeypatch.setattr(
        "openprogram.interfaces.server.ws_actions.chat.attachments._persist_attachments", persist,
    )

    async def scenario():
        beats = 0
        task = asyncio.create_task(_persist_attachments_async("s", [], "x"))
        while not started.is_set():
            await asyncio.sleep(0)
        for _ in range(5):
            await asyncio.sleep(0)
            beats += 1
        assert beats == 5
        task.cancel()
        await asyncio.sleep(0)
        assert not finished.is_set()
        assert not task.done()
        task.cancel()
        await asyncio.sleep(0)
        assert not finished.is_set()
        assert not task.done()
        release.set()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert finished.is_set()

    asyncio.run(scenario())


def test_graph_builder_uses_captured_messages_without_second_history_read(
    tmp_path, monkeypatch
):
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u", "role": "user", "content": "q"})
    store.append_message(
        "s", {"id": "a", "role": "assistant", "content": "r", "predecessor": "u"}
    )
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    captured = store.get_messages("s")
    calls = 0
    original = store.get_messages

    def counted(session_id, *, limit=None):
        nonlocal calls
        calls += 1
        return original(session_id, limit=limit)

    monkeypatch.setattr(store, "get_messages", counted)
    graph = build_session_graph("s", "a", messages=captured)
    assert any(row["id"] == "a" for row in graph)
    assert calls == 0


def test_session_store_read_keeps_event_loop_heartbeat(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def slow_read():
        started.set()
        assert release.wait(2)
        return {"head_id": "h1"}

    async def scenario():
        task = asyncio.create_task(_session_io(slow_read))
        while not started.is_set():
            await asyncio.sleep(0)
        beats = 0
        for _ in range(5):
            await asyncio.sleep(0)
            beats += 1
        assert beats == 5
        release.set()
        assert await task == {"head_id": "h1"}

    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["read", "aggregate", "runtime", "page", "page_snapshot"])
def test_handle_load_session_offloads_slow_history_and_keeps_new_head(
    tmp_path, monkeypatch, phase
):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.interfaces.server import server

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u", "role": "user", "content": "q"})
    store.append_message("s", {
        "id": "a", "role": "assistant", "content": "r", "predecessor": "u",
    })
    original = store.get_messages
    old_rows = original("s")
    started = threading.Event()
    release = threading.Event()

    def slow_messages(session_id, *, limit=None):
        started.set()
        assert release.wait(2)
        return list(old_rows)

    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    if phase == "read":
        monkeypatch.setattr(store, "get_messages", slow_messages)
    else:
        from importlib import import_module
        module_name, name = {
            "aggregate": ("openprogram.interfaces.server.persistence", "aggregate_tool_messages"),
            "runtime": ("openprogram.interfaces.server.ws_actions.session.transcript", "_rebuild_runtime_cards"),
            "page": ("openprogram.interfaces.server.transcript.reader", "first_read"),
            "page_snapshot": ("openprogram.interfaces.server.transcript.reader", "first_read"),
        }[phase]
        module = import_module(module_name)
        original_phase = getattr(module, name)

        def slow_phase(*args, **kwargs):
            started.set()
            assert release.wait(2)
            result = original_phase(*args, **kwargs)
            if phase == "page_snapshot":
                # The publisher refreshes this same cached view after the page
                # captured its window, before the event loop serializes it.
                from openprogram.interfaces.server.transcript.turn_index import get_view
                assert get_view(store, "s") is args[1]
                assert args[1].head_id == "new"
            return result

        monkeypatch.setattr(module, name, slow_phase)
    monkeypatch.setattr(
        "openprogram.interfaces.server.ws_actions.session.load.reconcile_session_projection",
        lambda _sid: None,
        raising=False,
    )
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "_sessions", {
        "s": {"id": "s", "messages": [], "head_id": None},
    })
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_config.load_session_run_config",
        lambda _sid: SessionRunConfig(),
    )
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_config.project_defaults",
        lambda _sid: {},
    )
    monkeypatch.setattr(
        "openprogram.runtime.conversation.session_config.permission_from_config",
        lambda _cfg, default=None: default or "ask",
    )
    monkeypatch.setattr(
        "openprogram.runtime.permissions.permission_state",
        lambda _sid: {"version": 1},
    )
    monkeypatch.setattr("openprogram.host.sandbox.ui_state", lambda _enabled: {})
    monkeypatch.setattr(
        "openprogram.state.store.session.graph.builder.build_session_graph",
        lambda *_args, **_kwargs: [],
    )

    class WS:
        _history_protocol = 3

        def __init__(self):
            self.frames = []

        async def send_text(self, payload):
            self.frames.append(payload)

    async def scenario():
        from openprogram.interfaces.server.transcript import publisher
        from openprogram.interfaces.server.transcript.turn_index import forget_views

        # No publisher from a previous test owns this read. The snapshot case
        # below advances its shared view at a deterministic handoff instead.
        publisher.stop()
        forget_views()
        ws = WS()
        task = asyncio.create_task(handle_load_session(ws, {"session_id": "s"}))
        while not started.is_set():
            await asyncio.sleep(0)
        heartbeat = 0
        for _ in range(5):
            await asyncio.sleep(0)
            heartbeat += 1
        assert heartbeat == 5
        server._sessions["s"]["head_id"] = "newer"
        store.append_message("s", {
            "id": "new", "role": "assistant", "content": "late",
            "predecessor": "a",
        })
        store.set_head("s", "new")
        release.set()
        await task
        frames = [json.loads(frame) for frame in ws.frames]
        [loaded] = [frame["data"] for frame in frames if frame["type"] == "session_loaded"]
        assert server._sessions["s"]["head_id"] == "newer"
        assert loaded["window"]["head_id"] == "a"
        assert loaded["head_id"] == loaded["window"]["head_id"]
        publisher.release(ws)

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["session_loaded", "full_tool_output"])
def test_negotiated_history_delivery_does_not_disconnect_on_large_snapshot(kind):
    import json
    from openprogram.interfaces.server.ws_delivery import QueuedWebSocket

    class Raw:
        _history_protocol = 3
        def __init__(self):
            self.frames = []
            self.closed = False
        async def send_text(self, text):
            self.frames.append(text)
        async def close(self, **_kwargs):
            self.closed = True

    async def scenario():
        raw = Raw()
        ws = QueuedWebSocket(raw, asyncio.get_running_loop())
        ws.start()
        payload = json.dumps({'type': kind, 'data': {'id': 's', 'messages': ['x' * (5 * 1024 * 1024)]}})
        try:
            await ws.send_text(payload)
            assert not raw.closed
            assert max(len(f.encode()) for f in raw.frames) < 256 * 1024
            fragments = [json.loads(f)['data'] for f in raw.frames]
            assert ''.join(f['text'] for f in fragments) == payload
            assert fragments[-1]['final']
        finally:
            await ws.stop()
    asyncio.run(scenario())


def test_branch_graph_preparation_keeps_event_loop_responsive(monkeypatch):
    from openprogram.interfaces.server.ws_actions import branch

    started = threading.Event()
    release = threading.Event()

    def slow_payload(sid):
        started.set()
        assert release.wait(2)
        return {'session_id': sid, 'graph': []}

    monkeypatch.setattr(branch, 'build_branches_payload', slow_payload)

    class WS:
        frames = []

        async def send_text(self, payload):
            self.frames.append(payload)

    async def scenario():
        ws = WS()
        task = asyncio.create_task(branch.handle_list_branches(ws, {'session_id': 's'}))
        try:
            while not started.is_set():
                await asyncio.sleep(0)
            assert not task.done()
            assert not ws.frames
        finally:
            release.set()
            await task
        assert len(ws.frames) == 1

    asyncio.run(scenario())
