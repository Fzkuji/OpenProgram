"""An ACP session's turn, run end to end against a fake model provider.

The provider is registered as an API for the test, so the real turn path
runs (tool loop, real tools, persistence) with no network. These tests pin
what an editor or a harness driving ``openprogram acp`` relies on: tools run
in the ``cwd`` the client gave ``session/new``; a model error answers
``session/prompt`` with a JSON-RPC error instead of an ordinary stop; a
permission request the client cannot answer declines the operation at once;
``session/cancel`` aborts the model request in flight and starts no other.
"""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from openprogram.interfaces.acp.server import PROTOCOL_VERSION
from openprogram.models.providers.catalog import api_registry
from openprogram.models.providers.catalog.enabled_models import ENABLED_MODELS
from openprogram.models.providers.types import (
    AssistantMessage,
    EventDone,
    EventError,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    ToolCall,
)
from openprogram.runtime.context.session_db import SessionDB

from tests.component.programs.mcp.test_acp_server import FakeClient

API = "acp-session-test-api"
PROVIDER = "acptest"
MODEL_ID = "fake-model"
MODEL = f"{PROVIDER}/{MODEL_ID}"


class ScriptedProvider:
    """Answers each turn round from ``script``: a list of tool calls, a
    text answer, ``"error"`` or ``"hang"`` (wait until cancelled)."""

    def __init__(self, script: list) -> None:
        self.script = list(script)
        self.calls = 0
        self.tool_results: list[str] = []
        self.hanging = threading.Event()
        self.cancelled = threading.Event()

    def stream(self, model, context, options=None):
        return self.stream_simple(model, context, options)

    async def stream_simple(self, model, context, options=None):
        message = AssistantMessage(
            content=[], api=model.api, provider=model.provider, model=model.id,
            stop_reason="stop", timestamp=0)
        if not context.tools:  # a call beside the turn (the title)
            message.content = [TextContent(text="Title")]
            yield EventStart(partial=message)
            yield EventDone(reason="stop", message=message)
            return
        self.calls += 1
        for item in context.messages or []:
            if getattr(item, "role", None) == "toolResult":
                text = "".join(getattr(c, "text", "") or "" for c in item.content or [])
                if text not in self.tool_results:
                    self.tool_results.append(text)
        step = self.script[min(self.calls, len(self.script)) - 1]
        yield EventStart(partial=message)
        if step == "hang":
            self.hanging.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.cancelled.set()
                raise
            message.content = [TextContent(text="woke up")]
            yield EventDone(reason="stop", message=message)
            return
        if step == "error":
            message.stop_reason = "error"
            message.error_message = "upstream 500: overloaded"
            yield EventError(reason="error", error=message)
            return
        if isinstance(step, list):
            message.content = [ToolCall(id=f"call{self.calls}_{i}", name=name, arguments=args)
                               for i, (name, args) in enumerate(step)]
            message.stop_reason = "toolUse"
            yield EventDone(reason="toolUse", message=message)
            return
        text = str(step)
        message.content = [TextContent(text=text)]
        yield EventTextStart(content_index=0, partial=message)
        yield EventTextDelta(content_index=0, delta=text, partial=message)
        yield EventTextEnd(content_index=0, content=text, partial=message)
        yield EventDone(reason="stop", message=message)


@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.runtime.context.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.state.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.state.store.default_store", lambda: db)
    return db


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Install a scripted provider as the Agent's model.

    The process runs from a directory other than the session's, so a tool
    that lands in the process directory is told apart from one that lands
    in the session's.
    """
    elsewhere = tmp_path / "process-cwd"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    def install(script: list) -> ScriptedProvider:
        fake = ScriptedProvider(script)
        monkeypatch.setitem(api_registry._registry, API, fake)
        monkeypatch.setitem(ENABLED_MODELS, MODEL, Model(
            id=MODEL_ID, name="fake", api=API, provider=PROVIDER,
            base_url="http://acp-session.invalid"))
        monkeypatch.setattr(
            "openprogram.runtime.conversation.session_model.new_session_chat_model",
            lambda _agent_id: (PROVIDER, MODEL_ID))
        return fake

    return install


@pytest.fixture
def client():
    made: list[FakeClient] = []

    def _make(**kw) -> FakeClient:
        c = FakeClient(**kw)
        made.append(c)
        return c

    yield _make
    for c in made:
        c.close()


def _new_session(c: FakeClient, cwd: Path) -> str:
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    return c.call("session/new", {"cwd": str(cwd), "mcpServers": []})["sessionId"]


def _prompt(c: FakeClient, sid: str, text: str, timeout: float = 60.0):
    return c.call("session/prompt", {
        "sessionId": sid, "prompt": [{"type": "text", "text": text}]},
        timeout=timeout)


def test_tools_run_in_the_cwd_the_client_gave(tmp_db, provider, client, tmp_path):
    project = tmp_path / "editor-project"
    project.mkdir()
    fake = provider([
        [("write", {"file_path": "made_by_acp.txt", "content": "hi"}),
         ("bash", {"command": "pwd"})],
        "done",
    ])
    c = client()
    sid = _new_session(c, project)

    assert _prompt(c, sid, "write the file")["stopReason"] == "end_turn"

    assert (project / "made_by_acp.txt").read_text() == "hi"
    assert not (tmp_path / "process-cwd" / "made_by_acp.txt").exists()
    assert any(f"Output:\n{project.resolve()}\n" in r for r in fake.tool_results), fake.tool_results


def test_a_model_error_answers_the_prompt_with_an_error(tmp_db, provider, client, tmp_path):
    provider(["error"])
    c = client()
    sid = _new_session(c, tmp_path)

    with pytest.raises(AssertionError, match="upstream 500: overloaded"):
        _prompt(c, sid, "hello")


def test_cancel_aborts_the_model_request_in_flight(tmp_db, provider, client, tmp_path):
    fake = provider(["hang", "should never be asked"])
    c = client()
    sid = _new_session(c, tmp_path)
    result: dict = {}

    def _run() -> None:
        try:
            result["res"] = _prompt(c, sid, "a long one")
        except Exception as exc:  # noqa: BLE001 - asserted below
            result["err"] = exc

    t = threading.Thread(target=_run, daemon=True)
    started = time.monotonic()
    t.start()
    assert fake.hanging.wait(30), "the model request never started"
    c.notify("session/cancel", {"sessionId": sid})
    t.join(30)

    assert not t.is_alive(), "the prompt did not end after cancel"
    assert "err" not in result, result.get("err")
    assert result["res"]["stopReason"] == "cancelled"
    assert fake.cancelled.wait(5), "the model request in flight was not aborted"
    assert time.monotonic() - started < 30
    assert fake.calls == 1


def _gated_turn(provider, client, tmp_path):
    fake = provider([[("bash", {"command": "echo gated > gated.txt"})], "done"])
    c = client(permission_mode="ask")
    sid = _new_session(c, tmp_path)
    return fake, c, sid


def test_an_allowed_operation_runs_and_the_prompt_lasts_the_whole_turn(
        tmp_db, provider, client, tmp_path):
    """The approval suspends the execution; the answer resumes it, and the
    prompt ends only when the resumed turn does, with its reply streamed."""
    fake, c, sid = _gated_turn(provider, client, tmp_path)
    c.permission_choice = "allow_once"

    assert _prompt(c, sid, "run it")["stopReason"] == "end_turn"

    assert len(c.permission_requests) == 1
    assert c.permission_requests[0]["toolCall"]["title"] == "bash"
    assert (tmp_path / "gated.txt").read_text().strip() == "gated"
    assert fake.calls == 2
    assert "".join(u["content"]["text"] for u in c.updates("agent_message_chunk")) == "done"


def test_a_permission_request_the_client_rejects_declines_at_once(
        tmp_db, provider, client, tmp_path, monkeypatch):
    """A client that answers ``session/request_permission`` with an error
    (it does not implement it) must not leave the operation waiting."""
    fake, c, sid = _gated_turn(provider, client, tmp_path)

    def _reject(msg: dict) -> None:
        if msg.get("method") == "session/request_permission":
            c.permission_requests.append(msg["params"])
            c._write({"jsonrpc": "2.0", "id": msg["id"],
                      "error": {"code": -32601, "message": "Method not found"}})
            return
        FakeClient._handle_incoming(c, msg)

    monkeypatch.setattr(c, "_handle_incoming", _reject)
    started = time.monotonic()
    assert _prompt(c, sid, "run it")["stopReason"] == "end_turn"

    assert time.monotonic() - started < 30
    assert len(c.permission_requests) == 1
    assert not (tmp_path / "gated.txt").exists()
    assert fake.calls == 2
    assert "".join(u["content"]["text"] for u in c.updates("agent_message_chunk")) == "done"


def test_cancel_while_a_permission_request_is_open_ends_the_prompt(
        tmp_db, provider, client, tmp_path):
    fake, c, sid = _gated_turn(provider, client, tmp_path)
    result: dict = {}

    def _run() -> None:
        try:
            result["res"] = _prompt(c, sid, "run it")
        except Exception as exc:  # noqa: BLE001 - asserted below
            result["err"] = exc

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    deadline = time.monotonic() + 30
    while not c.permission_requests and time.monotonic() < deadline:
        time.sleep(0.05)
    assert c.permission_requests, "no permission request reached the client"
    c.notify("session/cancel", {"sessionId": sid})
    t.join(30)

    assert not t.is_alive(), "the prompt did not end after cancel"
    assert "err" not in result, result.get("err")
    assert result["res"]["stopReason"] == "cancelled"
    assert not (tmp_path / "gated.txt").exists()
    assert fake.calls == 1


def test_a_client_that_hangs_up_fails_the_requests_it_left_open():
    import io
    import os

    from openprogram.interfaces.acp.jsonrpc import Connection, RPCError

    read_fd, write_fd = os.pipe()
    reader = os.fdopen(read_fd, "r")
    conn = Connection(reader, io.StringIO(), lambda _m, _p: None)
    server = threading.Thread(target=conn.serve_forever, daemon=True)
    server.start()
    outcome: dict = {}

    def _ask() -> None:
        try:
            conn.request("session/request_permission", {}, timeout=60)
        except RPCError as exc:
            outcome["error"] = exc.message

    asker = threading.Thread(target=_ask, daemon=True)
    asker.start()
    time.sleep(0.2)
    os.close(write_fd)
    asker.join(10)

    assert not asker.is_alive()
    assert outcome["error"] == "the client disconnected"


def test_an_error_in_a_resumed_round_reaches_the_client(tmp_db, provider, client, tmp_path):
    """The round after a permission answer runs in a later attempt than the
    prompt started; its error must still be the one the client sees."""
    fake = provider([[("bash", {"command": "echo hi"})], "error"])
    c = client(permission_mode="ask")
    c.permission_choice = "allow_once"
    sid = _new_session(c, tmp_path)

    with pytest.raises(AssertionError, match="upstream 500: overloaded"):
        _prompt(c, sid, "run it")
    assert len(c.permission_requests) == 1
    assert fake.calls == 2


def test_a_thread_never_prompted_leaves_the_folder_alone(tmp_db, provider, client, tmp_path):
    project = tmp_path / "untouched"
    project.mkdir()
    provider(["done"])
    c = client()
    sid = _new_session(c, project)

    assert list(project.iterdir()) == []
    assert tmp_db.get_session(sid) is None

    assert _prompt(c, sid, "hello")["stopReason"] == "end_turn"
    assert (project / ".openprogram" / "project.json").exists()
    assert tmp_db.get_session(sid) is not None


def _drop_question_frames(c, monkeypatch):
    """Keep every wait frame from the server, as if it were lost."""
    from openprogram.interfaces.acp import server as acp_server

    real = acp_server.ACPServer._on_event

    def _on_event(self, sess, env):
        if env.get("type") in ("question.asked", "system_access.waiting"):
            return None
        return real(self, sess, env)

    monkeypatch.setattr(acp_server.ACPServer, "_on_event", _on_event)
    monkeypatch.setattr(c.server, "_on_question", lambda _event: None)


def test_a_wait_whose_frame_was_lost_is_still_asked(
        tmp_db, provider, client, tmp_path, monkeypatch):
    fake, c, sid = _gated_turn(provider, client, tmp_path)
    _drop_question_frames(c, monkeypatch)
    c.permission_choice = "allow_once"

    assert _prompt(c, sid, "run it")["stopReason"] == "end_turn"

    assert len(c.permission_requests) == 1
    assert (tmp_path / "gated.txt").read_text().strip() == "gated"
    assert fake.calls == 2


def test_a_system_access_wait_ends_the_prompt_with_an_error(
        tmp_db, provider, client, tmp_path, monkeypatch):
    """An editor cannot grant system access: the turn is cancelled and the
    prompt names what it needs instead of staying open."""
    import dataclasses

    from openprogram.runtime.execution import waits as waits_module

    fake, c, sid = _gated_turn(provider, client, tmp_path)
    _drop_question_frames(c, monkeypatch)
    real_list_open = waits_module.DurableWaitStore.list_open

    def _as_system_access(self, **kw):
        return [dataclasses.replace(
            w, kind="system_access",
            request={**w.request, "required_capabilities": ["accessibility"]})
            for w in real_list_open(self, **kw)]

    monkeypatch.setattr(waits_module.DurableWaitStore, "list_open", _as_system_access)
    started = time.monotonic()
    with pytest.raises(AssertionError, match="system access.*accessibility"):
        _prompt(c, sid, "run it")

    assert time.monotonic() - started < 30
    assert c.permission_requests == []
    assert not (tmp_path / "gated.txt").exists()
    assert fake.calls == 1


class _Record:
    def __init__(self, status, reason_code=None, version=1):
        self.status = status
        self.reason_code = reason_code
        self.status_version = version


class _OneRecordStore:
    def __init__(self, record):
        self.record = record

    def get_execution(self, _execution_id):
        return self.record


@pytest.mark.parametrize("status,reason,expected", [
    ("paused", "user_pause", "paused"),
    ("reconciliation_required", "effect_reconciliation", "reconciliation"),
])
def test_a_turn_nothing_here_can_move_on_ends_the_prompt(status, reason, expected):
    from openprogram.interfaces.acp.server import ACPServer, _Session
    from openprogram.runtime.execution.model import ExecutionStatus

    server = ACPServer.__new__(ACPServer)
    sess = _Session("acp_x", "/tmp")
    sess.execution_id = "ex1"
    record, stuck = server._follow(
        sess, _OneRecordStore(_Record(ExecutionStatus(status), reason)), "ex1")

    assert stuck and expected in stuck


def test_a_suspended_turn_with_nothing_to_answer_ends_the_prompt(monkeypatch):
    from openprogram.interfaces.acp import server as acp_server
    from openprogram.runtime.execution import waits as waits_module
    from openprogram.runtime.execution.model import ExecutionStatus

    monkeypatch.setattr(acp_server, "_STALL_S", 0.3)
    monkeypatch.setattr(waits_module.DurableWaitStore, "list_open", lambda self, **kw: [])
    server = acp_server.ACPServer.__new__(acp_server.ACPServer)
    sess = acp_server._Session("acp_x", "/tmp")
    sess.execution_id = "ex1"
    started = time.monotonic()
    _record, stuck = server._follow(
        sess, _OneRecordStore(_Record(ExecutionStatus.PAUSED, "wait_open")), "ex1")

    assert stuck and "did not resume" in stuck
    assert time.monotonic() - started < 5
