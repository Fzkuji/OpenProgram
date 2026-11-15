"""What a ``chat`` or ``compact`` request sends back before a turn runs.

Turn-ending errors name the session and carry a message id so the client
can resolve the sent bubble; a ``/skill`` refusal is a local reply, never a
model turn; a committed message reaches the other tabs; ``/compact`` holds
the run slot without holding up the requesting socket.
"""
from __future__ import annotations

import asyncio
import threading
import json
import types

import pytest


class _FakeWS:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))


def _clear_slot(session_id: str) -> None:
    from openprogram.interfaces.server import server as _s
    with _s._running_tasks_lock:
        _s._running_tasks.pop(session_id, None)


def test_an_ack_to_a_socket_already_holding_the_chat_adds_nothing():
    from openprogram.interfaces.server.ws_actions.chat.turn import _join_and_read

    ws = types.SimpleNamespace(_history_protocol=3, _transcript_views={"held"})
    assert asyncio.run(_join_and_read(ws, "held"))[0] == {}


def test_invalid_response_format_error_names_session_and_message():
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {
        "text": "hi", "session_id": "s-format",
        "response_format": {"type": "json_schema", "schema": "not a schema"},
    }))

    data = ws.sent[0]["data"]
    assert data["type"] == "error"
    assert data["session_id"] == "s-format"
    assert data["msg_id"]
    assert data["retry_query"] == "hi"


def test_local_error_frames_carry_a_message_id():
    from openprogram.interfaces.server.ws_actions.chat.turn_local_command import (
        _send_local_error,
    )

    ws = _FakeWS()
    asyncio.run(_send_local_error(ws, "s1", "function_call_failed", "boom"))

    data = ws.sent[0]["data"]
    assert (data["session_id"], data["code"], data["content"]) == (
        "s1", "function_call_failed", "boom")
    assert data["msg_id"]


@pytest.fixture
def chat_env(monkeypatch):
    """A handle_chat with the session and workspace phases stubbed."""
    from openprogram.interfaces.server import server as _s

    appended: list[dict] = []
    monkeypatch.setattr(_s, "_get_or_create_session", lambda sid, **kw: {"id": sid})
    monkeypatch.setattr(
        _s, "_append_msg", lambda conv, msg, **kw: appended.append(msg),
    )
    return appended


@pytest.mark.parametrize("found, code", [
    (None, "skill_not_installed"),
    ("ambiguous", "skill_ambiguous"),
])
def test_unusable_skill_is_a_local_reply_not_a_model_turn(
    monkeypatch, chat_env, found, code,
):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat import handle_chat
    import openprogram.extensions.skills.loader as loader

    def _resolve(name):
        if found == "ambiguous":
            raise loader.AmbiguousSkillError(name, ["a/x", "b/x"])
        return None

    monkeypatch.setattr(loader, "get_skill", lambda name: None)
    monkeypatch.setattr(loader, "resolve", _resolve)
    session_id = f"skill-{code}"
    _clear_slot(session_id)

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "/skill x do it", "session_id": session_id}))

    assert chat_env == []
    assert [f["data"]["type"] for f in ws.sent] == ["local_command"]
    reply = ws.sent[0]["data"]
    assert reply["code"] == code
    assert reply["session_id"] == session_id
    with _s._running_tasks_lock:
        assert session_id not in _s._running_tasks


def test_skill_body_reaches_the_model_only():
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest
    from openprogram.interfaces.server.ws_actions.chat.turn_skill import _skill_model_text

    req = _ChatRequest(
        cmd={}, text="/skill review check src/a.py", session_id="s1",
        agent_id=None, thinking_effort=None, tools_flag=None,
        web_search_flag=None, permission_mode=None, sandbox_flag=None,
        surface_ref=None, service_tier=None, response_format=None,
        attachments=None,
    )
    assert _skill_model_text(req) is None

    req.skill_name = "review"
    req.skill_activation = "Activating skill: **review**\n\nBODY\n\n---\n\n"
    assert _skill_model_text(req) == req.skill_activation + "check src/a.py"
    assert req.text == "/skill review check src/a.py"

    req.text = "/skill review"
    assert _skill_model_text(req).endswith("Please apply the review skill.")


def test_committed_message_is_broadcast_to_other_tabs_only(monkeypatch):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat.turn import _broadcast_user_message

    sent: list[tuple[dict, object]] = []
    monkeypatch.setattr(
        _s, "_broadcast",
        lambda msg, *, exclude=None: sent.append((json.loads(msg), exclude)),
    )
    sender = object()
    turn = types.SimpleNamespace(session_id="s1", msg_id="m1", side_branch_id=None, branch_id=None)

    _broadcast_user_message(_s, sender, turn, {
        "content": "hello", "source": "web", "timestamp": 2.0, "predecessor": "r0",
    })

    frame, excluded = sent[0]
    assert excluded is sender
    assert frame == {"type": "chat_response", "data": {
        "type": "user_message", "session_id": "s1", "msg_id": "m1",
        "content": "hello", "source": "web", "timestamp": 2.0, "predecessor": "r0",
    }}


def test_a_stopped_turn_ends_with_a_clear_that_names_it(monkeypatch):
    """A stop frees the slot while the execution is still ending, so the
    frame it sends still shows the turn; the turn's end sends the clear."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat.turn_launch import (
        _AdmittedTurn, _canonical_runner,
    )

    session_id = "stopped-turn-clear"
    sent: list[dict] = []
    monkeypatch.setattr(
        _s, "_broadcast", lambda text, **_kw: sent.append(json.loads(text)))
    ending = {"task": {
        "msg_id": "m1", "execution_id": "exec-1", "func_name": "agent",
        "status": "cancelling", "reason_code": "cancel.user", "status_version": 4,
    }}
    monkeypatch.setattr(_s, "_canonical_foreground_task", lambda _sid: ending["task"])

    class _Adapter:
        async def activate(self, _admission, on_activated):
            _s._release_session_occupancy_for_execution(
                {"session_id": session_id, "execution_id": "exec-1"})
            ending["task"] = None
            return None

    with _s._running_tasks_lock:
        _s._running_tasks[session_id] = {
            "msg_id": "m1", "execution_id": "exec-1", "func_name": "_chat",
            "started_at": 0,
        }
    turn = _AdmittedTurn(
        session_id=session_id, msg_id="m1", conv={}, adapter=_Adapter(),
        admission=object(), surface_context=None,
        release_surface_bindings=lambda _context: None,
        response_format=None, execution_id="exec-1",
    )
    try:
        _canonical_runner(_s, turn)()
    finally:
        _clear_slot(session_id)

    assert [frame["type"] for frame in sent] == ["running_task", "running_task_clear"]
    assert sent[0]["data"]["status"] == "cancelling"
    clear = sent[1]["data"]
    assert (clear["session_id"], clear["msg_id"], clear["execution_id"]) == (
        session_id, "m1", "exec-1")


def test_a_stopped_turns_end_does_not_announce_the_slots_next_owner(monkeypatch):
    """/compact reserves the slot between the stop and the end of the stopped
    turn's thread. That end clears the stopped turn by name and never
    announces the next owner; the compaction announces itself and its
    release clears it by its own name."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat.turn_launch import (
        _AdmittedTurn, _canonical_runner,
    )

    session_id = "stopped-turn-then-reserved"
    sent: list[dict] = []
    monkeypatch.setattr(
        _s, "_broadcast", lambda text, **_kw: sent.append(json.loads(text)))
    monkeypatch.setattr(_s, "_canonical_foreground_task", lambda _sid: None)

    class _Adapter:
        async def activate(self, _admission, on_activated):
            _s._release_session_occupancy_for_execution(
                {"session_id": session_id, "execution_id": "exec-1"})
            assert _s.reserve_operation(session_id, "compact", "compact-1") is None
            return None

    with _s._running_tasks_lock:
        _s._running_tasks[session_id] = {
            "msg_id": "m1", "execution_id": "exec-1", "func_name": "_chat",
            "started_at": 0,
        }
    turn = _AdmittedTurn(
        session_id=session_id, msg_id="m1", conv={}, adapter=_Adapter(),
        admission=object(), surface_context=None,
        release_surface_bindings=lambda _context: None,
        response_format=None, execution_id="exec-1",
    )
    try:
        _canonical_runner(_s, turn)()
        with _s._running_tasks_lock:
            assert _s._running_tasks[session_id]["msg_id"] == "compact-1"
        _s._release_run_reservation(session_id, "compact-1")
    finally:
        _clear_slot(session_id)

    turn_frames = [frame for frame in sent if frame["data"].get("msg_id") != "compact-1"]
    assert [frame["type"] for frame in turn_frames] == ["running_task_clear", "running_task_clear"]
    assert all(
        (frame["data"]["msg_id"], frame["data"]["execution_id"]) == ("m1", "exec-1")
        for frame in turn_frames)
    own = [(frame["type"], frame["data"].get("op_kind")) for frame in sent
           if frame["data"].get("msg_id") == "compact-1"]
    assert own == [("running_task", "compact"), ("running_task_clear", None)]


@pytest.fixture
def compact_env(monkeypatch):
    from openprogram.interfaces.server import server as _s
    import openprogram.runtime.context.session_db as session_db
    import openprogram.runtime.conversation.turn_runtime as turn_runtime
    from openprogram.interfaces.server.ws_actions.chat import commands

    calls: list[str] = []
    monkeypatch.setattr(
        session_db, "default_db",
        lambda: types.SimpleNamespace(get_session=lambda sid: {"id": sid}),
    )
    monkeypatch.setattr(commands, "_db_agent_id", lambda sid: "main")
    monkeypatch.setattr(_s, "refresh_context_stats", lambda *a: None)
    monkeypatch.setattr(
        turn_runtime, "trigger_compaction",
        lambda sid, **kw: calls.append(sid),
    )
    return calls


def test_compact_is_refused_while_a_turn_runs(compact_env):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat.commands import handle_compact

    session_id = "compact-busy"
    _clear_slot(session_id)
    assert _s._try_reserve_run(session_id, "turn")
    try:
        ws = _FakeWS()
        asyncio.run(handle_compact(ws, {"session_id": session_id}))
    finally:
        _s._release_run_reservation(session_id, "turn")

    assert compact_env == []
    data = ws.sent[0]["data"]
    assert (data["type"], data["code"], data["session_id"], data["kind"]) == (
        "compaction_failed", "run_active", session_id, "reply")
    # The refusal says what runs on the branch.
    assert data["error"].startswith("A reply is being written on this branch")


def test_compact_holds_the_slot_and_runs_in_the_background(compact_env):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat import commands

    session_id = "compact-idle"
    _clear_slot(session_id)
    observed: dict = {}

    async def _main():
        ws = _FakeWS()
        await commands.handle_compact(ws, {"session_id": session_id})
        # The handler returned with compaction still pending: the slot is held.
        observed["active"] = _s._is_run_active(session_id)
        await asyncio.gather(*commands._compact_tasks)
        observed["sent"] = ws.sent

    asyncio.run(_main())

    assert observed["active"] is True
    assert observed["sent"] == []
    assert compact_env == [session_id]
    with _s._running_tasks_lock:
        assert session_id not in _s._running_tasks


def test_compaction_ends_on_the_wire_only_after_its_slot_is_free(compact_env, monkeypatch):
    """A client reloads the conversation on ``compaction_finished``; that load
    must find the conversation idle, so the frame follows the release."""
    from openprogram.interfaces.server import server as _s
    import openprogram.runtime.conversation.turn_runtime as turn_runtime
    from openprogram.interfaces.server.ws_actions.chat import commands

    session_id = "compact-ending-order"
    _clear_slot(session_id)
    seen: list[tuple[str, bool]] = []
    monkeypatch.setattr(_s, "_broadcast_chat_response", lambda sid, mid, data: seen.append(
        (data.get("type"), _s._is_run_active(sid))))
    monkeypatch.setattr(_s, "refresh_context_stats", lambda sid, mid="": seen.append(
        ("context_stats", _s._is_run_active(sid))))

    def _compact(sid, *, on_event, **_kw):
        on_event({"type": "chat_response", "data": {"type": "compaction_started"}})
        on_event({"type": "chat_response", "data": {"type": "compaction_finished"}})

    monkeypatch.setattr(turn_runtime, "trigger_compaction", _compact)

    async def _main():
        await commands.handle_compact(_FakeWS(), {"session_id": session_id})
        await asyncio.gather(*commands._compact_tasks)

    asyncio.run(_main())

    assert seen == [
        ("compaction_started", True),
        ("compaction_finished", False),
        ("context_stats", False),
    ]


def test_a_compaction_is_the_branchs_operation_on_every_surface(monkeypatch):
    """/compact is the selected branch's foreground operation: it announces
    itself with its kind and op_id, a load shows it the same way (no reply
    row, a Stop by op_id), and its release sends the clear that closes it."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.session.load import _send_running_task_state

    session_id = "compact-shown"
    _clear_slot(session_id)
    sent: list[dict] = []
    monkeypatch.setattr(_s, "_broadcast", lambda text, **_kw: sent.append(json.loads(text)))
    monkeypatch.setattr(_s, "_canonical_foreground_task", lambda _sid: None)

    assert _s.reserve_operation(session_id, "compact", "compact_1") is None
    ws = _FakeWS()
    try:
        run_task = _s._run_identity(session_id)
        asyncio.run(_send_running_task_state(ws, _s, session_id, run_task))
        # A second operation on the same branch is refused and named.
        refusal = _s.reserve_operation(session_id, "rewind", "rewind_1")
        _s._release_run_reservation(session_id, "compact_1")
    finally:
        _clear_slot(session_id)

    assert run_task["op_kind"] == "compact" and run_task["op_id"].startswith("op_")
    assert run_task["status_version"] == 1 and run_task["execution_id"] == ""
    shown = ws.sent[0]["data"]
    assert (ws.sent[0]["type"], shown["msg_id"], shown["reply_id"], shown["op_kind"], shown["op_id"]) == (
        "running_task", "compact_1", None, "compact", run_task["op_id"])
    assert (refusal["code"], refusal["kind"]) == ("run_active", "compact")
    assert "compacted" in refusal["error"]
    assert [(f["type"], f["data"].get("msg_id"), f["data"].get("op_kind")) for f in sent] == [
        ("running_task", "compact_1", "compact"), ("running_task_clear", "compact_1", None)]


def test_a_compaction_a_run_state_probe_showed_ends_with_a_clear(monkeypatch):
    """``get_run_state`` hands a client the operation as its running slot
    just as a load does, and its release sends the clear."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.session.run_state import handle_get_run_state

    session_id = "compact-run-state"
    _clear_slot(session_id)
    sent: list[dict] = []
    monkeypatch.setattr(_s, "_broadcast", lambda text, **_kw: sent.append(json.loads(text)))
    monkeypatch.setattr(_s, "_canonical_foreground_task", lambda _sid: None)

    assert _s.reserve_operation(session_id, "compact", "compact_1") is None
    ws = _FakeWS()
    try:
        asyncio.run(handle_get_run_state(ws, {"session_id": session_id}))
        _s._release_run_reservation(session_id, "compact_1")
    finally:
        _clear_slot(session_id)

    state = ws.sent[0]["data"]
    assert (state["run_active"], state["run_task"]["msg_id"], state["run_task"]["op_kind"]) == (
        True, "compact_1", "compact")
    assert [(f["type"], f["data"].get("msg_id")) for f in sent][-1] == (
        "running_task_clear", "compact_1")


def test_stop_by_op_id_ends_a_compaction(compact_env, monkeypatch):
    """Stop names the operation: its cancel event reaches the summary request,
    the compaction ends and its slot is free."""
    from openprogram.interfaces.server import server as _s
    import openprogram.runtime.conversation.turn_runtime as turn_runtime
    from openprogram.interfaces.server.ws_actions.chat import commands

    session_id = "compact-stopped"
    _clear_slot(session_id)
    entered = threading.Event()
    seen: dict = {}

    def _compact(sid, *, on_event, cancel_event=None, **_kw):
        seen["event"] = cancel_event
        entered.set()
        assert cancel_event is not None and cancel_event.wait(10)
        on_event({"type": "chat_response", "data": {"type": "compaction_failed", "error": "cancelled"}})

    monkeypatch.setattr(turn_runtime, "trigger_compaction", _compact)
    monkeypatch.setattr(_s, "_broadcast_chat_response", lambda sid, mid, data: seen.setdefault("ending", data))

    async def _main():
        await commands.handle_compact(_FakeWS(), {"session_id": session_id})
        await asyncio.to_thread(entered.wait, 10)
        op_id = _s._run_identity(session_id)["op_id"]
        ws = _FakeWS()
        await commands.handle_operation_cancel(ws, {"session_id": session_id, "op_id": op_id})
        seen["cancel"] = ws.sent[0]
        await asyncio.gather(*commands._compact_tasks)
        missing = _FakeWS()
        await commands.handle_operation_cancel(missing, {"session_id": session_id, "op_id": op_id})
        seen["again"] = missing.sent[0]

    asyncio.run(_main())

    assert seen["cancel"]["type"] == "operation_cancel_result"
    assert (seen["cancel"]["data"]["ok"], seen["cancel"]["data"]["op_kind"],
            seen["cancel"]["data"]["op_version"]) == (True, "compact", 2)
    assert seen["event"].is_set() and seen["ending"]["type"] == "compaction_failed"
    assert seen["again"]["data"]["code"] == "not_found"
    assert not _s._is_run_active(session_id)


def test_pre_ack_error_echoes_the_client_request_id(monkeypatch, chat_env):
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat import handle_chat
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _client_request_id

    monkeypatch.setattr(_s, "_try_reserve_run", lambda sid, mid, **_k: False)
    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {
        "text": "hi", "session_id": "s-echo", "client_request_id": "req-7",
    }))

    data = ws.sent[0]["data"]
    assert (data["code"], data["client_request_id"]) == ("run_active", "req-7")
    assert _client_request_id.get() is None

    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "hi", "session_id": "s-echo"}))
    assert "client_request_id" not in ws.sent[0]["data"]


def test_a_refused_admission_ends_the_turn_with_its_reason(monkeypatch, chat_env):
    """A refusal at admission (too large, a bad image) reaches the sender as
    the turn's error with its code, not as a bare ``handler_error``."""
    from openprogram.interfaces.server import server as _s
    from openprogram.interfaces.server.ws_actions.chat import handle_chat, turn
    from openprogram.runtime.conversation.production_driver import AgentDriverError

    def refuse(*_args, **_kwargs):
        raise AgentDriverError("image_too_large", "Images exceed the image size limit")

    monkeypatch.setattr(turn, "_save_run_config",
                        lambda *_a, **_k: (types.SimpleNamespace(), "ask"))
    monkeypatch.setattr(turn, "_admit_and_commit", refuse)
    session_id = "s-admission-refused"
    _clear_slot(session_id)
    ws = _FakeWS()
    asyncio.run(handle_chat(ws, {"text": "look", "session_id": session_id,
                                 "client_request_id": "req-9"}))

    data = ws.sent[-1]["data"]
    assert (data["type"], data["code"], data["kind"]) == (
        "error", "image_too_large", "image_too_large")
    assert data["content"] == "Images exceed the image size limit"
    assert (data["session_id"], data["client_request_id"]) == (session_id, "req-9")
    assert data["msg_id"]
    _clear_slot(session_id)


def _local_request(text: str):
    from openprogram.interfaces.server.ws_actions.chat.turn_request import _ChatRequest

    return _ChatRequest(
        cmd={}, text=text, session_id="s-goal", agent_id=None, thinking_effort=None,
        tools_flag=None, web_search_flag=None, permission_mode=None, sandbox_flag=None,
        surface_ref=None, service_tier=None, response_format=None, attachments=None,
    )


def _run_goal_command(monkeypatch, out: dict):
    from openprogram.interfaces.server.ws_actions.chat import turn_local_command

    resolved = types.SimpleNamespace(command_name="goal", raw_args="ship it",
                                     local_handler=lambda _ctx, _args: out)
    monkeypatch.setattr(turn_local_command, "_resolve_local_command", lambda _t, _s: resolved)
    ws, req = _FakeWS(), _local_request("/goal ship it")
    goes_on = asyncio.run(turn_local_command._run_local_command(ws, req))
    return ws, req, goes_on


def test_a_goal_is_set_by_its_labelled_message_with_no_reply_line(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat.turn import _build_user_msg

    ws, req, goes_on = _run_goal_command(monkeypatch, {
        "text": "Goal saved; working in this conversation.", "send_text": "ship it", "starts_goal": True})
    assert goes_on and ws.sent == []
    assert (req.text, req.starts_goal) == ("ship it", True)
    user_msg = _build_user_msg(req, "m1", {"action": None}, {})
    assert (user_msg["content"], user_msg["starts_goal"]) == ("ship it", True)


def test_a_goal_that_was_not_set_still_answers_in_text(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat.turn import _build_user_msg

    ws, req, goes_on = _run_goal_command(monkeypatch, {
        "text": "An unfinished Goal exists; resume or cancel it first", "send_text": None})
    assert not goes_on
    assert [(f["data"]["type"], f["data"]["content"]) for f in ws.sent] == [
        ("local_command", "An unfinished Goal exists; resume or cancel it first")]
    assert "starts_goal" not in _build_user_msg(_local_request("hi"), "m2", {"action": None}, {})


def _draft_command(monkeypatch, out: dict, cmd: dict):
    """``handle_chat`` for a local command sent from a draft tab: the turn
    phases after run config are stubbed, and what reached them recorded."""
    from openprogram.interfaces.server.ws_actions.chat import handle_chat, turn, turn_local_command
    from openprogram.runtime.conversation.production_driver import AgentDriverError

    resolved = types.SimpleNamespace(command_name="goal", raw_args="",
                                     local_handler=lambda _ctx, _args: out)
    monkeypatch.setattr(turn_local_command, "_resolve_local_command", lambda _t, _s: resolved)
    seen: dict = {}

    def save(_s, req, conv, msg_id):
        seen.update(session_id=req.session_id, conv_id=conv["id"], text=req.text,
                    permission_mode=req.permission_mode, starts_goal=req.starts_goal)
        return types.SimpleNamespace(), "plan"

    def stop_before_commit(*_a, **_k):
        raise AgentDriverError("stub", "stopped before commit")

    monkeypatch.setattr(turn, "_save_run_config", save)
    monkeypatch.setattr(turn, "_admit_and_commit", stop_before_commit)
    _clear_slot(cmd["session_id"])
    ws = _FakeWS()
    asyncio.run(handle_chat(ws, cmd))
    _clear_slot(cmd["session_id"])
    return ws, seen


def test_a_goal_from_a_draft_starts_the_drafts_own_conversation(monkeypatch, chat_env):
    """A /goal sent as a draft's first message goes on as that draft's first
    turn: the conversation is opened and its run config saved under the
    draft's id, with the draft's settings, exactly as a typed message."""
    ws, seen = _draft_command(monkeypatch, {
        "text": "Goal saved; working in this conversation.", "send_text": "ship it",
        "starts_goal": True,
    }, {"text": "/goal ship it", "session_id": "local_draft1", "permission_mode": "plan",
        "client_request_id": "req-g"})
    assert seen == {"session_id": "local_draft1", "conv_id": "local_draft1", "text": "ship it",
                    "permission_mode": "plan", "starts_goal": True}
    assert all(f["data"]["session_id"] == "local_draft1" for f in ws.sent)


def test_a_reply_only_command_from_a_draft_answers_in_the_draft(monkeypatch, chat_env):
    ws, seen = _draft_command(monkeypatch, {"text": "No goal set.", "send_text": None},
                              {"text": "/goal status", "session_id": "local_draft2",
                               "client_request_id": "req-s"})
    assert seen == {}, "no turn and no run config: nothing to create"
    assert [(f["data"]["type"], f["data"]["session_id"], f["data"]["content"],
             f["data"]["client_request_id"]) for f in ws.sent] == [
        ("local_command", "local_draft2", "No goal set.", "req-s")]


def test_the_ack_of_a_goal_message_says_it_set_the_goal(monkeypatch):
    from openprogram.interfaces.server.ws_actions.chat import turn

    async def no_read(_ws, _sid):
        return {}, lambda: None

    monkeypatch.setattr(turn, "_join_and_read", no_read)
    adm = types.SimpleNamespace(
        session_id="local_d", msg_id="m1", execution_id="e1", side_branch_id=None,
        admission=types.SimpleNamespace(status_version=1), conv={})
    for starts, expected in ((True, True), (False, None)):
        req = _local_request("ship it")
        req.starts_goal = starts
        req.turn_permission_mode = None
        ws = _FakeWS()
        asyncio.run(turn._send_chat_ack(ws, req, adm, "ask"))
        assert ws.sent[0]["data"].get("starts_goal") is expected
