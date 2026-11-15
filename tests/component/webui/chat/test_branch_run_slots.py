"""Two branches of one conversation run at the same time.

A message naming a recorded branch that is not selected (``branch_id``)
runs on that branch's own run slot, beside the selected branch's turn: both
finish, each branch keeps its own head and messages, the selected branch
stays selected, a stop of one leaves the other running, and ``run_active``
keeps meaning the selected branch's turn while ``running_branches`` and the
``branch_running_task`` frames name every branch.
"""
from __future__ import annotations

import asyncio
import json
import re
import threading
import time
from types import SimpleNamespace

import pytest

from openprogram.runtime.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.runtime.execution.driver import DriverRegistry
from openprogram.runtime.execution.model import ExecutionStatus


class _WebSocket:
    def __init__(self) -> None:
        from openprogram.runtime.permissions.authority import owner_authority

        self.frames: list[dict] = []
        self.scope = {"state": {"authority": owner_authority("owner/install/0123456789abcdef")}}

    async def send_text(self, value: str) -> None:
        self.frames.append(json.loads(value))


class _GatedProvider:
    """Replies to each prompt once its gate opens, so turns overlap."""

    requires_credentials = False

    def __init__(self) -> None:
        self.entered: dict[str, threading.Event] = {}
        self.gates: dict[str, threading.Event] = {}
        self.contexts: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def gate(self, prompt: str) -> threading.Event:
        with self._lock:
            self.entered.setdefault(prompt, threading.Event())
            return self.gates.setdefault(prompt, threading.Event())

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        texts = []
        for message in context.messages:
            content = getattr(message, "content", "")
            if isinstance(content, str):
                texts.append(content)
            else:
                texts.extend(getattr(block, "text", "") for block in content or [])
        # The prompt reaches the model wrapped with its speaker.
        prompt = next((match.group(1) for text in reversed(texts)
                       if (match := re.search(r'"content":"(on [^"]*)"', text))), "")
        gate = self.gate(prompt)
        self.contexts[prompt] = texts
        self.entered[prompt].set()
        while not gate.is_set():
            await asyncio.sleep(0.005)
        from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText

        scripted = ScriptedProvider()
        scripted.add_response(ScriptedText(f"reply to {prompt}"))
        async for event in scripted.stream_simple(model, context, options):
            yield event


def _wait(predicate, timeout: float = 30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("condition did not become true")


@pytest.fixture
def two_branches(tmp_path, monkeypatch):
    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.state.store import SessionNodeWriter
    from openprogram.state.store.session.nodes import Call, ROLE_USER
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import chat as chat_actions
    from openprogram.interfaces.server.ws_actions import session as session_actions
    import openprogram.runtime.conversation.turn_runtime as dispatcher
    import openprogram.runtime.conversation.turn_runtime.loop_runner as loop_runner
    import openprogram.runtime.agent.questions as questions
    import openprogram.runtime.conversation.session_config as session_config
    import openprogram.runtime.context.session_db as session_db
    import openprogram.models.providers.catalog.api_registry as api_registry
    import openprogram.state.store.session.session_store as session_store_module
    from openprogram.models.providers.catalog.api_registry import get_api_provider, register_api_provider
    from openprogram.models.providers.types import Model
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver

    store = ExecutionStore(tmp_path / "execution.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    sessions = SessionStore(tmp_path / "sessions-git")
    session_id = "branch-run-slots"
    sessions.create_session(session_id, "main")
    monkeypatch.setattr(session_db, "default_db", lambda: sessions)
    monkeypatch.setattr(session_store_module.shared, "_default_store", sessions)

    # Branch A: hi -> hello. Branch B: other -> o. A is selected again.
    writer = SessionNodeWriter(sessions, session_id)
    writer.append(Call(id="ROOT", role=ROLE_USER, output="", metadata={"display": "root"}))
    writer.append(Call(id="u1", role=ROLE_USER, output="hi", caller="ROOT", predecessor="ROOT"))
    sessions.set_head(session_id, "u1")
    sessions.append_message(session_id, {"id": "r1", "role": "assistant", "content": "hello", "predecessor": "u1"})
    writer.append(Call(id="u2", role=ROLE_USER, output="other", caller="ROOT", predecessor="ROOT"))
    sessions.set_head(session_id, "u2")
    sessions.append_message(session_id, {"id": "r2", "role": "assistant", "content": "o", "predecessor": "u2"})
    sessions.set_head(session_id, "r1")
    row = sessions.get_session(session_id)
    branch_a = row["active_branch_id"]
    branch_b = next(key for key, ref in row["branch_refs"].items() if ref["head_id"] == "r2")

    monkeypatch.setattr(server, "_get_or_create_session",
                        lambda *_args, **_kwargs: {"id": session_id, "messages": []})
    monkeypatch.setattr(chat_actions.turn, "_db_agent_id", lambda _session_id: "main")
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    frames: list[dict] = []
    monkeypatch.setattr(server, "_broadcast", lambda value, *_a, **_k: frames.append(json.loads(value)))
    monkeypatch.setattr(server, "_broadcast_envelope", lambda env, *_a, **_k: frames.append(env), raising=False)

    provider = _GatedProvider()
    api_name = "branch-slots-api"
    previous_provider = get_api_provider(api_name)
    register_api_provider(api_name, provider)
    model = Model(id="branch-slots-model", name="branch-slots-model", api=api_name,
                  provider="branch-slots-provider", base_url="http://branch-slots.invalid")
    monkeypatch.setattr(dispatcher, "_load_agent_profile",
                        lambda _id: {"id": "main", "model": "branch-slots-provider/branch-slots-model"})
    monkeypatch.setattr(dispatcher, "_resolve_model", lambda _profile, _override: model)
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *_args, **_kwargs: [])
    registry = questions.QuestionRegistry()
    monkeypatch.setattr(questions, "get_question_registry", lambda: registry)
    config_writes: list[dict] = []

    def save_config(*_args, **kwargs):
        config_writes.append(kwargs)
        return SimpleNamespace(tools_override=None, web_search=False, thinking_effort="medium",
                               permission_mode=kwargs.get("permission_mode"), permission_rules=None,
                               sandbox_enabled=None, additional_working_dirs=[])

    monkeypatch.setattr(session_config, "save_session_run_config", save_config)
    monkeypatch.setattr(session_config, "permission_from_config",
                        lambda config, default=None: config.permission_mode or default or "bypass")
    monkeypatch.setattr(session_config, "project_defaults", lambda _sid: {"permission_mode": "bypass"})

    async def activate(attempt, activation):
        driver = AgentProductionDriver(store, control_service=control, question_registry=registry)
        return await driver.activate(attempt, activation)

    control.activator = activate
    harness = SimpleNamespace(store=store, sessions=sessions, session_id=session_id, server=server,
                              provider=provider, frames=frames, branch_a=branch_a, branch_b=branch_b,
                              config_writes=config_writes)
    try:
        yield harness
    finally:
        for gate in list(provider.gates.values()):
            gate.set()
        deadline = time.monotonic() + 30.0
        while time.monotonic() < deadline and server._is_run_active(session_id):
            time.sleep(0.01)
        with server._running_tasks_lock:
            server._running_tasks.pop(session_id, None)
            for key in [key for key in server._branch_running_tasks if key[0] == session_id]:
                server._branch_running_tasks.pop(key, None)
        if previous_provider is None:
            api_registry._registry.pop(api_name, None)
            api_registry._original_registry.pop(api_name, None)
        else:
            register_api_provider(api_name, previous_provider)


def _send(harness, text: str, **extra) -> list[dict]:
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    ws = _WebSocket()
    asyncio.run(handle_chat(ws, {"text": text, "session_id": harness.session_id,
                                 "permission_mode": "bypass", **extra}))
    return ws.frames


def _ack(frames: list[dict]) -> dict:
    return next(frame["data"] for frame in frames if frame.get("type") == "chat_ack")


def _error_code(frames: list[dict]) -> str | None:
    return next((frame["data"].get("code") or frame["data"].get("error_code")
                 for frame in frames if frame.get("type") == "chat_response"
                 and frame["data"].get("type") == "error"), None)


def _cancel(execution) -> None:
    from openprogram.interfaces.server.ws_actions import runtime

    asyncio.run(runtime.ACTIONS["execution.cancel"](_WebSocket(), {
        "type": "execution.command", "action": "execution.cancel", "command_id": "stop-branch-b",
        "execution_id": execution.execution_id, "expected_version": execution.status_version,
        "payload": {},
    }))


def _status(harness, execution_id: str) -> ExecutionStatus:
    return harness.store.get_execution(execution_id).status


def test_two_branches_run_at_once_and_keep_their_own_heads(two_branches):
    h = two_branches
    server, sid = h.server, h.session_id

    ack_a = _ack(_send(h, "on A"))
    _wait(lambda: h.provider.gate("on A") and h.provider.entered["on A"].is_set())
    ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
    # The second turn reached the model while the first one is still there.
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())
    assert ack_b["branch_id"] == h.branch_b and "branch_id" not in ack_a
    # The composer's settings went to the branch the message names.
    assert h.config_writes[-1].get("branch_id") == h.branch_b
    assert "branch_id" not in h.config_writes[0]

    # One turn per branch: a second message to either branch waits.
    assert _error_code(_send(h, "on A again")) == "run_active"
    assert _error_code(_send(h, "on B again", branch_id=h.branch_b)) == "run_active"

    # run_active keeps meaning the selected branch's turn; every turn is listed with its branch.
    assert server._is_selected_run_active(sid)
    assert server._run_identity(sid)["execution_id"] == ack_a["execution_id"]
    running = {item["branch_id"]: item for item in server._running_branches(sid)}
    assert running[h.branch_a]["execution_id"] == ack_a["execution_id"] and running[h.branch_a]["selected"]
    assert running[h.branch_b]["execution_id"] == ack_b["execution_id"] and not running[h.branch_b]["selected"]

    # Stop B: A keeps running and the conversation stays running.
    _cancel(h.store.get_execution(ack_b["execution_id"]))
    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.CANCELLED)
    _wait(lambda: not server._branch_run_active(sid, h.branch_b))
    assert _status(h, ack_a["execution_id"]) is ExecutionStatus.RUNNING
    assert server._is_selected_run_active(sid)
    assert [item["branch_id"] for item in server._running_branches(sid)] == [h.branch_a]
    assert h.sessions.get_session(sid)["status"] == "running"

    h.provider.gate("on A").set()
    _wait(lambda: _status(h, ack_a["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._is_run_active(sid))

    row = h.sessions.get_session(sid)
    reply_a, reply_b = f"{ack_a['msg_id']}_reply", f"{ack_b['msg_id']}_reply"
    # The selected branch stays selected and its head is its own reply.
    assert row["active_branch_id"] == h.branch_a
    assert row["head_id"] == reply_a
    assert row["branch_refs"][h.branch_a]["head_id"] == reply_a
    assert row["branch_refs"][h.branch_b]["head_id"] == reply_b
    line_a = [item["id"] for item in h.sessions.get_branch(sid, reply_a)]
    line_b = [item["id"] for item in h.sessions.get_branch(sid, reply_b)]
    assert line_a[-4:] == ["u1", "r1", ack_a["msg_id"], reply_a]
    assert line_b[-4:] == ["u2", "r2", ack_b["msg_id"], reply_b]
    # Each turn read only its own branch.
    assert "other" not in h.provider.contexts["on A"] and "hi" in h.provider.contexts["on A"]
    assert "hi" not in h.provider.contexts["on B"] and "other" in h.provider.contexts["on B"]

    # Old clients' frames describe the selected branch only; B has its own.
    legacy = [frame["data"] for frame in h.frames if frame.get("type") == "running_task"]
    assert legacy and all(item.get("execution_id") != ack_b["execution_id"] for item in legacy)
    started = [frame["data"] for frame in h.frames if frame.get("type") == "branch_running_task"]
    cleared = [frame["data"] for frame in h.frames if frame.get("type") == "branch_running_task_clear"]
    assert any(item["branch_id"] == h.branch_b and item["execution_id"] == ack_b["execution_id"]
               for item in started)
    assert any(item["branch_id"] == h.branch_b for item in cleared)
    # The turn's own frames name its branch.
    tagged = [frame for frame in h.frames if isinstance(frame.get("data"), dict)
              and frame["data"].get("msg_id") == ack_b["msg_id"]
              and frame.get("type") == "chat_response"]
    assert tagged and all(frame["data"].get("branch_id") == h.branch_b for frame in tagged)


def test_a_message_to_the_selected_branch_by_name_runs_as_before(two_branches):
    h = two_branches
    ack = _ack(_send(h, "on A", branch_id=h.branch_a))
    assert "branch_id" not in ack
    _wait(lambda: h.provider.gate("on A") and h.provider.entered["on A"].is_set())
    with h.server._running_tasks_lock:
        assert h.server._running_tasks[h.session_id]["msg_id"] == ack["msg_id"]
        assert not h.server._branch_running_tasks
    h.provider.gate("on A").set()
    _wait(lambda: _status(h, ack["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not h.server._is_run_active(h.session_id))
    assert h.sessions.get_session(h.session_id)["head_id"] == f"{ack['msg_id']}_reply"


def test_a_message_to_an_unknown_branch_is_refused(two_branches):
    h = two_branches
    assert _error_code(_send(h, "on nowhere", branch_id="branch_missing")) == "branch_gone"
    assert not h.server._is_run_active(h.session_id)


def test_operations_on_one_branch_run_beside_another_branchs_turn(two_branches):
    """Each branch has its own foreground operation. /compact, a retry or a
    rewind of the conversation only take the selected branch's slot while a
    turn runs on another branch; a second operation on the same branch is
    refused and names what runs there."""
    from openprogram.interfaces.server.routes.chat_branches import _with_reserved_run

    h = two_branches
    server, sid = h.server, h.session_id
    ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())

    ran: list[str] = []
    assert _with_reserved_run(sid, lambda: ran.append("rewind") or {"ok": True}) == {"ok": True}
    assert ran == ["rewind"]
    assert server.reserve_operation(sid, "compact", "compact_x") is None
    try:
        assert server._running_branches(sid)[0]["op_kind"] == "compact"
        refusal = server.reserve_operation(sid, "retry", "retry_x")
        assert (refusal["code"], refusal["kind"]) == ("run_active", "compact")
        # A message to the selected branch waits; B keeps running.
        assert _error_code(_send(h, "on A now")) == "run_active"
        assert _status(h, ack_b["execution_id"]) is ExecutionStatus.RUNNING
    finally:
        server._release_run_reservation(sid, "compact_x")

    # An operation on B's own slot is refused while B's turn runs.
    refusal = server.reserve_operation(sid, "compact", "compact_b", branch_id=h.branch_b)
    assert (refusal["code"], refusal["kind"], refusal["branch_id"]) == ("run_active", "reply", h.branch_b)

    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._is_run_active(sid))


def test_a_rewind_that_restores_files_waits_for_every_branch(two_branches):
    """Only a rewind that changes files is conversation-wide: refused while
    another branch runs, naming that branch with the ids a Stop needs; once
    that branch is stopped it goes ahead, and while it runs no branch is
    admitted and new executions wait for it."""
    from openprogram.interfaces.server.routes.chat_branches import _with_reserved_run
    from openprogram.state.store.session.operations import workspace_hold

    h = two_branches
    server, sid = h.server, h.session_id
    h.sessions.update_session(sid, branch_refs={
        **h.sessions.get_session(sid)["branch_refs"],
        h.branch_b: {**h.sessions.get_session(sid)["branch_refs"][h.branch_b], "label": {"name": "nightly"}},
    })
    ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())

    ran: list[str] = []
    refused = _with_reserved_run(sid, lambda: ran.append("files") or {}, scope="conversation")
    assert ran == [] and refused["__error__"][1] == 409 and refused["code"] == "branch_running"
    blocking = refused["blocking"]
    assert (blocking["branch_id"], blocking["execution_id"]) == (h.branch_b, ack_b["execution_id"])
    assert isinstance(blocking["status_version"], int) and blocking.get("branch_name") == "nightly"
    assert "nightly" in refused["__error__"][0] and "stop it, then rewind" in refused["__error__"][0]

    # Stop it, then rewind.
    _cancel(h.store.get_execution(blocking["execution_id"]))
    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.CANCELLED)
    _wait(lambda: not server._is_run_active(sid))

    def restore() -> dict:
        ran.append("files")
        # While it restores files, no branch is admitted and the hold is
        # visible to other processes.
        assert workspace_hold(sid) is not None
        assert _error_code(_send(h, "on B later", branch_id=h.branch_b)) == "run_active"
        assert _error_code(_send(h, "on A later")) == "run_active"
        return {"ok": True}

    assert _with_reserved_run(sid, restore, scope="conversation") == {"ok": True}
    assert ran == ["files"] and workspace_hold(sid) is None


def test_deleting_a_branch_waits_only_for_the_branch_it_holds(two_branches):
    """A branch delete holds the selected branch for a moment. A turn on
    another branch does not refuse it, unless the deleted tail holds it."""
    from openprogram.interfaces.server.ws_actions import branch as branch_actions

    h = two_branches
    server, sid = h.server, h.session_id
    ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())

    assert server.reserve_branch_operation(sid, "delete_a", target_head="r1") is None
    server._release_run_reservation(sid, "delete_a")
    assert not server._is_selected_run_active(sid)

    ws = _WebSocket()
    asyncio.run(branch_actions.handle_delete_branch(ws, {"session_id": sid, "head_msg_id": "u2"}))
    deleted = next(frame["data"] for frame in ws.frames if frame["type"] == "branch_deleted")
    assert deleted["ok"] is False and deleted["code"] == "run_active" and deleted["branch_id"] == h.branch_b
    assert deleted["blocking"]["execution_id"] == ack_b["execution_id"]
    assert h.sessions.message_exists(sid, "u2")
    assert _status(h, ack_b["execution_id"]) is ExecutionStatus.RUNNING

    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._is_run_active(sid))


def _delete(harness, **cmd) -> dict:
    from openprogram.interfaces.server.ws_actions import branch as branch_actions

    ws = _WebSocket()
    asyncio.run(branch_actions.handle_delete_branch(ws, {"session_id": harness.session_id, **cmd}))
    return next(frame["data"] for frame in ws.frames if frame["type"] == "branch_deleted")


def _grow_branch_a(h) -> None:
    """Branch A gets a second turn, so its tail is u1 r1 u3 r3."""
    h.sessions.append_message(h.session_id, {"id": "u3", "role": "user", "content": "more", "predecessor": "r1"})
    h.sessions.set_head(h.session_id, "u3")
    h.sessions.append_message(h.session_id, {"id": "r3", "role": "assistant", "content": "ok", "predecessor": "u3"})
    h.sessions.set_head(h.session_id, "r3")


def test_deleting_the_selected_branch_removes_its_whole_tail_and_record(two_branches):
    """``/branch delete`` names no message: the selected branch goes, from
    its tip up to the fork point, with its record; the conversation moves
    to the other branch, and a message sent to the deleted one is refused."""
    h = two_branches
    sid = h.session_id
    _grow_branch_a(h)
    assert h.sessions.get_session(sid)["active_branch_id"] == h.branch_a

    deleted = _delete(h)
    assert deleted["ok"] is True and deleted["deleted"] == 4 and deleted["new_head"] == "r2"
    assert not any(h.sessions.message_exists(sid, mid) for mid in ("u1", "r1", "u3", "r3"))
    row = h.sessions.get_session(sid)
    assert row["head_id"] == "r2" and row["active_branch_id"] == h.branch_b
    assert h.branch_a not in row["branch_refs"]
    assert [b["head_msg_id"] for b in h.sessions.list_branches(sid)] == ["r2"]
    assert _error_code(_send(h, "on A", branch_id=h.branch_a)) == "branch_gone"


def test_deleting_an_unselected_branch_by_its_tip_keeps_the_selection(two_branches):
    h = two_branches
    sid = h.session_id
    deleted = _delete(h, head_msg_id="r2")
    assert deleted["ok"] is True and deleted["deleted"] == 2 and deleted["new_head"] is None
    assert not h.sessions.message_exists(sid, "u2")
    row = h.sessions.get_session(sid)
    assert row["head_id"] == "r1" and row["active_branch_id"] == h.branch_a
    assert h.branch_b not in row["branch_refs"]


def test_deleting_a_retry_keeps_the_shared_question(two_branches):
    """A retry forks at the user message: deleting it removes only the
    reply that differs."""
    h = two_branches
    sid = h.session_id
    h.sessions.append_message(sid, {"id": "r1b", "role": "assistant", "content": "hey", "predecessor": "u1"})
    deleted = _delete(h, head_msg_id="r1b")
    assert deleted["ok"] is True and deleted["deleted"] == 1
    assert h.sessions.message_exists(sid, "u1") and h.sessions.message_exists(sid, "r1")


def test_the_only_branch_and_a_shared_message_are_not_deleted(two_branches):
    h = two_branches
    sid = h.session_id
    assert _delete(h, head_msg_id="r2")["ok"] is True
    only = _delete(h)
    assert only["ok"] is False and "only branch" in only["error"]
    assert h.sessions.message_exists(sid, "r1")

    h.sessions.append_message(sid, {"id": "r1b", "role": "assistant", "content": "hey", "predecessor": "u1"})
    shared = _delete(h, head_msg_id="u1")
    assert shared["ok"] is False and "Several branches" in shared["error"]
    assert h.sessions.message_exists(sid, "r1") and h.sessions.message_exists(sid, "r1b")


def _switch(harness, head_msg_id: str, **extra) -> dict:
    from openprogram.interfaces.server.ws_actions import branch as branch_actions

    ws = _WebSocket()
    asyncio.run(branch_actions.handle_checkout_branch(
        ws, {"session_id": harness.session_id, "head_msg_id": head_msg_id, **extra}))
    return next(frame["data"] for frame in ws.frames if frame["type"] == "branch_checked_out")


def test_switching_away_from_a_running_turn_then_sending_on_the_other_branch(two_branches):
    """A switch goes ahead while the selected branch's turn runs. The turn
    keeps its branch: the person sends on the branch they switched to, and
    the turn that finishes later writes to its own branch and leaves the
    selection alone."""
    h = two_branches
    server, sid = h.server, h.session_id
    ack_a = _ack(_send(h, "on A"))
    _wait(lambda: h.provider.gate("on A") and h.provider.entered["on A"].is_set())

    switched = _switch(h, "r2")
    assert switched["ok"] and switched["active_branch_id"] == h.branch_b
    # A's turn now runs beside the selection, on A's own slot.
    assert not server._is_selected_run_active(sid)
    running = {item["branch_id"]: item for item in server._running_branches(sid)}
    assert running[h.branch_a]["execution_id"] == ack_a["execution_id"]
    assert not running[h.branch_a]["selected"]
    assert any(frame.get("type") == "running_task_clear" for frame in h.frames)

    ack_b = _ack(_send(h, "on B"))
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())
    assert server._run_identity(sid)["execution_id"] == ack_b["execution_id"]

    # A finishes after the switch: the selection stays on B.
    h.provider.gate("on A").set()
    _wait(lambda: _status(h, ack_a["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._branch_run_active(sid, h.branch_a))
    reply_a, reply_b = f"{ack_a['msg_id']}_reply", f"{ack_b['msg_id']}_reply"
    row = h.sessions.get_session(sid)
    assert row["active_branch_id"] == h.branch_b
    assert row["branch_refs"][h.branch_a]["head_id"] == reply_a
    assert row["head_id"] != reply_a

    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._is_run_active(sid))
    row = h.sessions.get_session(sid)
    assert row["active_branch_id"] == h.branch_b and row["head_id"] == reply_b
    assert row["branch_refs"][h.branch_b]["head_id"] == reply_b
    assert [item["id"] for item in h.sessions.get_branch(sid, reply_a)][-4:] == \
        ["u1", "r1", ack_a["msg_id"], reply_a]
    assert [item["id"] for item in h.sessions.get_branch(sid, reply_b)][-4:] == \
        ["u2", "r2", ack_b["msg_id"], reply_b]
    assert "hi" not in h.provider.contexts["on B"]


def test_switching_onto_a_running_branch_shows_it_and_switching_back_reuses_the_record(two_branches):
    """Entering a branch whose turn runs makes that turn the selected one:
    run state, Stop and the transcript show it, a rewind there is still
    refused, and switching back selects the branch's existing record."""
    from openprogram.interfaces.server.routes.chat_branches import _with_reserved_run

    h = two_branches
    server, sid = h.server, h.session_id
    records = set(h.sessions.get_session(sid)["branch_refs"])
    ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
    _wait(lambda: h.provider.gate("on B") and h.provider.entered["on B"].is_set())
    b_tip = h.sessions.get_session(sid)["branch_refs"][h.branch_b]["head_id"]

    seen = len(h.frames)
    switched = _switch(h, b_tip)
    assert switched["ok"] and switched["active_branch_id"] == h.branch_b
    assert server._is_selected_run_active(sid)
    assert server._run_identity(sid)["execution_id"] == ack_b["execution_id"]
    shown = [frame["data"] for frame in h.frames[seen:] if frame.get("type") == "running_task"]
    assert any(item.get("execution_id") == ack_b["execution_id"] for item in shown)

    # A rewind on the running branch is still refused.
    ran: list[str] = []
    refused = _with_reserved_run(sid, lambda: ran.append("rewind") or {})
    assert ran == [] and refused["__error__"][1] == 409 and refused["code"] == "run_active"

    # Switching back to A reuses A's record.
    back = _switch(h, "r1")
    assert back["ok"] and back["active_branch_id"] == h.branch_a
    assert not server._is_selected_run_active(sid)
    assert server._branch_run_active(sid, h.branch_b)

    h.provider.gate("on B").set()
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: not server._is_run_active(sid))
    row = h.sessions.get_session(sid)
    assert row["active_branch_id"] == h.branch_a and row["head_id"] == "r1"
    assert row["branch_refs"][h.branch_b]["head_id"] == f"{ack_b['msg_id']}_reply"
    assert set(row["branch_refs"]) == records
    # And back onto B again: the same record, at its finished tip.
    again = _switch(h, f"{ack_b['msg_id']}_reply")
    assert again["active_branch_id"] == h.branch_b
    assert set(h.sessions.get_session(sid)["branch_refs"]) == records


class _LoadingProvider(_GatedProvider):
    """Each branch's turn searches for its own tool first, then waits at its
    next request, so both turns hold their loads at once."""

    def __init__(self, loads: dict[str, str]) -> None:
        super().__init__()
        self.loads = loads
        self.tools: dict[str, list[str]] = {}

    async def stream_simple(self, model, context, options=None):
        from tests.component.providers.scripted_provider import ScriptedProvider, ScriptedText, ScriptedToolCall

        texts = []
        for message in context.messages:
            content = getattr(message, "content", "")
            texts.extend([content] if isinstance(content, str)
                         else [getattr(block, "text", "") for block in content or []])
        prompt = next((match.group(1) for text in reversed(texts)
                       if (match := re.search(r'"content":"(on [^"]*)"', text))), "")
        scripted = ScriptedProvider()
        if not any(getattr(message, "role", None) == "toolResult" for message in context.messages):
            scripted.add_response(ScriptedToolCall(
                "tool_search", {"select": f"select:{self.loads[prompt]}"}, f"call_{self.loads[prompt]}"))
        else:
            gate = self.gate(prompt)
            self.tools[prompt] = [tool.name for tool in context.tools or []]
            self.entered[prompt].set()
            while not gate.is_set():
                await asyncio.sleep(0.005)
            scripted.add_response(ScriptedText(f"reply to {prompt}"))
        async for event in scripted.stream_simple(model, context, options):
            yield event


def test_branch_loads_are_isolated(two_branches, monkeypatch):
    """Deferred-tools L1: each branch's request loads what its own messages
    loaded (Pi keeps loads in the branch's own state), while both run."""
    import openprogram.runtime.conversation.turn_runtime.loop_runner as loop_runner
    from openprogram.models.providers.catalog.api_registry import register_api_provider
    from openprogram.models.providers.types import AgentTool, AgentToolResult, TextContent

    h = two_branches

    def tool(name: str, deferred: bool, answer) -> AgentTool:
        async def execute(call_id, args, cancel=None, on_update=None):
            return answer(args)

        made = AgentTool(name=name, label=name, description=f"The {name} tool.",
                         parameters={"type": "object", "properties": {"select": {"type": "string"}}},
                         execute=execute)
        setattr(made, "_defer", deferred)
        return made

    def loader(args):
        name = str(args.get("select", "")).removeprefix("select:")
        return AgentToolResult(content=[TextContent(text=f"Loaded {name}.")], details={"loads": [{
            "name": name, "definition": {"name": name, "description": f"The {name} tool.",
                                         "parameters": {"type": "object", "properties": {}}}}]})

    tools = [tool("tool_search", False, loader),
             tool("cron_a", True, lambda args: AgentToolResult(content=[TextContent(text="a")])),
             tool("cron_b", True, lambda args: AgentToolResult(content=[TextContent(text="b")]))]
    monkeypatch.setattr(loop_runner, "_resolve_tools", lambda *_args, **_kwargs: list(tools))
    provider = _LoadingProvider({"on A": "cron_a", "on B": "cron_b"})
    register_api_provider("branch-slots-api", provider)
    h.provider = provider

    try:
        ack_a = _ack(_send(h, "on A"))
        _wait(lambda: provider.gate("on A") and provider.entered["on A"].is_set())
        ack_b = _ack(_send(h, "on B", branch_id=h.branch_b))
        _wait(lambda: provider.gate("on B") and provider.entered["on B"].is_set())

        # Both turns are waiting at their request after the load.
        assert "cron_a" in provider.tools["on A"] and "cron_b" not in provider.tools["on A"]
        assert "cron_b" in provider.tools["on B"] and "cron_a" not in provider.tools["on B"]
    finally:
        provider.gate("on A").set()
        provider.gate("on B").set()
    _wait(lambda: _status(h, ack_a["execution_id"]) is ExecutionStatus.COMPLETED)
    _wait(lambda: _status(h, ack_b["execution_id"]) is ExecutionStatus.COMPLETED)
