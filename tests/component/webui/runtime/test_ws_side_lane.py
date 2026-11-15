"""A slow read-only action does not hold the connection's other frames.

Once every action on a connection ran in order, so a slow read (a
folder-size walk, a file search) left the user's Send with no bubble and no
``chat_ack`` for as long. Read-only actions whose reply carries its request
id now run beside the ordered lane; frames of the same action still answer
in the order sent.
"""
from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from starlette.websockets import WebSocketDisconnect

from openprogram.interfaces.server import server
from openprogram.interfaces.server.server_runtime import websocket_dispatch
from openprogram.runtime.permissions.authority import owner_authority


class _WS:
    def __init__(self, frames, until: asyncio.Event):
        self.frames = [json.dumps(f) for f in frames]
        self.until = until
        self.sent: list[dict] = []
        self.scope = {"state": {"authority": owner_authority("owner/install/0123456789abcdef")}}

    async def accept(self):
        pass

    async def send_text(self, text):
        self.sent.append(json.loads(text))

    async def receive_text(self):
        if self.frames:
            return self.frames.pop(0)
        # Stay connected until the test has seen what it waits for.
        await asyncio.wait_for(self.until.wait(), 5)
        raise WebSocketDisconnect(1000)


def _read(action: str, **extra) -> dict:
    return {"action": action, "request_id": str(uuid.uuid4()), **extra}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(server, "_ws_connections", [])
    monkeypatch.setattr(server, "_discover_functions", lambda: [])
    monkeypatch.setattr(server, "_get_provider_info", lambda: {})
    monkeypatch.setattr(
        "openprogram.operations.self_update.verification.ui_checks.permits_ws_command",
        lambda ws, cmd: True)


def test_a_chat_sent_while_a_slow_read_runs_is_admitted_at_once(monkeypatch):
    events: list[str] = []

    async def exercise():
        chat_seen = asyncio.Event()
        done = asyncio.Event()

        async def slow_list(ws, cmd):
            events.append("list started")
            # Held until the chat behind it is handled: a connection that
            # ran frames in order would wait here for the full timeout.
            await asyncio.wait_for(chat_seen.wait(), 5)
            events.append("list answered")
            done.set()

        async def chat(ws, cmd):
            events.append("chat admitted")
            chat_seen.set()

        monkeypatch.setitem(server.WS_ACTIONS, "project_file_search", slow_list)
        monkeypatch.setitem(server.WS_ACTIONS, "chat", chat)
        ws = _WS([_read("project_file_search"), {"action": "chat", "session_id": "s"}], done)
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)

    asyncio.run(exercise())
    assert sorted(events[:2]) == ["chat admitted", "list started"]
    assert events[2:] == ["list answered"]


def test_frames_of_one_read_action_answer_in_order_and_ordered_actions_wait(monkeypatch):
    events: list[str] = []

    async def exercise():
        done = asyncio.Event()

        async def listing(ws, cmd):
            # The first listing is the slower one; it still answers first.
            await asyncio.sleep(0.05 if cmd["n"] == 1 else 0)
            events.append(f"list {cmd['n']}")
            if cmd["n"] == 2:
                done.set()

        async def bind(ws, cmd):
            await asyncio.sleep(0.02)
            events.append("bind")

        async def chat(ws, cmd):
            events.append("chat")

        monkeypatch.setitem(server.WS_ACTIONS, "project_file_tree", listing)
        monkeypatch.setitem(server.WS_ACTIONS, "set_session_project", bind)
        monkeypatch.setitem(server.WS_ACTIONS, "chat", chat)
        ws = _WS([_read("project_file_tree", n=1), {"action": "set_session_project"},
                  {"action": "chat"}, _read("project_file_tree", n=2)], done)
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)

    asyncio.run(exercise())
    # bind and chat keep their order; the listings keep theirs.
    assert events.index("bind") < events.index("chat")
    assert events.index("list 1") < events.index("list 2")
    assert events.index("chat") < events.index("list 1")


def test_a_failing_read_is_answered_with_an_operation_error(monkeypatch):
    async def exercise():
        done = asyncio.Event()

        async def broken(ws, cmd):
            raise RuntimeError("boom")

        async def chat(ws, cmd):
            pass

        monkeypatch.setitem(server.WS_ACTIONS, "project_file_info", broken)
        monkeypatch.setitem(server.WS_ACTIONS, "chat", chat)
        monkeypatch.setattr(
            "openprogram.interfaces.server.ws_errors.persist_operation_error_frame",
            lambda ws, frame: None)
        ws = _WS([_read("project_file_info"), {"action": "chat"}], done)
        send = ws.send_text

        async def send_text(text):
            await send(text)
            if json.loads(text).get("type") == "operation_error":
                done.set()
        ws.send_text = send_text
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)
        return ws.sent

    sent = asyncio.run(exercise())
    errors = [f["data"] for f in sent if f.get("type") == "operation_error"]
    assert [(e["action"], e["code"]) for e in errors] == [("project_file_info", "handler_error")]


def test_list_projects_keeps_the_connection_order(monkeypatch):
    """The web matches ``projects_list`` by type: a listing still running
    when a ``create_project`` sent after it answers would be taken as the
    reply to the re-list that follows, and the new project would vanish."""
    events: list[str] = []

    async def exercise():
        done = asyncio.Event()

        async def listing(ws, cmd):
            await asyncio.sleep(0.05)
            events.append("listed")

        async def create(ws, cmd):
            events.append("created")
            done.set()

        monkeypatch.setitem(server.WS_ACTIONS, "list_projects", listing)
        monkeypatch.setitem(server.WS_ACTIONS, "create_project", create)
        ws = _WS([{"action": "list_projects"}, {"action": "create_project"}], done)
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)

    asyncio.run(exercise())
    assert events == ["listed", "created"]


def test_a_flood_of_slow_reads_is_held_back_by_the_socket(monkeypatch):
    """Side reads in flight are bounded; the receive loop stops reading
    until one finishes instead of piling up a task per frame."""
    monkeypatch.setattr(websocket_dispatch, "MAX_SIDE_TASKS", 3)
    peak = {"reads": 0}

    async def exercise():
        gate = asyncio.Event()
        done = asyncio.Event()
        answered: list[int] = []
        frames = [_read(action, n=i) for i in range(6)
                  for action in ("project_file_search", "project_file_info")]
        total = len(frames)

        async def slow(ws, cmd):
            await gate.wait()
            answered.append(cmd["n"])
            if len(answered) == total:
                done.set()

        monkeypatch.setitem(server.WS_ACTIONS, "project_file_search", slow)
        monkeypatch.setitem(server.WS_ACTIONS, "project_file_info", slow)
        ws = _WS(frames, done)
        handler = asyncio.create_task(websocket_dispatch._websocket_handler(ws))
        await asyncio.sleep(0.05)
        peak["reads"] = total - len(ws.frames)
        gate.set()
        await asyncio.wait_for(handler, 10)
        return answered

    answered = asyncio.run(exercise())
    # Three in flight, the fourth frame read and waiting for a free slot.
    assert peak["reads"] == 4
    assert len(answered) == 12


def test_a_chat_sent_while_a_long_conversation_opens_is_admitted_at_once(monkeypatch):
    """The first read of a long conversation once held a Send or Stop sent
    after it for as long as the read took."""
    events: list[str] = []

    async def exercise():
        chat_seen = asyncio.Event()
        done = asyncio.Event()

        async def slow_load(ws, cmd):
            events.append("load started")
            await asyncio.wait_for(chat_seen.wait(), 5)
            events.append("load answered")
            done.set()

        async def chat(ws, cmd):
            events.append("chat admitted")
            chat_seen.set()

        monkeypatch.setitem(server.WS_ACTIONS, "load_session", slow_load)
        monkeypatch.setitem(server.WS_ACTIONS, "chat", chat)
        ws = _WS([_read("load_session", session_id="s", history_version=3),
                  {"action": "chat", "session_id": "s"}], done)
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)

    asyncio.run(exercise())
    assert sorted(events[:2]) == ["chat admitted", "load started"]
    assert events[2:] == ["load answered"]


def test_a_load_sees_ordered_frames_before_it_and_loads_answer_in_order(monkeypatch):
    """A checkout sent before a load is applied before the load reads, and
    two loads (a page, then the next) answer in the order sent."""
    events: list[str] = []

    async def exercise():
        done = asyncio.Event()

        async def checkout(ws, cmd):
            await asyncio.sleep(0.03)
            events.append("checkout")

        async def load(ws, cmd):
            events.append(f"load {cmd['n']} started")
            await asyncio.sleep(0.05 if cmd["n"] == 1 else 0)
            events.append(f"load {cmd['n']} answered")
            if cmd["n"] == 2:
                done.set()

        async def chat(ws, cmd):
            # The connection already reads turns: the load said so first.
            events.append(f"chat protocol {getattr(ws, '_history_protocol', 0)}")

        monkeypatch.setitem(server.WS_ACTIONS, "checkout_branch", checkout)
        monkeypatch.setitem(server.WS_ACTIONS, "load_session", load)
        monkeypatch.setitem(server.WS_ACTIONS, "chat", chat)
        ws = _WS([{"action": "checkout_branch"},
                  _read("load_session", n=1, session_id="s", history_version=3),
                  {"action": "chat"},
                  _read("load_session", n=2, session_id="s", history_version=3)], done)
        await asyncio.wait_for(websocket_dispatch._websocket_handler(ws), 10)

    asyncio.run(exercise())
    assert events[0] == "checkout"
    assert "chat protocol 3" in events
    assert events.index("load 1 answered") < events.index("load 2 started")
