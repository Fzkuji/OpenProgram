from __future__ import annotations

import asyncio
import json

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.agent.authority import local_owner_authority
from openprogram.webui import server
from openprogram.webui.ws_actions import webtab


class _QueueWS:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.sent: list[dict] = []
        self.accepted = asyncio.Event()
        self.scope = {"state": {"authority": local_owner_authority()}}
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted.set()

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def receive_text(self) -> str:
        item = await self.incoming.get()
        if item is None:
            raise WebSocketDisconnect(1000)
        return item

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)
        await self.incoming.put(None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_loop", None)
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})
    monkeypatch.setattr("openprogram.paths.get_state_dir", lambda: tmp_path)
    webtab._pending.clear()
    webtab._bindings.clear()
    webtab._desktop_windows.clear()
    webtab._connection_revisions.clear()
    webtab._page_revisions.clear()
    webtab._restore_jobs.clear()
    yield
    for _rev, task in list(webtab._restore_jobs.values()):
        task.cancel()
    webtab._restore_jobs.clear()


def _list_commands(sent: list[dict]) -> list[dict]:
    return [
        frame for frame in sent
        if frame.get("type") == "webtab.command" and (frame.get("data") or {}).get("op") == "list"
    ]


def test_webtab_register_returns_before_list_and_rebinds_hidden_page(tmp_path, monkeypatch):
    from openprogram.browser_resources import BrowserResourceStore
    from openprogram.context.nodes import Call
    from openprogram.store import SessionNodeWriter
    from openprogram.store.session.session_store import SessionStore

    db = SessionStore(tmp_path / "sessions")
    writer = SessionNodeWriter(db, "parent")
    writer.append(Call(id="u1", role="user", predecessor="ROOT", seq=1))
    db.set_head("parent", "u1")
    store = BrowserResourceStore()
    store.retain(
        page_key="page:dead:1", window_id="main", tab_id="tab-hidden",
        title="arXiv", target="https://arxiv.org/abs/1",
        session_id="parent", conversation_session_id="parent", live=True,
    )
    store.mark_unavailable("page:dead:1")

    async def scenario() -> None:
        server._loop = asyncio.get_running_loop()
        ws = _QueueWS()
        handler = asyncio.create_task(server._websocket_handler(ws))
        await asyncio.wait_for(ws.accepted.wait(), 1)
        while len(ws.sent) < 2:
            await asyncio.sleep(0)
        await ws.incoming.put(json.dumps({
            "action": "webtab_register", "window_id": "main",
        }))
        await ws.incoming.put(json.dumps({"action": "list_sessions"}))
        deadline = asyncio.get_running_loop().time() + 2
        while asyncio.get_running_loop().time() < deadline:
            if _list_commands(ws.sent):
                break
            await asyncio.sleep(0.01)
        commands = _list_commands(ws.sent)
        assert commands, "register must dispatch list without waiting on the receive loop"
        req_id = commands[0]["data"]["req_id"]
        await ws.incoming.put(json.dumps({
            "action": "webtab_result",
            "req_id": req_id,
            "ok": True,
            "window_id": "main",
            "pages": [{
                "tab_id": "tab-hidden",
                "target_id": "target-live",
                "url": "https://arxiv.org/abs/1?utm=1#pdf",
                "title": "arXiv",
                "visible": False,
                "region": "background",
            }],
        }))
        deadline = asyncio.get_running_loop().time() + 3
        successor = None
        while asyncio.get_running_loop().time() < deadline:
            rows = store.list_rows(
                "parent", executions=[], parents={}, session_store=db,
            )
            live = [row for row in rows if row.get("status") == "open"]
            if live:
                successor = live[0]
                break
            await asyncio.sleep(0.02)
        assert successor is not None
        assert successor["tab_id"] == "tab-hidden"
        assert successor["control_state"] == "idle"
        assert successor["resource_id"] != "page:dead:1"
        assert store.get_resource("page:dead:1")["lifecycle"] == "superseded"
        await ws.incoming.put(None)
        await asyncio.wait_for(handler, 2)

    asyncio.run(scenario())


def test_duplicate_register_reuses_restore_job(monkeypatch, tmp_path):
    async def scenario() -> None:
        started = []

        def fake_restore(ws, window_id, revision=None):
            started.append((window_id, revision))
            import time
            time.sleep(0.2)

        monkeypatch.setattr(webtab, "restore_window_pages", fake_restore)
        loop = asyncio.get_running_loop()
        ws = object()
        webtab._connection_revisions[ws] = 7
        first = webtab.schedule_window_restore(ws, "main", 7)
        second = webtab.schedule_window_restore(ws, "main", 7)
        assert first is second
        await asyncio.wait_for(first, 1)
        assert started == [("main", 7)]
        del loop

    asyncio.run(scenario())
