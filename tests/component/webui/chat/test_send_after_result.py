"""A message sent the moment the previous turn says it is done.

A turn sends its terminal frame (``result``, or ``error``) and then still
finishes up: it closes its execution record and context figures and only
then gives its run slot back. A message sent on seeing that frame used to
find the slot held and be refused ``run_active``. The turn now marks its
slot as finishing just before the frame goes out, and a request for the
branch waits the finish-up out instead: it is admitted once the slot is
free, never while the first turn can still write.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from tests.component.agent.execution.test_agent_continuation_real import (  # noqa: F401
    _WebSocket,
    _wait,
    real_agent_chat,
)


def _send(harness, text: str, frames: list, stamps: dict) -> None:
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    class _Socket(_WebSocket):
        async def send_text(self, value: str) -> None:
            frame = json.loads(value)
            if frame.get("type") == "chat_ack":
                stamps.setdefault("ack", time.monotonic())
            frames.append(frame)

    asyncio.run(handle_chat(_Socket(), {
        "text": text, "session_id": harness.session_id, "permission_mode": "bypass",
    }))


def test_a_send_racing_the_result_frame_is_admitted_after_the_slot_frees(
    real_agent_chat, monkeypatch,
):
    from tests.component.providers.scripted_provider import ScriptedText

    h = real_agent_chat
    server = h.server
    h.provider.add_response(ScriptedText("first"))
    h.provider.add_response(ScriptedText("second"))

    stamps: dict = {}
    second_frames: list[dict] = []
    second_done = threading.Event()
    raced = threading.Event()

    def send_second():
        try:
            _send(h, "second", second_frames, stamps)
        finally:
            second_done.set()

    def broadcast(message, *_args, **_kwargs):
        if raced.is_set() or '"result"' not in message:
            return
        data = json.loads(message).get("data") or {}
        if data.get("type") != "result":
            return
        raced.set()
        # The second message goes out the instant the first turn's result
        # does; the first turn's thread is held at that frame meanwhile.
        threading.Thread(target=send_second, daemon=True).start()
        second_done.wait(0.5)

    monkeypatch.setattr(server, "_broadcast", broadcast)
    take = server._take_owned_run

    def timed_take(session_id, msg_id):
        where = take(session_id, msg_id)
        if where is not None:
            stamps.setdefault("first_released", time.monotonic())
        return where

    monkeypatch.setattr(server, "_take_owned_run", timed_take)

    first_frames: list[dict] = []
    _send(h, "first", first_frames, {})
    assert any(frame.get("type") == "chat_ack" for frame in first_frames)
    assert second_done.wait(30), "the second send never got an answer"

    refusals = [frame["data"] for frame in second_frames
                if (frame.get("data") or {}).get("type") == "error"]
    assert refusals == []
    assert any(frame.get("type") == "chat_ack" for frame in second_frames)
    # Admitted only once the first turn had given its slot back.
    assert stamps["first_released"] <= stamps["ack"]
    _wait(lambda: h.provider.call_count == 2 and not server._is_run_active(h.session_id))


@pytest.fixture
def slots(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", store,
        raising=False,
    )
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(server, "_broadcast", lambda *_a, **_k: None)
    store.create_session("s-finishing", "main", title="finishing")
    yield server
    with server._running_tasks_lock:
        for key in [key for key in server._run_slots if key[0] == "s-finishing"]:
            server._run_slots.pop(key, None)


def _later(seconds: float, action) -> threading.Thread:
    def run():
        time.sleep(seconds)
        action()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize("entry", ["send", "operation", "owner_turn"])
def test_every_entry_waits_out_a_finishing_turn(slots, entry):
    from openprogram.interfaces.server.server_runtime.session_lifecycle import ServerRunSlot

    server = slots
    session_id = "s-finishing"
    assert server._try_reserve_run(session_id, "turn-a")
    ServerRunSlot().finishing(session_id, "turn-a")
    released: list[float] = []

    def release():
        server._take_owned_run(session_id, "turn-a")
        released.append(time.monotonic())

    _later(0.15, release)
    if entry == "send":
        admitted = server._try_reserve_run(session_id, "turn-b")
    elif entry == "operation":
        admitted = server.reserve_operation(session_id, "retry", "turn-b") is None
    else:
        admitted = ServerRunSlot().reserve(session_id, "turn-b")
    assert admitted
    assert released and released[0] <= time.monotonic()
    with server._running_tasks_lock:
        assert server._running_tasks[session_id]["msg_id"] == "turn-b"


def test_a_side_branch_send_waits_out_its_branch_finishing(slots):
    from openprogram.interfaces.server.server_runtime.session_lifecycle import mark_run_finishing

    server = slots
    session_id = "s-finishing"
    assert server._try_reserve_branch_run(session_id, "side", "turn-a")
    assert mark_run_finishing(session_id, "turn-a")
    _later(0.15, lambda: server._take_owned_run(session_id, "turn-a"))
    assert server._try_reserve_branch_run(session_id, "side", "turn-b")


def test_a_turn_still_running_is_refused_at_once(slots):
    server = slots
    session_id = "s-finishing"
    assert server._try_reserve_run(session_id, "turn-a")
    started = time.monotonic()
    assert not server._try_reserve_run(session_id, "turn-b")
    assert server.reserve_operation(session_id, "retry", "turn-b")["code"] == "run_active"
    assert time.monotonic() - started < 1.0


def test_a_finishing_turn_that_never_ends_is_refused_after_the_bound(slots, monkeypatch):
    from openprogram.interfaces.server.server_runtime import session_lifecycle

    server = slots
    session_id = "s-finishing"
    monkeypatch.setattr(session_lifecycle, "FINISHING_WAIT_SECONDS", 0.2)
    assert server._try_reserve_run(session_id, "turn-a")
    session_lifecycle.mark_run_finishing(session_id, "turn-a")
    started = time.monotonic()
    assert not server._try_reserve_run(session_id, "turn-b")
    assert 0.2 <= time.monotonic() - started < 2.0


def test_an_edit_or_retry_waits_out_a_finishing_turn(slots):
    from openprogram.interfaces.server.server_runtime.session_lifecycle import (
        ServerRunSlot,
        selected_run_blocks,
    )

    server = slots
    session_id = "s-finishing"
    assert not selected_run_blocks(session_id)
    assert server._try_reserve_run(session_id, "turn-a")
    # Still running: refused at once.
    started = time.monotonic()
    assert selected_run_blocks(session_id)
    assert time.monotonic() - started < 1.0
    ServerRunSlot().finishing(session_id, "turn-a")
    _later(0.3, lambda: server._take_owned_run(session_id, "turn-a"))
    assert not selected_run_blocks(session_id)
    assert not server._is_selected_run_active(session_id)


def test_the_fork_route_asks_whether_the_selected_run_blocks(monkeypatch):
    from openprogram.interfaces.server.routes import chat_branches
    from openprogram.interfaces.server.server_runtime import session_lifecycle

    asked: list[str] = []
    monkeypatch.setattr(session_lifecycle, "selected_run_blocks",
                        lambda session_id: asked.append(session_id) or True)
    out = chat_branches._fork_user_turn_and_run("s-any", "m-any", None)
    assert asked == ["s-any"]
    assert out["__error__"][1] == 409
