"""A request repeated with its own key, while the first still holds the slot.

The run slot is where a second operation on a branch is refused. A repeat
of the operation that holds it (the same rewind or revert with its
idempotency key) is not a second operation: it waits for the first and
returns the same result, through the real action handlers. A different
key on that branch is still refused, and a send repeated with its
``client_request_id`` is answered ``request_admitted``, never queued as a
new turn.
"""
from __future__ import annotations

import asyncio
import json
import threading
from concurrent.futures import ThreadPoolExecutor, wait

import pytest

from openprogram.state.store.session.session_store import SessionStore
from openprogram.state.store.snapshot.checkpoint import CheckpointStore


@pytest.fixture
def world(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", store,
        raising=False,
    )
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    # The head mirror and context figures are the page's; nothing here reads them.
    monkeypatch.setattr(server, "_set_active_head", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "refresh_context_stats", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_broadcast", lambda *_a, **_k: None)
    yield store
    with server._running_tasks_lock:
        for key in [key for key in server._run_slots if key[0].startswith("s-repeat")]:
            server._run_slots.pop(key, None)


def _three_turns(store, tmp_path, session_id):
    store.create_session(session_id, "main", title="repeat")
    target = tmp_path / "work" / f"{session_id}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("v0\n", encoding="utf-8")
    journal = CheckpointStore(store._session_dir(session_id))
    predecessor = None
    for number in range(1, 4):
        store.append_message(session_id, {
            "id": f"u{number}", "role": "user", "content": f"u{number}",
            "predecessor": predecessor,
        })
        journal.backup_before_edit(f"a{number}", str(target))
        target.write_text(f"v{number}\n", encoding="utf-8")
        journal.commit_after_edit(f"a{number}", str(target), operation="edit")
        store.append_message(session_id, {
            "id": f"a{number}", "role": "assistant", "content": f"a{number}",
            "predecessor": f"u{number}",
        })
        predecessor = f"a{number}"
    store.set_head(session_id, "a3")
    return target


def _pause_first(monkeypatch, module, name):
    entered, release = threading.Event(), threading.Event()
    original = getattr(module, name)
    calls = []

    def paused(*args, **kwargs):
        calls.append(None)
        if len(calls) == 1:
            entered.set()
            assert release.wait(timeout=30)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, paused)
    return entered, release


def _post_rewind(session_id, plan, key):
    from openprogram.interfaces.server.routes import chat_branches

    response = asyncio.run(chat_branches.post_chat_rewind({
        "session_id": session_id, "msg_id": "u2", "plan_hash": plan["plan_hash"],
        "idempotency_key": key, "mode": "code_and_conversation",
    }))
    return response.status_code, json.loads(response.body)


def test_the_same_rewind_twice_returns_one_result(world, tmp_path, monkeypatch):
    from openprogram.runtime.conversation import rewind
    from openprogram.runtime.conversation.rewind import plan_rewind

    session_id = "s-repeat-rewind"
    target = _three_turns(world, tmp_path, session_id)
    plan = plan_rewind(session_id, "u2")
    # Paused holding the run slot, before the store's own locks.
    entered, release = _pause_first(monkeypatch, rewind, "rewind_to")
    with ThreadPoolExecutor(max_workers=3) as executor:
        first = executor.submit(_post_rewind, session_id, plan, plan["idempotency_key"])
        assert entered.wait(timeout=30)
        second = executor.submit(_post_rewind, session_id, plan, plan["idempotency_key"])
        # Another rewind on the branch is still refused while the first runs.
        other = _post_rewind(session_id, plan, "another-key")
        assert other[0] == 409 and other[1]["code"] == "run_active"
        assert other[1]["kind"] == "rewind"
        # The repeat waits for the first instead of being refused.
        assert not wait([second], timeout=0.5).done
        release.set()
        results = [first.result(timeout=60), second.result(timeout=60)]

    assert [code for code, _body in results] == [200, 200]
    assert [body["status"] for _code, body in results] == ["committed", "committed"]
    assert results[0][1]["new_head_id"] == results[1][1]["new_head_id"]
    assert target.read_text(encoding="utf-8") == "v1\n"
    assert world.get_session(session_id)["head_id"] == "a1"


class _WebSocket:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.frames.append(json.loads(value))


def _revert(session_id, turn_id, key):
    from openprogram.interfaces.server.ws_actions.turn_files.history import handle_revert_turn

    ws = _WebSocket()
    asyncio.run(handle_revert_turn(ws, {
        "session_id": session_id, "msg_id": turn_id, "idempotency_key": key,
    }))
    return ws.frames[-1]["data"]


def test_the_same_revert_twice_returns_one_result(world, tmp_path, monkeypatch):
    session_id, turn_id = "s-repeat-revert", "u1_reply"
    world.create_session(session_id, "main", title="revert")
    world.append_message(session_id, {"id": "u1", "role": "user", "content": "edit"})
    world.append_message(session_id, {
        "id": turn_id, "role": "assistant", "content": "done", "predecessor": "u1",
    })
    target = tmp_path / "revert.py"
    target.write_text("before\n", encoding="utf-8")
    journal = CheckpointStore(world._session_dir(session_id))
    journal.backup_before_edit(turn_id, str(target))
    target.write_text("after\n", encoding="utf-8")
    journal.commit_after_edit(turn_id, str(target), operation="edit")
    from openprogram.runtime.conversation import revert

    # Paused holding the run slot, before the store's own locks.
    entered, release = _pause_first(monkeypatch, revert, "revert_turn")
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(_revert, session_id, turn_id, "undo-1")
        assert entered.wait(timeout=30)
        second = executor.submit(_revert, session_id, turn_id, "undo-1")
        other = _revert(session_id, turn_id, "undo-2")
        assert other["status"] == "blocked" and other["errors"] == ["run_active"]
        assert not wait([second], timeout=0.5).done
        release.set()
        results = [first.result(timeout=60), second.result(timeout=60)]

    assert [result["status"] for result in results] == ["committed", "committed"]
    assert results[0]["transaction_id"] == results[1]["transaction_id"]
    assert target.read_text(encoding="utf-8") == "before\n"


def test_a_repeated_send_is_answered_as_that_send(world, monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions.chat import turn
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _client_request_id

    session_id = "s-repeat-send"
    world.create_session(session_id, "main", title="send")
    assert server._try_reserve_run(session_id, "first-turn", key="send:req-1")
    try:
        for request_id, code in (("req-1", "request_admitted"), ("req-2", "run_active")):
            ws = _WebSocket()
            req = turn._ChatRequest(
                cmd={"client_request_id": request_id}, text="hello", session_id=session_id,
                agent_id=None, thinking_effort=None, tools_flag=None, web_search_flag=None,
                permission_mode=None, sandbox_flag=None, surface_ref=None, service_tier=None,
                response_format=None, attachments=None,
            )
            token = _client_request_id.set(request_id)
            try:
                assert asyncio.run(turn._reserve_run(ws, server, req)) is None
            finally:
                _client_request_id.reset(token)
            data = ws.frames[-1]["data"]
            assert data["code"] == code
            assert data["client_request_id"] == request_id
            # Only a send refused for another's turn is queued to send again.
            assert ("retry_query" in data) is (code == "run_active")
    finally:
        server._release_run_reservation(session_id, "first-turn")


def test_a_repeat_whose_first_attempt_just_ended_is_not_refused(monkeypatch):
    """The first attempt can end between the refusal and the look for the
    slot carrying its key; the repeat then asks again and takes the slot."""
    from openprogram.interfaces.server.server_runtime import session_lifecycle

    answers = [{"code": "run_active", "kind": "rewind", "error": "busy"}, None]
    monkeypatch.setattr(session_lifecycle, "_reserve_operation_once",
                        lambda *args, **kwargs: answers.pop(0))
    monkeypatch.setattr(session_lifecycle, "_outwait_finishing", lambda *args: False)
    monkeypatch.setattr(session_lifecycle, "keyed_slot", lambda *args: None)

    assert session_lifecycle.reserve_operation("s-repeat-ended", "rewind", "owner", key="k") is None
    # Something else in the way, with no slot for this key, is still refused.
    answers[:] = [{"code": "run_active", "kind": "reply", "error": "busy"}] * 2
    assert session_lifecycle.reserve_operation("s-repeat-ended", "rewind", "owner", key="k")["code"] == "run_active"
