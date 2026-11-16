"""A held conversation delays the request that needs it, never the Worker.

Every read and write of a conversation takes its lock (its process lock,
then its flock), which a turn, a cold first load of a large conversation
or another process can hold for a while. The Worker's event loop serves
every connection, so a request handled there runs that work in a worker
thread: while one conversation is held, the loop keeps answering others.

Taking a run slot waits for the same lock with the store's own budget and
reports a lock it could not have for what it is (``conversation_locked``),
not as a turn that is running (``run_active``).
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from types import SimpleNamespace

import pytest

SID = "s-held"
HOLD_SECONDS = 0.6
# A tick of the heartbeat below is 10 ms; a loop free to run answers well
# inside this, a loop blocked on the lock waits out the whole hold.
MAX_STALL = 0.25


@pytest.fixture
def held(tmp_path, monkeypatch):
    from openprogram.interfaces.server import server
    from openprogram.state.store.session.session_store import SessionStore

    store = SessionStore(tmp_path / "sessions")
    monkeypatch.setattr(
        "openprogram.state.store.session.session_store.shared._default_store", store,
        raising=False,
    )
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: store)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(server, "_broadcast", lambda *_a, **_k: None)
    store.create_session(SID, "main", title="held")
    store.append_message(SID, {"id": "u1", "role": "user", "content": "q"})
    store.append_message(SID, {"id": "a1", "role": "assistant", "content": "r",
                               "predecessor": "u1"})
    store.set_head(SID, "a1")
    yield SimpleNamespace(server=server, store=store)
    with server._running_tasks_lock:
        for key in [key for key in server._run_slots if key[0] == SID]:
            server._run_slots.pop(key, None)


def _hold(lock_factory, seconds: float,
          release: threading.Event | None = None) -> tuple[threading.Thread, threading.Event]:
    """Hold a conversation lock on another thread for ``seconds``, or until
    ``release`` is set."""
    taken = threading.Event()

    def run():
        with lock_factory():
            taken.set()
            if release is not None:
                release.wait(30)
            else:
                time.sleep(seconds)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    assert taken.wait(5)
    return thread, taken


async def _stall_while(task: asyncio.Task, seconds: float) -> float:
    """The longest gap between 10 ms heartbeats while ``task`` runs."""
    longest, last = 0.0, time.monotonic()
    deadline = last + seconds
    while time.monotonic() < deadline and not task.done():
        await asyncio.sleep(0.01)
        now = time.monotonic()
        longest, last = max(longest, now - last), now
    return longest


class _Socket:
    _history_protocol = 0
    scope: dict = {}

    def __init__(self):
        self.frames: list[dict] = []

    async def send_text(self, value: str) -> None:
        self.frames.append(json.loads(value))


def test_a_lock_taken_on_the_loop_stalls_every_connection(held):
    """The measure: a store read made on the loop waits out the whole hold."""
    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.ensure_future(asyncio.sleep(HOLD_SECONDS * 2))
        start = time.monotonic()
        held.store.get_session(SID)  # on the loop, as handlers used to read
        blocked = time.monotonic() - start
        task.cancel()
        holder.join()
        return blocked

    assert asyncio.run(scenario()) >= HOLD_SECONDS * 0.8


@pytest.mark.parametrize("action", [
    "load_session", "get_run_state", "checkout_branch", "rename_session",
    "get_full_tool_output", "set_sandbox",
])
def test_a_held_conversation_delays_only_its_own_request(held, monkeypatch, action):
    from openprogram.interfaces.server.ws_actions.branch import handle_checkout_branch
    from openprogram.interfaces.server.ws_actions.session.lifecycle import handle_rename_session
    from openprogram.interfaces.server.ws_actions.session.load import handle_load_session
    from openprogram.interfaces.server.ws_actions.session.run_config import handle_set_sandbox
    from openprogram.interfaces.server.ws_actions.session.run_state import handle_get_run_state
    from openprogram.interfaces.server.ws_actions.session.tool_output import (
        handle_get_full_tool_output,
    )

    server = held.server
    monkeypatch.setattr(server, "refresh_context_stats", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_sessions", {
        SID: {"id": SID, "messages": [], "head_id": "a1"},
    })
    handler, cmd, answer = {
        "load_session": (handle_load_session, {"session_id": SID}, "session_loaded"),
        "get_run_state": (handle_get_run_state, {"session_id": SID}, "run_state"),
        "checkout_branch": (handle_checkout_branch,
                            {"session_id": SID, "head_msg_id": "u1"}, "branch_checked_out"),
        "rename_session": (handle_rename_session,
                           {"session_id": SID, "title": "renamed"}, "session_rename_result"),
        "get_full_tool_output": (handle_get_full_tool_output,
                                 {"session_id": SID, "message_id": "a1", "node_id": "t1"},
                                 "full_tool_output"),
        "set_sandbox": (handle_set_sandbox, {"session_id": SID}, "sandbox_changed"),
    }[action]

    async def scenario():
        ws = _Socket()
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(handler(ws, cmd))
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        # Still waiting for the conversation, while the loop kept running.
        waiting = not task.done()
        holder.join()
        await asyncio.wait_for(task, 10)
        return stall, waiting, ws.frames

    stall, waiting, frames = asyncio.run(scenario())
    assert waiting, "the request did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on {action}"
    assert any(frame.get("type") == answer for frame in frames), frames


def test_a_lock_taken_on_the_worker_loop_is_logged_once(held, monkeypatch, caplog):
    from openprogram.state.store.session.files import session_lock

    server = held.server
    monkeypatch.setattr(session_lock, "_loop_sites", set())

    async def scenario():
        monkeypatch.setattr(server, "_loop", asyncio.get_running_loop(), raising=False)
        for _ in range(2):
            held.store.get_session(SID)  # one call site, read twice
        await asyncio.to_thread(held.store.get_session, SID)

    with caplog.at_level(logging.WARNING, logger=session_lock.__name__):
        asyncio.run(scenario())
    notes = [record for record in caplog.records if "on the event loop" in record.getMessage()]
    assert len(notes) == 1
    assert "test_conversation_lock_off_loop.py" in notes[0].getMessage()


def test_a_slot_waits_for_a_conversation_still_loading(held):
    """A cold load holding the lock past the old 0.25 s is waited out: the
    send is admitted, not refused as if a reply were running."""
    from openprogram.state.store.session.files.session_lock import session_interprocess_lock

    server = held.server
    holder, _ = _hold(lambda: session_interprocess_lock(SID), 0.6)
    try:
        assert server._try_reserve_run(SID, "after-load")
    finally:
        holder.join()
    with server._running_tasks_lock:
        assert server._running_tasks[SID]["msg_id"] == "after-load"


def test_a_lock_not_had_in_time_is_reported_as_locked(held, monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat.turn import _reserve_run
    from openprogram.state.store.session.files import session_lock
    from openprogram.state.store.session.files.session_lock import (
        SessionLockTimeout,
        session_interprocess_lock,
    )

    server = held.server
    monkeypatch.setattr(session_lock, "DEFAULT_WAIT", 0.2)
    release = threading.Event()
    holder, _ = _hold(lambda: session_interprocess_lock(SID, timeout=5), 0, release)
    try:
        with pytest.raises(SessionLockTimeout):
            server._try_reserve_run(SID, "typed")
        with pytest.raises(SessionLockTimeout):
            server._try_reserve_branch_run(SID, "side", "typed")
        refusal = server.reserve_operation(SID, "compact", "compact-1")
        assert refusal["code"] == "conversation_locked"
        assert refusal["retryable"] is True
        refusal, owners = server.reserve_branches_operation(SID, ["b1"], "delete", "op")
        assert refusal["code"] == "conversation_locked" and owners == []

        ws = _Socket()
        req = SimpleNamespace(session_id=SID, side_branch_id=None, cmd={}, text="hello")
        assert asyncio.run(_reserve_run(ws, server, req)) is None
    finally:
        release.set()
        holder.join()
    [error] = [frame["data"] for frame in ws.frames if frame.get("type") == "chat_response"]
    assert error["code"] == "conversation_locked"
    assert error["retry_query"] == "hello"
    # Nothing was taken while the lock was missing.
    with server._running_tasks_lock:
        assert SID not in server._running_tasks
    # Once it is free, the same send is admitted.
    assert server._try_reserve_run(SID, "typed")


def test_a_held_slot_is_still_run_active(held):
    server = held.server
    assert server._try_reserve_run(SID, "first")
    assert not server._try_reserve_run(SID, "second")
    assert server.reserve_operation(SID, "retry", "retry-1")["code"] == "run_active"


def test_a_function_run_waits_for_its_conversation_off_the_loop(held, monkeypatch):
    """``POST /api/function/{name}`` takes the run slot (which waits the
    store's budget) and writes the call's records: a held conversation
    delays that request, not the loop."""
    from fastapi import FastAPI
    from openprogram.interfaces.server.routes import chat as chat_routes

    def run_call(name, kwargs, session_id, **_options):
        held.store.get_session(session_id)  # as the slot and the records do
        return {"session_id": session_id, "msg_id": "m1"}

    monkeypatch.setattr(chat_routes, "run_agent_method_call", run_call)
    app = FastAPI()
    chat_routes.register(app)
    [endpoint] = [route.endpoint for route in app.routes
                  if getattr(route, "path", "") == "/api/function/{name}"]

    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(endpoint("paper_agent", {"session_id": SID, "kwargs": {}}))
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        waiting = not task.done()
        holder.join()
        return stall, waiting, await asyncio.wait_for(task, 10)

    stall, waiting, response = asyncio.run(scenario())
    assert waiting, "the function run did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on a function run"
    assert json.loads(response.body)["msg_id"] == "m1"


@pytest.mark.parametrize("path", [
    "/api/sessions/{session_id}/context-range",
    "/api/sessions/{session_id}/context",
    "/api/sessions/{session_id}/branches/tokens",
    "/api/sessions/{session_id}/tokens",
])
def test_a_conversation_read_route_waits_off_the_loop(held, monkeypatch, path):
    """The routes a page calls on opening a conversation (its context
    coverage and figures) read it under its lock; while a turn or a cold
    load holds it, the other connections keep being answered."""
    from fastapi import FastAPI
    from openprogram.interfaces.server.routes.files import tree

    monkeypatch.setattr(held.server, "_sessions", {SID: {"id": SID, "head_id": "a1"}})
    app = FastAPI()
    tree.register(app)
    [endpoint] = [route.endpoint for route in app.routes if getattr(route, "path", "") == path]
    kwargs = {"head_id": None} if path.endswith(("context-range", "/context")) else {}
    if path.endswith("/tokens") and "branches" not in path:
        kwargs = {"head_id": None, "model": None, "provider": None}

    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(endpoint(SID, **kwargs))
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        waiting = not task.done()
        holder.join()
        return stall, waiting, await asyncio.wait_for(task, 10)

    stall, waiting, response = asyncio.run(scenario())
    assert waiting, f"{path} did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on {path}"
    assert response.status_code == 200
    json.loads(response.body)


def test_marking_a_held_conversation_read_waits_off_the_loop(held):
    from openprogram.interfaces.server.ws_actions.session.lifecycle import (
        handle_mark_session_read,
    )

    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(handle_mark_session_read(_Socket(), {"session_id": SID}))
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        waiting = not task.done()
        holder.join()
        await asyncio.wait_for(task, 10)
        return stall, waiting

    stall, waiting = asyncio.run(scenario())
    assert waiting, "marking read did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s marking a conversation read"
    assert held.store.get_session(SID).get("unread") is False


def test_an_execution_replay_reads_its_job_off_the_loop(held, monkeypatch):
    """A page reconnecting replays each execution; the snapshot's job view
    reads the conversation's jobs under its lock."""
    from openprogram.interfaces.server.ws_actions import runtime as runtime_actions

    execution = SimpleNamespace(execution_id="exec-1", session_id=SID)
    replay = SimpleNamespace(
        execution=execution, events=[], recovery=None,
        cursor=SimpleNamespace(next_sequence=1, to_dict=lambda: {"next_sequence": 1}),
    )
    store = SimpleNamespace(get_execution=lambda _id: execution,
                            read_event_replay=lambda *_a, **_k: replay)
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr(runtime_actions, "_authorize_execution", lambda *_a, **_k: None)
    monkeypatch.setattr(runtime_actions, "_trusted_runtime_actor", lambda _ws: {})

    def snapshot(record, *, event_sequence=None):
        held.store.get_session(SID)  # as the job view does
        return {"execution_id": record.execution_id}, {"next_sequence": 1}

    monkeypatch.setattr(runtime_actions, "_public_execution_snapshot", snapshot)

    async def scenario():
        ws = _Socket()
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(runtime_actions.handle_execution_replay(
            ws, {"execution_id": "exec-1", "after_sequence": 0}))
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        waiting = not task.done()
        holder.join()
        await asyncio.wait_for(task, 10)
        return stall, waiting, ws.frames

    stall, waiting, frames = asyncio.run(scenario())
    assert waiting, "the replay did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on an execution replay"
    [frame] = frames
    assert frame["type"] == "execution.replay" and frame["snapshot"] == {"execution_id": "exec-1"}


def test_the_store_is_told_the_loop_and_never_imports_the_server():
    """The lock learns the Worker's loop from the server (``watch_event_loop``);
    the store, below the server, does not reach up for it."""
    import inspect

    from openprogram.interfaces.server import server
    from openprogram.state.store.session.files import session_lock

    assert "interfaces" not in inspect.getsource(session_lock)
    assert session_lock._worker_loop is not None
    loop = asyncio.new_event_loop()
    try:
        previous = getattr(server, "_loop", None)
        server._loop = loop
        assert loop.run_until_complete(_on_loop(session_lock))
        assert not session_lock._on_worker_event_loop()
    finally:
        server._loop = previous
        loop.close()


async def _on_loop(session_lock) -> bool:
    return session_lock._on_worker_event_loop()


# --- Every handler that reads or writes a conversation, with it held -------


def _held_scenario(held, coroutine_factory):
    """Run ``coroutine_factory()`` while another thread holds ``SID``:
    ``(longest loop stall, still waiting during the hold, result)``."""
    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        task = asyncio.create_task(coroutine_factory())
        stall = await _stall_while(task, HOLD_SECONDS * 0.7)
        waiting = not task.done()
        holder.join()
        return stall, waiting, await asyncio.wait_for(task, 10)

    return asyncio.run(scenario())


def _ws_handler(action: str):
    from openprogram.interfaces.server.ws_actions import branch, context_commits, dag_nodes, project
    from openprogram.interfaces.server.ws_actions import runtime as runtime_actions
    from openprogram.interfaces.server.ws_actions.chat import commands, conversation_channel
    from openprogram.interfaces.server.ws_actions.session import lifecycle

    return {
        "attach_branch": (branch.handle_attach_branch,
                          {"session_id": SID, "target_head_msg_id": "u1"}, "attach_branch_result"),
        "subscribe_execution_stream": (runtime_actions.handle_subscribe_execution_stream,
                                       {"session_id": SID, "execution_id": "e1",
                                        "node_ids": ["a1"]}, "chat_response"),
        "set_session_project": (project.handle_set_session_project,
                                {"session_id": SID, "project_id": "no-such-project"},
                                "session_project_set"),
        "list_session_workdirs": (project.handle_list_session_workdirs,
                                  {"session_id": SID}, "session_workdirs"),
        "add_session_workdir": (project.handle_add_session_workdir,
                                {"session_id": SID, "path": "."}, "session_workdir_added"),
        "remove_session_workdir": (project.handle_remove_session_workdir,
                                   {"session_id": SID, "path": "."}, "session_workdir_removed"),
        "move_session_to_project": (project.handle_move_session_to_project,
                                    {"session_id": SID, "project_id": "no-such-project"},
                                    "session_moved"),
        "update_session_flags": (lifecycle.handle_update_session_flags,
                                 {"session_id": SID, "pinned": True, "sort_at": 5}, None),
        "list_commits": (context_commits.handle_list_commits,
                         {"session_id": SID}, "context_commits_list"),
        "delete_nodes": (dag_nodes.handle_delete_nodes,
                         {"session_id": SID, "node_ids": ["a1"]}, "nodes_deleted"),
        "set_conversation_channel": (conversation_channel.handle_set_conversation_channel,
                                     {"session_id": SID}, "session_channel_updated"),
        "compact": (commands.handle_compact, {"session_id": SID}, None),
    }[action]


@pytest.mark.parametrize("action", [
    "attach_branch", "subscribe_execution_stream", "set_session_project",
    "list_session_workdirs", "add_session_workdir", "remove_session_workdir",
    "move_session_to_project", "update_session_flags", "list_commits", "delete_nodes",
    "set_conversation_channel", "compact",
])
def test_every_conversation_handler_waits_off_the_loop(held, monkeypatch, tmp_path, action):
    """The handlers the Worker's loop warning named, one by one: a held
    conversation keeps each waiting while the loop's heartbeat goes on."""
    server = held.server
    monkeypatch.setattr(server, "refresh_context_stats", lambda *_a, **_k: None)
    monkeypatch.setattr(server, "_sessions", {
        SID: {"id": SID, "messages": [], "head_id": "a1"},
    })
    monkeypatch.setattr(
        "openprogram.runtime.conversation.turn_runtime.trigger_compaction",
        lambda *_a, **_k: None,
    )
    handler, cmd, answer = _ws_handler(action)
    if action == "move_session_to_project":
        from openprogram.state.store.project import project_store

        target = tmp_path / "elsewhere"
        target.mkdir()
        cmd = {**cmd, "project_id": project_store.resolve_project(target).id}
    ws = _Socket()
    stall, waiting, _ = _held_scenario(held, lambda: handler(ws, cmd))
    assert waiting, f"{action} did not wait for the held conversation: {ws.frames}"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on {action}"
    if answer is not None:
        assert any(frame.get("type") == answer for frame in ws.frames), ws.frames


def _route(module, path: str, method: str = "POST"):
    from fastapi import FastAPI

    if hasattr(module, "router"):
        routes = module.router.routes
    else:
        app = FastAPI()
        module.register(app)
        routes = app.routes
    [endpoint] = [route.endpoint for route in routes
                  if getattr(route, "path", "") == path and method in getattr(route, "methods", ())]
    return endpoint


@pytest.mark.parametrize("name", ["chat_branch", "export", "rewind_points"])
def test_every_conversation_route_waits_off_the_loop(held, monkeypatch, name):
    from openprogram.interfaces.server.routes import chat, chat_branches
    from openprogram.interfaces.server.routes.files import export

    monkeypatch.setattr(held.server, "_sessions", {SID: {"id": SID, "head_id": "a1"}})
    call = {
        "chat_branch": lambda: _route(chat, "/api/chat/branch")({"session_id": SID, "msg_id": "u1"}),
        "export": lambda: _route(export, "/api/sessions/{session_id}/export", "GET")(SID, "md"),
        "rewind_points": lambda: _route(chat_branches, "/api/chat/rewind_points")(
            {"session_id": SID}),
    }[name]
    stall, waiting, response = _held_scenario(held, call)
    assert waiting, f"{name} did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on {name}"
    assert response.status_code == 200


@pytest.mark.parametrize("path", ["/api/file-resolve", "/api/file-search", "/api/file-paths"])
def test_a_file_route_finds_the_chat_folder_off_the_loop(held, monkeypatch, tmp_path, path):
    """The composer's @-mention, file search and a reply's file links find
    the chat's working directory under its lock; while a turn holds it,
    the other connections keep being answered."""
    from openprogram.interfaces.server.routes.files import file_search

    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(tmp_path))
    folder = file_search._chat_root(SID, "") or tmp_path
    (folder / "note.txt").write_text("hi", encoding="utf-8")
    endpoint = _route(file_search, path, "GET")
    call = {
        "/api/file-resolve": lambda: endpoint("note.txt", None, SID, ""),
        "/api/file-search": lambda: endpoint("note", None, 12, 5000, SID, ""),
        "/api/file-paths": lambda: endpoint(["note.txt"], SID),
    }[path]
    stall, waiting, response = _held_scenario(held, call)
    assert waiting, f"{path} did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on {path}"
    assert response.status_code == 200


def test_a_file_search_walks_the_folder_outside_the_conversation_lane(held, monkeypatch, tmp_path):
    """Only finding the chat's folder takes the conversation's lane: the
    walk, slow on a large folder and asked on every @-mention keystroke,
    must not hold a Send or Stop of the same chat behind it."""
    from openprogram.interfaces.server.routes.files import file_search
    from openprogram.state.store.session import conversation_work

    monkeypatch.setenv("OPENPROGRAM_PROJECT_ROOT", str(tmp_path))
    lane_busy_during_walk: list[int] = []

    def lane_busy() -> int:
        return sum(lane.busy for (_, sid), lane in tuple(conversation_work._lanes.items()) if sid == SID)

    def walk(cwd, needle, limit, max_scan):
        lane_busy_during_walk.append(lane_busy())
        return []

    monkeypatch.setattr(file_search, "_walk", walk)
    endpoint = _route(file_search, "/api/file-search", "GET")
    # Work an earlier test left on this conversation's lane is not the walk's.
    before = lane_busy()
    response = asyncio.run(endpoint("note", None, 12, 5000, SID, ""))
    assert response.status_code == 200
    assert lane_busy_during_walk == [before]


def test_noting_a_queued_message_never_waits_for_the_conversation(held):
    """``goal_queue`` keeps a flag in memory: a held conversation does not
    delay it at all."""
    from openprogram.interfaces.server.routes.execution import goal

    endpoint = _route(goal, "/api/sessions/{session_id}/goal/queue")

    async def scenario():
        holder, _ = _hold(lambda: held.store._session_lock(SID), HOLD_SECONDS)
        start = time.monotonic()
        response = await endpoint(SID, {"waiting": True})
        took = time.monotonic() - start
        holder.join()
        return took, response

    took, response = asyncio.run(scenario())
    assert took < 0.1 and response.status_code == 200


# --- Many waiters on one conversation, and the others ----------------------


def test_forty_waiters_on_one_conversation_leave_the_others_served(held, monkeypatch):
    """Requests queued on a held conversation take at most its lane's
    threads: another conversation's load still answers at once."""
    from openprogram.interfaces.server.ws_actions.session.run_state import handle_get_run_state

    other = "s-other"
    held.store.create_session(other, "main", title="other")
    monkeypatch.setattr(held.server, "_sessions", {})

    async def scenario():
        release = threading.Event()
        holder, _ = _hold(lambda: held.store._session_lock(SID), 0, release)
        waiters = [asyncio.create_task(handle_get_run_state(_Socket(), {"session_id": SID}))
                   for _ in range(40)]
        await asyncio.sleep(0.1)
        ws = _Socket()
        start = time.monotonic()
        await asyncio.wait_for(handle_get_run_state(ws, {"session_id": other}), 5)
        took = time.monotonic() - start
        still_waiting = sum(not task.done() for task in waiters)
        release.set()
        holder.join()
        await asyncio.wait_for(asyncio.gather(*waiters), 30)
        return took, still_waiting, ws.frames

    took, still_waiting, frames = asyncio.run(scenario())
    assert still_waiting == 40
    assert took < 0.5, f"another conversation waited {took:.2f}s behind 40 held requests"
    assert frames and frames[0]["type"] == "run_state"


# --- A Stop while its conversation is held ----------------------------------


ACTOR = {
    "speaker_kind": "owner", "speaker_id": "owner/local", "speaker_display": "Owner",
    "authority_tier": "owner", "principal_id": "owner/install/0123456789abcdef",
    "interaction": "interactive",
}


def _execution(tmp_path, monkeypatch, *, start: bool):
    from openprogram.runtime.execution import (
        AttemptStore, CapabilitySet, DriverRegistry, ExecutionStore, RuntimeControlService,
    )

    store = ExecutionStore(tmp_path / "executions.db")
    revision = store.create_revision(manifest={"entrypoint": "chat"})
    admitted = store.admit_execution(
        execution_id="turn", run_id="run", session_id=SID,
        revision_id=revision.revision_id, input_ref="blob:input",
        input_hash="input-hash", entrypoint="chat", trusted_actor={"subject": "owner"},
        config_snapshot_ref="blob:config", user_message_id="u1", assistant_message_id="a1",
        capabilities=CapabilitySet(pause=True, steer=True, safe_point_kinds=("after",)),
    )
    attempts = AttemptStore(store)
    if start:
        lease, reserved = attempts.lease(admitted.execution_id,
                                         expected_version=admitted.status_version, owner_id="owner")
        attempts.activate(lease.attempt_id, generation=lease.generation,
                          expected_execution_version=reserved.status_version)
    service = RuntimeControlService(store, attempts, DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: service)
    monkeypatch.setattr("openprogram.runtime.jobs.runner.runner_for_execution_store",
                        lambda _store: None)
    return store, service, admitted


def _stop(version: int, command_id: str = "stop-1") -> dict:
    return {"type": "execution.command", "action": "execution.cancel",
            "command_id": command_id, "execution_id": "turn",
            "expected_version": version, "payload": {}}


def test_a_stop_writes_its_conversation_off_the_loop(held, tmp_path, monkeypatch):
    """Stopping a turn that has not started writes its cancelled reply into
    the conversation: with the conversation held, the Stop waits for it in a
    worker thread while the loop keeps serving."""
    from openprogram.runtime.execution.control.submission import submit_execution_control

    store, _service, admitted = _execution(tmp_path, monkeypatch, start=False)
    stall, waiting, (command, latest) = _held_scenario(held, lambda: submit_execution_control(
        _stop(admitted.status_version), "cancel", actor=ACTOR, bound_session=SID, surface="ws"))
    assert waiting, "the stop did not wait for the held conversation"
    assert stall < MAX_STALL, f"the event loop stalled {stall:.2f}s on a stop"
    assert not isinstance(command, dict), command
    assert command.command_id == "stop-1"
    assert latest.status.value == "cancelled"


class _Driver:
    def __init__(self, takes: float = 0.0) -> None:
        self.cancels: list[str] = []
        self.takes = takes

    async def request_cancel(self, handle, command_id):
        from openprogram.runtime.execution.driver import DriverAck

        if self.takes:
            # An owner that answers after a pause: the other stops written
            # meanwhile find this one being handed over.
            await asyncio.sleep(self.takes)
        self.cancels.append(command_id)
        return DriverAck(command_id=command_id, attempt_id=handle)


@pytest.mark.parametrize("owner_takes", [0.0, 0.05])
def test_a_stop_reaches_a_running_owner_promptly_and_once(held, tmp_path, monkeypatch,
                                                          owner_takes):
    """The owner is told on the loop as soon as the stop is written; the
    same stop sent five times at once is one command, delivered once, even
    when the owner takes a moment to answer."""
    from openprogram.runtime.execution.control.submission import submit_execution_control
    from openprogram.runtime.execution.driver import DriverBinding

    store, service, admitted = _execution(tmp_path, monkeypatch, start=True)
    record = store.get_execution("turn")
    driver = _Driver(owner_takes)
    service.registry.bind(DriverBinding(
        execution_id="turn", attempt_id=record.current_attempt_id,
        generation=record.owner_lease["generation"], driver=driver,
        handle=record.current_attempt_id,
    ))

    async def scenario():
        start = time.monotonic()
        answers = await asyncio.gather(*(
            submit_execution_control(_stop(admitted.status_version), "cancel",
                                     actor=ACTOR, bound_session=SID, surface="ws")
            for _ in range(5)))
        return time.monotonic() - start, answers

    took, answers = asyncio.run(scenario())
    assert took < 5.0, f"the stop took {took:.2f}s"  # a loaded machine writes slowly; a stop held behind a lock waits far longer
    assert {command.command_id for command, _latest in answers} == {"stop-1"}
    assert driver.cancels == ["stop-1"]
    assert [command.command_id for command in store.list_commands("turn")] == ["stop-1"]
    assert all(latest.status.value == "cancelling" for _command, latest in answers)


# --- Taking the run slot without waiting ------------------------------------


def test_a_slot_taken_without_waiting_does_not_wait_for_the_lock(held):
    """A room event takes the slot with ``lock_wait=0``: a held lock is
    reported at once (``SessionLockTimeout``), never waited out."""
    from openprogram.runtime.conversation.run_control import run_slot
    from openprogram.state.store.session.files.session_lock import (
        SessionLockTimeout, session_interprocess_lock,
    )

    release = threading.Event()
    holder, _ = _hold(lambda: session_interprocess_lock(SID, timeout=5), 0, release)
    try:
        start = time.monotonic()
        with pytest.raises(SessionLockTimeout):
            run_slot.reserve(SID, "room-event", lock_wait=0)
        assert time.monotonic() - start < 0.2
    finally:
        release.set()
        holder.join()
    assert run_slot.reserve(SID, "room-event", lock_wait=0)
