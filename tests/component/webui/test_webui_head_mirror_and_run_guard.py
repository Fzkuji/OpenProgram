"""Webui head-mirror and run-guard regressions.

Four related defects, one root pattern: the store HEAD and the webui's
in-memory mirror (``_sessions[sid]["head_id"]`` / ``["messages"]``) must
move together, and turn entry points must not race a run in flight.

1. ``/merge`` (slash path, ``_execute._run_merge``): process_user_turn
   advanced the store HEAD but the mirror stayed pre-merge, so the
   ``_save_session`` right after execution flushed the old head back
   and orphaned the merge reply.
2. WS ``merge_branches``: same story, different entry point.
3. ``rewind``: the handler only synced the mirror when ``errors`` was
   empty — but ``rewind_to`` moves the head unconditionally, so a
   file-restore failure left mirror and store split.
4. WS ``chat``: the only turn entry point with no ``_is_run_active``
   guard; two racing clients could advance the same HEAD concurrently.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import types

import pytest


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


@pytest.fixture
def server_events(monkeypatch):
    """Record _set_active_head calls and broadcasts in arrival order."""
    from openprogram.webui import server as _s
    events: list[tuple] = []
    monkeypatch.setattr(
        _s, "_set_active_head",
        lambda sid, head: events.append(("head", sid, head)),
    )
    monkeypatch.setattr(
        _s, "_broadcast",
        lambda msg: events.append(("broadcast", json.loads(msg).get("type"))),
    )
    monkeypatch.setattr(_s, "refresh_context_stats", lambda sid: None)
    return events


def _merge_result(**overrides):
    base = dict(
        target_session_id="s1",
        target_assistant_id="asst_m",
        commit_id="commit_1",
        commit_parents=["p1"],
        final_text="merged",
        failed=False,
        error=None,
        base_peer=None,
    )
    base.update(overrides)
    return types.SimpleNamespace(**base)


# --- 1. /merge slash path ---------------------------------------------------

def test_run_merge_syncs_mirror_head_on_success(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui._execute import _run_merge
    import openprogram.agent.internals._merge as merge_mod

    responses: list[dict] = []
    monkeypatch.setattr(
        _s, "_broadcast_chat_response",
        lambda sid, mid, resp: responses.append(resp),
    )
    monkeypatch.setattr(
        merge_mod, "process_merge_turn", lambda **kw: _merge_result(),
    )

    _run_merge(session_id="s1", msg_id="m1",
               kwargs={"sub_sessions": ["peer_x"], "message": "go"},
               agent_id="main")

    assert ("head", "s1", "asst_m") in server_events, \
        "merge reply head never reached the webui mirror — the next " \
        "_save_session flushes the pre-merge head back (orphaned reply)"
    assert responses and responses[-1]["type"] == "result"


def test_run_merge_does_not_touch_head_on_failure(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui._execute import _run_merge
    import openprogram.agent.internals._merge as merge_mod

    monkeypatch.setattr(_s, "_broadcast_chat_response",
                        lambda sid, mid, resp: None)
    monkeypatch.setattr(
        merge_mod, "process_merge_turn",
        lambda **kw: _merge_result(failed=True, error="boom",
                                   target_assistant_id=None),
    )

    _run_merge(session_id="s1", msg_id="m1",
               kwargs={"sub_sessions": ["peer_x"], "message": "go"},
               agent_id="main")

    assert not [e for e in server_events if e[0] == "head"]


# --- 2. WS merge_branches ---------------------------------------------------

def test_merge_branches_syncs_mirror_before_session_reload(
    monkeypatch, server_events,
):
    from openprogram.webui import server as _s
    import openprogram.webui.ws_actions.merge as ws_merge

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(
        ws_merge, "_run",
        lambda *a, **kw: {
            "target_assistant_id": "asst1", "commit_id": "c1",
            "commit_parents": [], "final_text": "x",
            "failed": False, "error": None, "base_peer": None,
        },
    )

    ws = _FakeWS()
    asyncio.run(ws_merge.handle_merge_branches(ws, {
        "session_id": "t1",
        "peers": [{"session_id": "peer_a"}],
        "message": "m",
        "agent_id": "main",
    }))

    result = ws.sent[0]
    assert result["type"] == "merge_branches_result"
    assert not result["data"]["failed"]
    head_moves = [e for e in server_events if e[0] == "head"]
    assert head_moves == [("head", "t1", "asst1")]
    # Mirror sync must precede the session_reload that makes clients
    # re-pull — otherwise they reload the stale branch.
    reload_idx = server_events.index(("broadcast", "session_reload"))
    assert server_events.index(head_moves[0]) < reload_idx


# --- 3. rewind partial failure ---------------------------------------------

def _rewind_payload(**overrides):
    base = dict(
        session_id="s1", target_msg_id="u2", user_text="redo this",
        turns_reverted=1, nodes_rewound=2, total_restored_paths=[],
        new_head_id="n1", head_changed=True, status="committed", errors=[],
    )
    base.update(overrides)
    return base


def test_rewind_file_failure_does_not_sync_mirror(monkeypatch, server_events):
    from openprogram.webui import server as _s
    import openprogram.agent._rewind as rewind_mod
    from openprogram.webui.ws_actions.chat import handle_rewind

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(
        rewind_mod, "rewind_to",
        lambda sid, target, **_kwargs: _rewind_payload(
            status="rolled_back", new_head_id=None, head_changed=False,
            errors=["restore failed: f.txt"],
        ),
    )

    ws = _FakeWS()
    asyncio.run(handle_rewind(ws, {
        "session_id": "s1", "target_msg_id": "u2", "phase": "apply",
        "idempotency_key": "failure", "plan_hash": "sha256:test",
    }))

    assert not [event for event in server_events if event[0] == "head"]
    frame = ws.sent[0]
    assert frame["type"] == "rewind_result"
    assert frame["data"]["errors"] == ["restore failed: f.txt"]


def test_rewind_full_failure_leaves_mirror_alone(monkeypatch, server_events):
    from openprogram.webui import server as _s
    import openprogram.agent._rewind as rewind_mod
    from openprogram.webui.ws_actions.chat import handle_rewind

    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    # The _err path: nothing was rewound, head never moved.
    monkeypatch.setattr(
        rewind_mod, "rewind_to",
        lambda sid, target, **_kwargs: _rewind_payload(
            status="error", new_head_id=None, head_changed=False,
            errors=["node 'u2' not found"],
            turns_reverted=0, nodes_rewound=0, user_text="",
        ),
    )

    ws = _FakeWS()
    asyncio.run(handle_rewind(ws, {
        "session_id": "s1", "target_msg_id": "u2", "phase": "apply",
        "idempotency_key": "full-failure", "plan_hash": "sha256:test",
    }))

    assert not [e for e in server_events if e[0] == "head"]


# --- 4. chat run-active guard ----------------------------------------------

def test_handle_chat_rejects_while_run_active(monkeypatch, server_events):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.chat import handle_chat

    monkeypatch.setattr(_s, "_get_or_create_session",
                        lambda sid, **kw: {"id": sid})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: True)
    appended: list = []
    monkeypatch.setattr(_s, "_append_msg",
                        lambda conv, msg: appended.append(msg))

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "hi", "session_id": "s1"}))

    assert appended == [], "racing turn wrote into the DAG despite the guard"
    assert len(ws.sent) == 1
    frame = ws.sent[0]
    assert frame["type"] == "chat_response"
    assert frame["data"]["type"] == "error"
    assert frame["data"]["code"] == "run_active"
    assert frame["data"]["content"] == _s.RUN_ACTIVE_ERROR
    assert not any(f.get("type") == "chat_ack" for f in ws.sent)


def test_try_reserve_run_allows_only_one_concurrent_winner(monkeypatch):
    from openprogram.webui import server as _s

    session_id = "reserve-race"
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)

    try:
        with ThreadPoolExecutor(max_workers=16) as pool:
            won = list(pool.map(
                lambda i: _s._try_reserve_run(session_id, f"m{i}"),
                range(32),
            ))

        assert won.count(True) == 1
        assert won.count(False) == 31
        assert _s._is_run_active(session_id), \
            "an acquired reservation must block the next chat before runtime startup"
    finally:
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)


def test_activate_run_reservation_keeps_session_busy_during_handoff(monkeypatch):
    from openprogram.webui import server as _s

    session_id = "reservation-handoff"
    runtime = object()
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)
    _s._unregister_active_runtime(session_id)
    try:
        assert _s._try_reserve_run(session_id, "m1")
        observed: list[bool] = []
        real_register = _s._register_active_runtime

        def register_while_observing(sid, rt):
            observed.append(bool(
                _s._running_tasks.get(sid, {}).get("_reserved")))
            real_register(sid, rt)

        monkeypatch.setattr(_s, "_register_active_runtime", register_while_observing)
        assert _s._activate_run_reservation(session_id, "m1", runtime)
        assert observed == [True], "the reservation must remain until registration"
        with _s._running_tasks_lock:
            assert not _s._running_tasks[session_id].get("_reserved")
        assert _s._is_run_active(session_id)
    finally:
        _s._unregister_active_runtime(session_id)
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)


def test_registered_runtime_blocks_when_task_entry_is_temporarily_absent():
    from openprogram.webui import server as _s

    session_id = "runtime-only-handoff"
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)
    _s._register_active_runtime(session_id, object())
    try:
        assert _s._is_run_active(session_id)
        assert not _s._try_reserve_run(session_id, "m2")
    finally:
        _s._unregister_active_runtime(session_id)


def test_get_run_state_does_not_change_socket_focus(monkeypatch):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.session import handle_get_run_state

    ws = _FakeWS()
    ws._focused_session_id = "visible"
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: sid == "background")

    asyncio.run(handle_get_run_state(
        ws, {"session_id": "background"},
    ))

    assert ws._focused_session_id == "visible"
    assert ws.sent == [{
        "type": "run_state",
        "data": {"session_id": "background", "run_active": True},
    }]


def test_handle_chat_rejects_when_atomic_reservation_is_lost(
    monkeypatch, server_events,
):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.chat import handle_chat

    monkeypatch.setattr(_s, "_get_or_create_session",
                        lambda sid, **kw: {"id": sid})
    monkeypatch.setattr(_s, "_is_run_active", lambda sid: False)
    monkeypatch.setattr(_s, "_try_reserve_run", lambda sid, mid: False)
    appended: list = []
    monkeypatch.setattr(_s, "_append_msg",
                        lambda conv, msg: appended.append(msg))

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "hi", "session_id": "s1"}))

    assert appended == []
    assert len(ws.sent) == 1
    assert ws.sent[0]["data"]["code"] == "run_active"
    assert not any(f.get("type") == "chat_ack" for f in ws.sent)


def test_handle_chat_releases_reservation_when_setup_fails(monkeypatch):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.agent.session_config as session_config

    session_id = "setup-fails"
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)
    monkeypatch.setattr(_s, "_get_or_create_session",
                        lambda sid, **kw: {"id": sid})
    monkeypatch.setattr(
        session_config,
        "save_session_run_config",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("bad config")),
    )

    with pytest.raises(RuntimeError, match="bad config"):
        asyncio.run(handle_chat(
            _FakeWS(), {"text": "hi", "session_id": session_id},
        ))

    with _s._running_tasks_lock:
        assert session_id not in _s._running_tasks


@pytest.mark.parametrize("failure_phase", ["construct", "start"])
def test_chat_startup_failure_fails_exact_admission_and_clears_task(
    monkeypatch, failure_phase,
):
    """A post-ACK thread failure must not leave a queued execution or task."""
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions import chat as chat_actions
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.agent.session_config as session_config
    import openprogram.agent.session_db as session_db
    import openprogram.agent.surface_context as surface_context
    import openprogram.webui.ws_actions.session as session_actions
    import threading

    session_id = f"startup-failure-{failure_phase}"
    conv = {"id": session_id, "messages": []}
    failures: list[tuple[str, str]] = []
    released: list[object] = []
    events: list[dict] = []

    class _DB:
        def get_session(self, _sid):
            return {"extra_meta": {"_user_titled": True}}

        def update_session(self, _sid, **_fields):
            return None

    class _Admission:
        execution_id = "exec_startup_failure"
        status_version = 0

    class _Adapter:
        def __init__(self, **_kwargs):
            pass

        def admit(self, *_args, **_kwargs):
            return _Admission()

        def fail_admission(self, admission, *, reason_code):
            failures.append((admission.execution_id, reason_code))

    class _StartFailureThread:
        def start(self):
            if failure_phase == "start":
                raise RuntimeError("thread start unavailable")

    monkeypatch.setattr(_s, "_get_or_create_session", lambda sid, **kw: conv)
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda _sid: "main")
    monkeypatch.setattr(_s, "_append_msg", lambda target, msg: target["messages"].append(msg))
    monkeypatch.setattr(_s, "_emit_running_task_event", lambda _sid, **kw: events.append(kw))
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(session_db, "default_db", lambda: _DB())
    monkeypatch.setattr(
        session_config, "save_session_run_config",
        lambda *a, **kw: types.SimpleNamespace(
            tools_enabled=True, tools_override=None, web_search=False,
            toolset=None, thinking_effort="medium", permission_mode="ask",
            sandbox_enabled=None,
        ),
    )
    monkeypatch.setattr(surface_context, "capture", lambda *_a, **_kw: {"window_id": "w1"})
    monkeypatch.setattr(surface_context, "release_bindings", lambda value: released.append(value))
    monkeypatch.setattr(
        "openprogram.agent.production_driver.CanonicalAgentAdapter", _Adapter,
    )
    monkeypatch.setattr(
        threading, "Thread",
        (lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("thread constructor unavailable")))
        if failure_phase == "construct" else (lambda **_kwargs: _StartFailureThread()),
    )

    with pytest.raises(RuntimeError):
        asyncio.run(handle_chat(
            _FakeWS(), {"text": "hi", "session_id": session_id},
        ))

    assert failures == [("exec_startup_failure", "agent_runner_error")]
    assert released == [{"window_id": "w1"}]
    assert events and events[-1]["cleared_execution_id"] == "exec_startup_failure"
    with _s._running_tasks_lock:
        assert session_id not in _s._running_tasks


def test_handle_chat_starts_turn_when_ack_socket_is_gone(monkeypatch):
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions import chat as chat_actions
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.agent.session_config as session_config
    import openprogram.agent.session_db as session_db
    import openprogram.agent.internals._turn_lifecycle as turn_lifecycle
    import openprogram.webui.ws_actions.session as session_actions
    import threading

    session_id = "ack-disconnected"
    conv = {"id": session_id, "messages": []}
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)
    monkeypatch.setattr(_s, "_get_or_create_session", lambda sid, **kw: conv)
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda sid: "main")
    monkeypatch.setattr(_s, "_append_msg",
                        lambda target, msg: target["messages"].append(msg))
    monkeypatch.setattr(_s, "_emit_running_task_event", lambda sid: None)
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(
        session_config,
        "save_session_run_config",
        lambda *a, **kw: types.SimpleNamespace(
            tools_enabled=True,
            tools_override=None,
            web_search=False,
            toolset=None,
            thinking_effort="medium",
            permission_mode="default",
            sandbox_enabled=None,
        ),
    )
    monkeypatch.setattr(
        session_db,
        "default_db",
        lambda: types.SimpleNamespace(
            get_session=lambda sid: {"extra_meta": {"_user_titled": True}},
        ),
    )
    monkeypatch.setattr(turn_lifecycle, "insert_placeholder", lambda *a, **k: True)
    started: list[tuple] = []

    class _Thread:
        def __init__(self, *, target, args, kwargs, daemon):
            self.payload = (target, args, kwargs, daemon)

        def start(self):
            started.append(self.payload)

    monkeypatch.setattr(threading, "Thread", _Thread)

    class _GoneWS:
        async def send_text(self, text: str) -> None:
            raise ConnectionError("closed before ack")

    asyncio.run(handle_chat(
        _GoneWS(), {"text": "hi", "session_id": session_id},
    ))

    assert len(started) == 1, \
        "the persisted user turn must still start when its ACK cannot be delivered"
    assert conv["messages"] and conv["messages"][0]["content"] == "hi"

    competing_ws = _FakeWS()
    asyncio.run(handle_chat(
        competing_ws, {"text": "second", "session_id": session_id},
    ))
    assert competing_ws.sent[0]["data"]["code"] == "run_active"
    assert [m["content"] for m in conv["messages"]] == ["hi"]
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)


def test_chat_ack_exposes_only_a_durable_cancellable_execution(
    monkeypatch, tmp_path,
):
    from openprogram.agent.authority import owner_authority
    from openprogram.execution import default_store
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions import chat as chat_actions
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.agent.session_config as session_config
    import openprogram.agent.session_db as session_db
    import openprogram.store.session.session_store as store_module
    import openprogram.webui.ws_actions.session as session_actions
    import threading

    session_id = "durable-chat-ack"
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session(session_id, "main")
    conv = {"id": session_id, "messages": []}
    monkeypatch.setattr(session_db, "default_db", lambda: store)
    monkeypatch.setattr(store_module, "_default_store", store)
    monkeypatch.setattr(_s, "_get_or_create_session", lambda sid, **kw: conv)
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda sid: "main")
    running_frames: list[dict] = []

    def _emit_running_task_event(sid, **_kwargs):
        with _s._running_tasks_lock:
            task = dict(_s._running_tasks.get(sid) or {})
        if task:
            running_frames.append({"type": "running_task", "data": {
                "session_id": sid,
                "execution_id": task.get("execution_id"),
                "status_version": task.get("status_version"),
            }})

    monkeypatch.setattr(_s, "_emit_running_task_event", _emit_running_task_event)
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(
        session_config,
        "save_session_run_config",
        lambda *a, **kw: types.SimpleNamespace(
            tools_enabled=True,
            tools_override=None,
            web_search=False,
            toolset=None,
            thinking_effort="medium",
            permission_mode="default",
            sandbox_enabled=None,
        ),
    )

    class _Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    monkeypatch.setattr(threading, "Thread", _Thread)
    observed: dict = {}

    class _WS:
        async def send_text(self, text: str) -> None:
            frame = json.loads(text)
            if frame.get("type") != "chat_ack":
                return
            execution_id = frame["data"]["execution_id"]
            observed["execution_id"] = execution_id
            observed["status_version"] = frame["data"]["status_version"]
            observed["record_exists"] = default_store().get_execution(execution_id) is not None

    try:
        asyncio.run(handle_chat(
            _WS(), {"text": "hi", "session_id": session_id},
        ))
    finally:
        with _s._running_tasks_lock:
            _s._running_tasks.pop(session_id, None)

    assert observed["execution_id"]
    assert observed["record_exists"] is True
    assert running_frames[-1]["data"] == {
        "session_id": session_id,
        "execution_id": observed["execution_id"],
        "status_version": observed["status_version"],
    }

    class _ControlWS:
        def __init__(self):
            self.scope = {"state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            }}
            self.sent: list[dict] = []

        async def send_text(self, text: str) -> None:
            self.sent.append(json.loads(text))

    control_ws = _ControlWS()
    from openprogram.webui.ws_actions import runtime
    asyncio.run(runtime.ACTIONS["execution.cancel"](control_ws, {
        "action": "execution.cancel",
        "command_id": "chat-ack-stop",
        "execution_id": running_frames[-1]["data"]["execution_id"],
        "expected_version": running_frames[-1]["data"]["status_version"],
    }))
    command = next(frame for frame in control_ws.sent
                   if frame["type"] == "execution.command.updated")
    assert command["command"]["status"] == "applied"


def test_chat_ack_echoes_ask_when_permission_mode_missing_or_invalid(
    monkeypatch, tmp_path,
):
    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import server as _s
    from openprogram.webui.ws_actions import chat as chat_actions
    from openprogram.webui.ws_actions.chat import handle_chat
    import openprogram.agent.session_db as session_db
    import openprogram.store.session.session_store as store_module
    import openprogram.webui.ws_actions.session as session_actions
    import threading

    session_id = "perm-ack"
    store = SessionStore(tmp_path / "sessions-git")
    store.create_session(session_id, "main")
    conv = {"id": session_id, "messages": []}
    monkeypatch.setattr(session_db, "default_db", lambda: store)
    monkeypatch.setattr(store_module, "_default_store", store)
    monkeypatch.setattr(_s, "_get_or_create_session", lambda sid, **kw: conv)
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda sid: "main")
    monkeypatch.setattr(_s, "_emit_running_task_event", lambda sid: None)
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(threading, "Thread", lambda **kwargs: types.SimpleNamespace(start=lambda: None))

    def _ack_mode(cmd):
        observed: dict = {}

        class _WS:
            async def send_text(self, text: str) -> None:
                frame = json.loads(text)
                if frame.get("type") == "chat_ack":
                    observed["permission_mode"] = frame["data"].get("permission_mode")

        try:
            asyncio.run(handle_chat(_WS(), cmd))
        finally:
            with _s._running_tasks_lock:
                _s._running_tasks.pop(session_id, None)
            _s._unregister_active_runtime(session_id)
        return observed.get("permission_mode")

    assert _ack_mode({"text": "hi", "session_id": session_id}) == "ask"
    assert _ack_mode({
        "text": "hi", "session_id": session_id, "permission_mode": "nope",
    }) == "ask"
    assert _ack_mode({
        "text": "hi", "session_id": session_id, "permission_mode": "inherit",
    }) == "ask"
    assert _ack_mode({
        "text": "hi", "session_id": session_id, "permission_mode": "bypass",
    }) == "bypass"
