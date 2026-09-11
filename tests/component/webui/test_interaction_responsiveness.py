"""Controlled event-loop regressions for WebSocket hydration helpers."""

from __future__ import annotations

import asyncio
import threading

import pytest

from openprogram.webui.graph_builder import build_session_graph
from openprogram.webui.ws_actions.session import _session_io
from openprogram.webui.ws_actions.session import handle_load_session


def test_graph_builder_uses_captured_messages_without_second_history_read(
    tmp_path, monkeypatch
):
    from openprogram.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    store.create_session("s", "main", title="t")
    store.append_message("s", {"id": "u", "role": "user", "content": "q"})
    store.append_message(
        "s", {"id": "a", "role": "assistant", "content": "r", "predecessor": "u"}
    )
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
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


def test_handle_load_session_offloads_slow_history_and_keeps_new_head(
    tmp_path, monkeypatch
):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import server

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

    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: store)
    monkeypatch.setattr(store, "get_messages", slow_messages)
    monkeypatch.setattr(
        "openprogram.webui.ws_actions.session.reconcile_session_projection",
        lambda _sid: None,
        raising=False,
    )
    monkeypatch.setattr(server, "_get_provider_info", lambda _sid=None: {})
    monkeypatch.setattr(server, "_is_run_active", lambda _sid: False)
    monkeypatch.setattr(server, "_sessions", {
        "s": {"id": "s", "messages": [], "head_id": None},
    })
    monkeypatch.setattr(
        "openprogram.agent.session_config.load_session_run_config",
        lambda _sid: type("Cfg", (), {
            "tools_enabled": None, "tools_override": None,
            "thinking_effort": None, "permission_mode": None,
            "additional_working_dirs": [], "sandbox_enabled": None,
        })(),
    )
    monkeypatch.setattr(
        "openprogram.agent.session_config.project_defaults",
        lambda _sid: {},
    )
    monkeypatch.setattr(
        "openprogram.agent.session_config.permission_from_config",
        lambda _cfg, default=None: default or "ask",
    )
    monkeypatch.setattr(
        "openprogram.agent.permissions.permission_state",
        lambda _sid: {"version": 1},
    )
    monkeypatch.setattr("openprogram.sandbox.ui_state", lambda _enabled: {})
    monkeypatch.setattr(
        "openprogram.webui.graph_builder.build_session_graph",
        lambda *_args, **_kwargs: [],
    )

    class WS:
        def __init__(self):
            self.frames = []

        async def send_text(self, payload):
            self.frames.append(payload)

    async def scenario():
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
        assert ws.frames
        assert '"type": "session_loaded"' in ws.frames[0]
        assert server._sessions["s"]["head_id"] == "newer"
        assert '"head_id": "a"' in ws.frames[0]
        assert '"head_id": "new"' not in ws.frames[0]

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["session_loaded", "full_tool_output"])
def test_negotiated_history_delivery_does_not_disconnect_on_large_snapshot(kind):
    import json
    from openprogram.webui.ws_delivery import QueuedWebSocket

    class Raw:
        _history_protocol = 1
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
