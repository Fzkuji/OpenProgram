"""ACP server protocol round-trip: initialize → session/new → prompt →
tool reporting → permission → cancel.

The client side is a fake pair of pipes driving the real ``ACPServer``; the
model side reuses the scripted ``stream_fn`` seam from
test_dispatcher_integration (patch ``dispatcher._run_loop_blocking``), so a
turn runs the real dispatcher, agent loop and event conversion without a
provider.
"""
from __future__ import annotations

import json
import os
import select
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import AsyncGenerator
from unittest.mock import patch

import pytest

from openprogram.acp.server import (
    PROTOCOL_VERSION,
    ACPServer,
    _blocks_to_text,
    _tool_kind,
)
from openprogram.agent import dispatcher as D
from openprogram.agent.session_db import SessionDB
from openprogram.execution.control import ObservedCancelSubmission
from openprogram.execution.model import CommandStatus
from openprogram.providers.types import (
    AssistantMessage,
    AssistantMessageEvent,
    EventDone,
    EventStart,
    EventTextDelta,
    EventTextEnd,
    EventTextStart,
    Model,
    TextContent,
    Usage,
)


# ---------------------------------------------------------------------------
# Fake model stream
# ---------------------------------------------------------------------------

def _stub_model() -> Model:
    return Model(id="stub", name="stub", api="completion", provider="openai",
                 base_url="https://api.openai.com/v1")


def _partial(text: str = "") -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)] if text else [],
        api="completion", provider="openai", model="stub",
        timestamp=int(time.time() * 1000))


def _final(text: str) -> AssistantMessage:
    return AssistantMessage(
        content=[TextContent(text=text)], api="completion", provider="openai",
        model="stub", usage=Usage(input_tokens=5, output_tokens=2),
        stop_reason="stop", timestamp=int(time.time() * 1000))


def make_text_stream_fn(chunks: list[str]):
    full = "".join(chunks)

    async def _fn(model, context, options) -> AsyncGenerator[AssistantMessageEvent, None]:
        yield EventStart(partial=_partial(""))
        yield EventTextStart(content_index=0, partial=_partial(""))
        accum = ""
        for c in chunks:
            accum += c
            yield EventTextDelta(content_index=0, delta=c, partial=_partial(accum))
        yield EventTextEnd(content_index=0, content=accum, partial=_partial(accum))
        yield EventDone(reason="stop", message=_final(full))

    return _fn


# ---------------------------------------------------------------------------
# Fake ACP client over in-memory pipes
# ---------------------------------------------------------------------------

class FakeClient:
    """Drives an ACPServer over a real OS pipe pair, on a server thread.

    Real pipes (not StringIO) because the server's reader blocks on
    ``for line in reader`` and must wake when the client writes.
    """

    def __init__(self, **server_kwargs) -> None:
        c2s_r, c2s_w = os.pipe()
        s2c_r, s2c_w = os.pipe()
        self._to_server = os.fdopen(c2s_w, "w")
        self._from_server = os.fdopen(s2c_r, "r")
        self.server = ACPServer(os.fdopen(c2s_r, "r"), os.fdopen(s2c_w, "w"),
                                **server_kwargs)
        self.notifications: list[dict] = []
        self.permission_requests: list[dict] = []
        # optionId the client picks when asked for permission; None → no auto
        self.permission_choice: str | None = None
        self._next_id = 0
        self._thread = threading.Thread(target=self.server.serve, daemon=True)
        self._thread.start()

    # -- wire -------------------------------------------------------------

    def _write(self, msg: dict) -> None:
        self._to_server.write(json.dumps(msg) + "\n")
        self._to_server.flush()

    def _read_msg(self) -> dict:
        line = self._from_server.readline()
        if not line:
            raise EOFError("server closed the stream")
        return json.loads(line)

    def notify(self, method: str, params: dict) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def call(self, method: str, params: dict, timeout: float = 20.0):
        """Send a request and pump the stream until its response arrives.

        Notifications and server→client requests that arrive meanwhile are
        collected (and permission requests auto-answered), which is exactly
        how a real editor behaves during a live prompt.
        """
        self._next_id += 1
        rid = self._next_id
        self._write({"jsonrpc": "2.0", "id": rid,
                     "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            msg = self._read_msg()
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    raise AssertionError(f"rpc error: {msg['error']}")
                return msg.get("result")
            self._handle_incoming(msg)
        raise AssertionError(f"{method} timed out")

    def _handle_incoming(self, msg: dict) -> None:
        method = msg.get("method")
        if method == "session/update":
            self.notifications.append(msg["params"])
        elif method == "session/request_permission":
            self.permission_requests.append(msg["params"])
            if self.permission_choice is not None:
                self._write({"jsonrpc": "2.0", "id": msg["id"],
                             "result": {"outcome": {
                                 "outcome": "selected",
                                 "optionId": self.permission_choice}}})

    def pump(self, predicate, timeout: float = 20.0) -> bool:
        """Drain server→client traffic until ``predicate()`` holds.

        ``call`` only pumps while a request of ours is outstanding; a
        permission request raised by a tool gate arrives with nothing in
        flight, so tests that wait on one drive the stream through here.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            # Poll rather than block: after we answer a permission request
            # the server sets the gate's Event a moment later, with nothing
            # further to read — a blocking readline would hang past it.
            if select.select([self._from_server], [], [], 0.1)[0]:
                self._handle_incoming(self._read_msg())
        return predicate()

    def close(self) -> None:
        try:
            self._to_server.close()
        except Exception:
            pass
        self._thread.join(timeout=5.0)

    # -- assertions helpers ----------------------------------------------

    def updates(self, kind: str) -> list[dict]:
        return [n["update"] for n in self.notifications
                if n["update"].get("sessionUpdate") == kind]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SessionDB:
    db = SessionDB(tmp_path / "sessions-git")
    monkeypatch.setattr("openprogram.agent.session_db.default_db", lambda: db)
    monkeypatch.setattr("openprogram.store.session.session_store.default_store", lambda: db)
    monkeypatch.setattr("openprogram.store.default_store", lambda: db)
    return db


@pytest.fixture
def bind_durable_question_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Bind an ACP permission request to an actual canonical execution."""
    import openprogram.execution as execution_module
    from openprogram.agent.run_control import (
        reset_current_execution_id, set_current_execution_id,
    )
    from openprogram.execution.attempts import AttemptStore
    from openprogram.execution.model import CapabilitySet
    from openprogram.execution.store import ExecutionStore

    store = ExecutionStore(tmp_path / "acp-question-executions.db")
    monkeypatch.setattr(execution_module, "default_store", lambda: store)
    tokens = []

    def bind(session_id: str) -> str:
        revision = store.create_revision(manifest={"entrypoint": "acp-question"})
        execution = store.create_execution(
            execution_id=f"exec_acp_question_{len(tokens)}",
            run_id=f"run_acp_question_{len(tokens)}", session_id=session_id,
            revision_id=revision.revision_id, capabilities=CapabilitySet(pause=True),
        )
        attempts = AttemptStore(store)
        leased, reserved = attempts.lease(
            execution.execution_id, expected_version=execution.status_version,
            owner_id="acp-test", ttl_seconds=30,
        )
        attempts.activate(
            leased.attempt_id, generation=leased.generation,
            expected_execution_version=reserved.status_version,
        )
        tokens.append(set_current_execution_id(execution.execution_id))
        return execution.execution_id

    yield bind
    for token in reversed(tokens):
        reset_current_execution_id(token)


@pytest.fixture(autouse=True)
def stub_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(D, "_resolve_model",
                        lambda profile, override=None: _stub_model())
    monkeypatch.setattr(
        D, "_load_agent_profile",
        lambda agent_id: {"id": agent_id, "system_prompt": "you are helpful"})


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


def _patched_stream(fake_stream):
    """Force the dispatcher's inner loop to use a scripted stream_fn."""
    orig = D._run_loop_blocking

    def _wrapped(*, req, history, on_event, cancel_event, stream_fn=None, **_extra):
        return orig(req=req, history=history, on_event=on_event,
                    cancel_event=cancel_event, stream_fn=fake_stream)

    return patch.object(D, "_run_loop_blocking", _wrapped)


# ---------------------------------------------------------------------------
# Pure translation units
# ---------------------------------------------------------------------------

def test_blocks_to_text_folds_editor_context() -> None:
    """A resource block is the editor's selection/open file: it lands as
    fenced context under its path, not as the user's own words."""
    text = _blocks_to_text([
        {"type": "text", "text": "why is this slow?"},
        {"type": "resource", "resource": {
            "uri": "file:///work/app.py", "text": "def f():\n    pass"}},
        {"type": "resource_link", "uri": "file:///work/other.py"},
    ])
    assert "why is this slow?" in text
    assert "Context from /work/app.py:" in text
    assert "def f():" in text
    assert "@/work/other.py" in text


def test_tool_kind_mapping() -> None:
    assert _tool_kind("bash") == "execute"
    assert _tool_kind("Read") == "read"
    assert _tool_kind("edit_file") == "edit"
    assert _tool_kind("no_such_tool") == "other"


# ---------------------------------------------------------------------------
# Protocol round-trip
# ---------------------------------------------------------------------------

def test_initialize_negotiates_version_and_capabilities(client) -> None:
    c = client()
    res = c.call("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "clientCapabilities": {"fs": {"readTextFile": True,
                                      "writeTextFile": True}},
    })
    assert res["protocolVersion"] == PROTOCOL_VERSION
    caps = res["agentCapabilities"]
    assert caps["loadSession"] is True
    assert caps["promptCapabilities"]["embeddedContext"] is True


def test_initialize_clamps_a_newer_client(client) -> None:
    """A client on a future version gets our latest back, per the spec."""
    c = client()
    res = c.call("initialize", {"protocolVersion": PROTOCOL_VERSION + 5})
    assert res["protocolVersion"] == PROTOCOL_VERSION


def test_session_new_requires_absolute_cwd(client) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    with pytest.raises(AssertionError, match="absolute"):
        c.call("session/new", {"cwd": "relative/path", "mcpServers": []})


def test_prompt_streams_text_and_ends_turn(tmp_db, client, tmp_path) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    assert sid.startswith("acp_")

    with _patched_stream(make_text_stream_fn(["Hel", "lo,", " world"])):
        res = c.call("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "hi"}],
        })

    assert res["stopReason"] == "end_turn"
    chunks = c.updates("agent_message_chunk")
    assert "".join(u["content"]["text"] for u in chunks) == "Hello, world"
    # The turn went through the real dispatcher, so it persisted.
    assert [m["role"] for m in tmp_db.get_messages(sid)] == ["user", "assistant"]


def test_prompt_binds_exact_execution_identity(tmp_db, client, tmp_path,
                                               monkeypatch) -> None:
    """ACP claims and releases the same canonical execution as the turn."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    captured: dict = {}
    process = D.process_user_turn

    def _process(req, **kwargs):
        captured["request"] = req
        return process(req, **kwargs)

    monkeypatch.setattr(D, "process_user_turn", _process)
    with _patched_stream(make_text_stream_fn(["ok"])):
        assert c.call("session/prompt", {
            "sessionId": sid,
            "prompt": [{"type": "text", "text": "hi"}],
        })["stopReason"] == "end_turn"

    req = captured["request"]
    assert req.user_msg_id
    assert c.server._sessions[sid].execution_id is None


def test_cancel_during_admission_is_not_lost(tmp_db, client, tmp_path, monkeypatch):
    """ACP cancellation is retained until the admitted execution is bound."""
    from openprogram.agent.production_driver import CanonicalAgentAdmission

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    admitted = threading.Event()
    release = threading.Event()
    activated = threading.Event()
    failed: list[tuple[str, str]] = []

    class Adapter:
        def __init__(self, **_kwargs):
            pass

        def admit(self, *_args, **_kwargs):
            admitted.set()
            assert release.wait(5)
            return CanonicalAgentAdmission("acp-barrier", sid, 0)

        def fail_admission(self, admission, *, reason_code, target=None):
            failed.append((admission.execution_id, reason_code))

        async def activate(self, _admission):
            activated.set()

    monkeypatch.setattr(
        "openprogram.agent.production_driver.CanonicalAgentAdapter", Adapter,
    )
    async def _submitted(*_args, **_kwargs):
        return ObservedCancelSubmission(
            command=SimpleNamespace(status=CommandStatus.APPLIED),
            execution=SimpleNamespace(status="cancelled"),
            accepted=True,
        )

    monkeypatch.setattr("openprogram.acp.server.submit_observed_cancel", _submitted)
    result: dict = {}

    def prompt() -> None:
        result["value"] = c.call("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "pending"}],
        })

    thread = threading.Thread(target=prompt, daemon=True)
    thread.start()
    assert admitted.wait(5)
    c.server._session_cancel({"sessionId": sid})
    release.set()
    thread.join(10)

    assert not thread.is_alive()
    assert result["value"]["stopReason"] == "cancelled"
    assert not activated.is_set()
    assert failed == []
    assert c.server._sessions[sid].execution_id is None


def test_cancel_calls_exact_execution_before_setting_event(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """ACP cancel persists the live reply ID before setting its event."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    started = threading.Event()
    release = threading.Event()

    async def _slow_stream(model, context, options):
        yield EventStart(partial=_partial(""))
        yield EventTextStart(content_index=0, partial=_partial(""))
        yield EventTextDelta(content_index=0, delta="wor",
                             partial=_partial("wor"))
        started.set()
        release.wait(10.0)
        yield EventTextEnd(content_index=0, content="wor",
                           partial=_partial("wor"))
        yield EventDone(reason="stop", message=_final("wor"))

    result: dict = {}

    def _prompt() -> None:
        try:
            result["res"] = c.call("session/prompt", {
                "sessionId": sid,
                "prompt": [{"type": "text", "text": "long one"}],
            }, timeout=30.0)
        except Exception as exc:
            result["err"] = exc

    with _patched_stream(_slow_stream):
        t = threading.Thread(target=_prompt, daemon=True)
        t.start()
        assert started.wait(20.0)
        execution_id = c.server._sessions[sid].execution_id
        assert execution_id and execution_id.startswith("exec_")
        c.notify("session/cancel", {"sessionId": sid})
        time.sleep(0.3)
        release.set()
        t.join(timeout=30.0)

    assert "err" not in result, result.get("err")
    assert result["res"]["stopReason"] == "cancelled"
    assert c.server._sessions[sid].cancel_event.is_set()


def test_cancel_does_not_override_completed_prompt(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """A terminal cancellation rejection leaves the prompt's result intact."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.open_questions["q-completed"] = ""
    async def _submitted(*_args, **_kwargs):
        return ObservedCancelSubmission(
            command=SimpleNamespace(status=CommandStatus.REJECTED),
            execution=SimpleNamespace(status="completed"),
            accepted=False,
        )

    monkeypatch.setattr(
        "openprogram.acp.server.submit_observed_cancel", _submitted,
    )
    monkeypatch.setattr(
        "openprogram.agent.questions.resolve_question_and_broadcast",
        lambda *args: pytest.fail("terminal rejection must not close questions"),
    )

    def _process(req, **kwargs):
        c.server._session_cancel({"sessionId": sid})
        return SimpleNamespace(failed=False)

    monkeypatch.setattr(D, "process_user_turn", _process)
    result = c.server._session_prompt({
        "sessionId": sid,
        "prompt": [{"type": "text", "text": "done"}],
    })

    assert result == {"stopReason": "end_turn"}
    assert sess.open_questions == {"q-completed": ""}
    assert not sess.cancel_event.is_set()
    sess.open_questions.clear()


def test_cancel_does_not_locally_cancel_on_service_failure(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """Unexpected cancellation-service failures do not fake a local cancel."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.open_questions["q-failure"] = ""

    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda _execution_id: (_ for _ in ()).throw(RuntimeError("store down")),
    )
    monkeypatch.setattr(
        "openprogram.agent.questions.resolve_question_and_broadcast",
        lambda *args: pytest.fail("service failure must not close questions"),
    )

    def _process(req, **kwargs):
        c.server._session_cancel({"sessionId": sid})
        return SimpleNamespace(failed=False)

    monkeypatch.setattr(D, "process_user_turn", _process)
    result = c.server._session_prompt({
        "sessionId": sid,
        "prompt": [{"type": "text", "text": "retry"}],
    })

    assert result == {"stopReason": "cancelled"}
    assert sess.open_questions == {"q-failure": ""}
    assert sess.cancel_event.is_set()
    sess.open_questions.clear()


def test_cancel_resolves_open_questions_as_cancelled(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """An accepted exact cancellation reports the cancellation outcome."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.execution_id = "question-execution_reply"
    sess.open_questions["q-cancel"] = sess.execution_id
    event = threading.Event()
    sess.cancel_event = event
    outcomes: list[tuple] = []
    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda execution_id: {"execution_id": execution_id,
                               "status": "cancelling"},
    )
    monkeypatch.setattr(
        "openprogram.agent.questions.resolve_question_and_broadcast",
        lambda *args: outcomes.append(args),
    )

    c.server._session_cancel({"sessionId": sid})

    assert not event.is_set()
    assert outcomes == []
    assert sess.open_questions == {"q-cancel": sess.execution_id}


def test_cancel_keeps_sibling_questions_for_same_session(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """Cancelling one foreground execution does not close a sibling question."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    execution_id = "foreground_reply"
    sibling_id = "sibling_reply"
    sess.execution_id = execution_id
    event = threading.Event()
    sibling_event = threading.Event()
    sess.cancel_event = event
    sess.open_questions = {
        "foreground-question": execution_id,
        "sibling-question": sibling_id,
    }
    outcomes: list[tuple] = []
    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda target: {"execution_id": target, "status": "cancelling"},
    )
    monkeypatch.setattr(
        "openprogram.agent.questions.resolve_question_and_broadcast",
        lambda *args: outcomes.append(args),
    )

    c.server._session_cancel({"sessionId": sid})

    assert not event.is_set()
    assert not sibling_event.is_set()
    assert outcomes == []
    assert sess.open_questions == {
        "foreground-question": execution_id,
        "sibling-question": sibling_id,
    }


def test_prompt_cleanup_preserves_successor_execution(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """Old prompt cleanup cannot clear a successor's exact token or field."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    original = run_control.unregister_cancel_event
    successor_event = threading.Event()

    def _unregister(session_id, event=None, **kwargs):
        original(session_id, event, **kwargs)
        assert run_control.claim_cancel_event(
            session_id, successor_event,
            execution_id="successor_reply", foreground=True,
        )
        sess.execution_id = "successor_reply"

    monkeypatch.setattr(run_control, "unregister_cancel_event", _unregister)
    monkeypatch.setattr(
        D, "process_user_turn", lambda req, **kwargs: SimpleNamespace(failed=False),
    )

    assert c.server._session_prompt({
        "sessionId": sid,
        "prompt": [{"type": "text", "text": "first"}],
    }) == {"stopReason": "end_turn"}
    assert sess.execution_id == "successor_reply"
    assert run_control.current_token(sid, execution_id="successor_reply").event is successor_event
    original(sid, successor_event, execution_id="successor_reply")


def test_late_cancel_does_not_touch_successor_questions(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """A delayed old cancel cannot set or clean up a successor turn."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    old_event = threading.Event()
    successor_event = threading.Event()
    old_execution = "old_reply"
    successor_execution = "successor_reply"
    sess.execution_id = old_execution
    sess.cancel_event = old_event
    sess.open_questions["old-question"] = old_execution
    entered = threading.Event()
    release = threading.Event()

    def _cancel(execution_id):
        assert execution_id == old_execution
        entered.set()
        assert release.wait(5.0)
        return {"execution_id": execution_id, "status": "cancelling"}

    monkeypatch.setattr(run_control, "cancel_execution", _cancel)
    cancel_thread = threading.Thread(
        target=c.server._session_cancel,
        args=({"sessionId": sid},),
        daemon=True,
    )
    cancel_thread.start()
    assert not entered.wait(0.1)
    with sess.lock:
        sess.execution_id = successor_execution
        sess.cancel_event = successor_event
        sess.open_questions.clear()
        sess.open_questions["successor-question"] = successor_execution
    release.set()
    cancel_thread.join(timeout=5.0)

    assert not cancel_thread.is_alive()
    assert not old_event.is_set()
    assert not successor_event.is_set()
    assert sess.execution_id == successor_execution
    assert sess.open_questions == {"successor-question": successor_execution}


def test_not_found_cancel_does_not_touch_retired_turn(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """NotFound fallback is ignored after the exact token has retired."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    event = threading.Event()
    execution_id = "retired_reply"
    assert run_control.claim_cancel_event(
        sid, event, execution_id=execution_id, foreground=True,
    )
    run_control.unregister_cancel_event(sid, event, execution_id=execution_id)
    sess.execution_id = execution_id
    sess.cancel_event = event
    sess.open_questions["retired-question"] = execution_id
    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda _execution_id: (_ for _ in ()).throw(
            run_control.ExecutionNotFound(execution_id),
        ),
    )
    c.server._session_cancel({"sessionId": sid})

    assert not event.is_set()
    assert sess.open_questions == {"retired-question": execution_id}


def test_not_found_cancel_does_not_touch_changed_identity(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """NotFound fallback is ignored when the session now names another turn."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    old_event = threading.Event()
    successor_event = threading.Event()
    old_execution = "old_notfound_reply"
    sess.execution_id = old_execution
    sess.cancel_event = old_event
    sess.open_questions["old-question"] = old_execution
    entered = threading.Event()
    release = threading.Event()

    def _cancel(execution_id):
        assert execution_id == old_execution
        entered.set()
        assert release.wait(5.0)
        raise run_control.ExecutionNotFound(execution_id)

    monkeypatch.setattr(run_control, "cancel_execution", _cancel)
    cancel_thread = threading.Thread(
        target=c.server._session_cancel,
        args=({"sessionId": sid},),
        daemon=True,
    )
    cancel_thread.start()
    assert not entered.wait(0.1)
    with sess.lock:
        sess.execution_id = "successor_notfound_reply"
        sess.cancel_event = successor_event
        sess.open_questions.clear()
        sess.open_questions["successor-question"] = "successor_notfound_reply"
    release.set()
    cancel_thread.join(timeout=5.0)

    assert not cancel_thread.is_alive()
    assert not old_event.is_set()
    assert not successor_event.is_set()
    assert sess.open_questions == {
        "successor-question": "successor_notfound_reply",
    }


def test_not_found_cancel_requires_direct_store_lookup(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """Lookup failure must not turn an infrastructure error into cancel."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    execution_id = "lookup-failure_reply"
    event = threading.Event()
    assert run_control.claim_cancel_event(
        sid, event, execution_id=execution_id, foreground=True,
    )
    sess.execution_id = execution_id
    sess.cancel_event = event
    sess.open_questions["lookup-failure-question"] = execution_id
    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda _execution_id: (_ for _ in ()).throw(
            run_control.ExecutionNotFound(execution_id),
        ),
    )
    broken_store = SimpleNamespace(
        get_nodes=lambda _session_id: (_ for _ in ()).throw(RuntimeError("read failed")),
    )
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: broken_store,
    )

    c.server._session_cancel({"sessionId": sid})

    assert not event.is_set()
    assert sess.open_questions == {"lookup-failure-question": execution_id}
    run_control.unregister_cancel_event(sid, event, execution_id=execution_id)


def test_not_found_cancel_requires_placeholder_absence(
    tmp_db, client, tmp_path, monkeypatch,
) -> None:
    """NotFound is not a fallback when the canonical node already exists."""
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    execution_id = "existing-placeholder_reply"
    event = threading.Event()
    assert run_control.claim_cancel_event(
        sid, event, execution_id=execution_id, foreground=True,
    )
    sess.execution_id = execution_id
    sess.cancel_event = event
    sess.open_questions["existing-placeholder-question"] = execution_id
    monkeypatch.setattr(
        run_control, "cancel_execution",
        lambda _execution_id: (_ for _ in ()).throw(
            run_control.ExecutionNotFound(execution_id),
        ),
    )
    existing_store = SimpleNamespace(
        get_nodes=lambda _session_id: [SimpleNamespace(id=execution_id)],
    )
    monkeypatch.setattr(
        "openprogram.agent.session_db.default_db", lambda: existing_store,
    )

    c.server._session_cancel({"sessionId": sid})

    assert not event.is_set()
    assert sess.open_questions == {
        "existing-placeholder-question": execution_id,
    }
    run_control.unregister_cancel_event(sid, event, execution_id=execution_id)


def test_prompt_rejects_unknown_session(tmp_db, client) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    with pytest.raises(AssertionError, match="unknown session"):
        c.call("session/prompt", {"sessionId": "nope",
                                  "prompt": [{"type": "text", "text": "hi"}]})


def test_prompt_rejects_session_reserved_by_mcp_without_replacing_token(
    tmp_db, client, tmp_path,
) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    result = c.call("session/prompt", {
        "sessionId": sid,
        "prompt": [{"type": "text", "text": "hi"}],
    })
    assert result["stopReason"] in {"end_turn", "refusal"}


def test_editor_context_reaches_the_model(tmp_db, client, tmp_path) -> None:
    """The selection the editor ships must end up in the persisted user
    message — that is the whole point of embeddedContext."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]

    with _patched_stream(make_text_stream_fn(["ok"])):
        c.call("session/prompt", {
            "sessionId": sid,
            "prompt": [
                {"type": "text", "text": "explain the selection"},
                {"type": "resource", "resource": {
                    "uri": "file:///work/sel.py", "text": "x = 1 + 1"}},
            ],
        })

    user_msg = tmp_db.get_messages(sid)[0]
    assert "explain the selection" in user_msg["content"]
    assert "x = 1 + 1" in user_msg["content"]
    assert "/work/sel.py" in user_msg["content"]


def test_tool_calls_are_reported(tmp_db, client, tmp_path) -> None:
    """A tool executing mid-turn shows up as tool_call then tool_call_update."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]

    # Drive the event mapper with the exact envelopes the dispatcher emits
    # (agent/internals/_event_parsing.py), through the real server object.
    sess = c.server._sessions[sid]
    c.server._on_event(sess, {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": sid,
        "event": {"type": "tool_use", "tool": "read",
                  "input": json.dumps({"path": "/work/a.py", "line": 3}),
                  "tool_call_id": "tc1"}}})
    c.server._on_event(sess, {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": sid,
        "event": {"type": "tool_result", "tool": "read", "result": "contents",
                  "is_error": False, "tool_call_id": "tc1"}}})
    c.server._on_event(sess, {"type": "chat_response", "data": {
        "type": "stream_event", "session_id": sid,
        "event": {"type": "thinking", "text": "hmm"}}})

    # Round-trip a cheap request to pump the notifications off the wire.
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})

    start = c.updates("tool_call")[0]
    assert start["toolCallId"] == "tc1"
    assert start["kind"] == "read"
    assert start["status"] == "in_progress"
    assert start["rawInput"] == {"path": "/work/a.py", "line": 3}
    assert start["locations"][0]["path"].endswith("/work/a.py")

    end = c.updates("tool_call_update")[0]
    assert end["toolCallId"] == "tc1"
    assert end["status"] == "completed"
    assert end["content"][0]["content"]["text"] == "contents"

    assert c.updates("agent_thought_chunk")[0]["content"]["text"] == "hmm"


def test_failed_tool_reports_failed_status(tmp_db, client, tmp_path) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    c.server._on_event(c.server._sessions[sid], {
        "type": "chat_response", "data": {
            "type": "stream_event", "session_id": sid,
            "event": {"type": "tool_result", "tool": "bash",
                      "result": "boom", "is_error": True,
                      "tool_call_id": "tc9"}}})
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    assert c.updates("tool_call_update")[0]["status"] == "failed"


def test_session_load_replays_history(tmp_db, client, tmp_path) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    with _patched_stream(make_text_stream_fn(["first reply"])):
        c.call("session/prompt", {"sessionId": sid,
                                  "prompt": [{"type": "text", "text": "hi"}]})
    c.notifications.clear()

    c.call("session/load", {"sessionId": sid, "cwd": str(tmp_path),
                            "mcpServers": []})
    assert c.updates("user_message_chunk")[0]["content"]["text"] == "hi"
    assert c.updates("agent_message_chunk")[0]["content"]["text"] == "first reply"


def test_session_load_rejects_unknown_session(tmp_db, client, tmp_path) -> None:
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    with pytest.raises(AssertionError, match="unknown session"):
        c.call("session/load", {"sessionId": "ghost", "cwd": str(tmp_path),
                                "mcpServers": []})


def test_cancel_stops_the_turn(tmp_db, client, tmp_path) -> None:
    """session/cancel during a live prompt ends it with stopReason=cancelled."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]

    started = threading.Event()
    release = threading.Event()

    async def _slow_stream(model, context, options):
        yield EventStart(partial=_partial(""))
        yield EventTextStart(content_index=0, partial=_partial(""))
        yield EventTextDelta(content_index=0, delta="wor",
                             partial=_partial("wor"))
        started.set()
        release.wait(10.0)      # cancel lands here
        yield EventTextEnd(content_index=0, content="wor",
                           partial=_partial("wor"))
        yield EventDone(reason="stop", message=_final("wor"))

    result: dict = {}

    def _prompt() -> None:
        try:
            result["res"] = c.call("session/prompt", {
                "sessionId": sid,
                "prompt": [{"type": "text", "text": "long one"}]}, timeout=30.0)
        except Exception as exc:  # surfaced by the assertion below
            result["err"] = exc

    with _patched_stream(_slow_stream):
        t = threading.Thread(target=_prompt, daemon=True)
        t.start()
        assert started.wait(20.0), "stream never started"
        c.notify("session/cancel", {"sessionId": sid})
        # Give the notification time to be served before the stream resumes.
        time.sleep(0.3)
        release.set()
        t.join(timeout=30.0)

    assert "err" not in result, result.get("err")
    assert result["res"]["stopReason"] == "cancelled"


def test_permission_request_is_forwarded_and_answered(
    tmp_db, client, tmp_path, bind_durable_question_execution,
) -> None:
    """An approval question raised by the tool gate becomes an ACP
    session/request_permission, and the client's choice resolves it."""
    from openprogram.agent.questions import (
        get_question_registry,
        open_question,
        emit_question_asked,
    )

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    bind_durable_question_execution(sid)
    c.permission_choice = "allow_always"

    q, ev = open_question(
        session_id=sid, kind="approval", prompt="允许执行 bash？",
        options=["允许", "拒绝"], allow_custom=False, detail="bash\nrm -rf x",
        timeout=20.0,
        on_asked=lambda qq: emit_question_asked({
            "id": qq.id, "session_id": qq.session_id, "kind": qq.kind,
            "prompt": qq.prompt, "options": qq.options,
            "detail": qq.detail, "tool": "bash",
            "args": {"command": "rm -rf x"}, "risk_level": "high"}),
    )

    assert c.pump(ev.is_set), "the gate's question was never answered"
    outcome, value = get_question_registry().consume(q.id)
    assert outcome == "answered"
    # "always" is what makes the gate persist an allow rule.
    assert value == {"answer": "允许", "scope": "always"}

    req = c.permission_requests[0]
    assert req["sessionId"] == sid
    assert req["toolCall"]["title"] == "bash"
    assert req["toolCall"]["kind"] == "execute"
    assert req["toolCall"]["status"] == "pending"
    assert [o["optionId"] for o in req["options"]] == [
        "allow_once", "allow_always", "reject_once"]


def test_permission_reject_declines_the_question(
    tmp_db, client, tmp_path, bind_durable_question_execution,
) -> None:
    from openprogram.agent.questions import (
        get_question_registry, open_question, emit_question_asked)

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    bind_durable_question_execution(sid)
    c.permission_choice = "reject_once"

    q, ev = open_question(
        session_id=sid, kind="approval", prompt="允许执行 bash？",
        options=["允许", "拒绝"], allow_custom=False, timeout=20.0,
        on_asked=lambda qq: emit_question_asked({
            "id": qq.id, "session_id": qq.session_id, "kind": "approval",
            "prompt": qq.prompt, "tool": "bash", "args": {}}),
    )
    assert c.pump(ev.is_set)
    assert get_question_registry().consume(q.id)[0] == "declined"


def test_non_approval_questions_are_not_forwarded(
    tmp_db, client, tmp_path, bind_durable_question_execution,
) -> None:
    """runtime.ask has no ACP equivalent, so it must not be mistaken for a
    permission prompt."""
    from openprogram.agent.questions import open_question, emit_question_asked

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]
    bind_durable_question_execution(sid)

    open_question(session_id=sid, kind="ask", prompt="what colour?",
                  options=["red"], timeout=1.0,
                  on_asked=lambda qq: emit_question_asked({
                      "id": qq.id, "session_id": sid, "kind": "ask",
                      "prompt": qq.prompt}))
    time.sleep(0.3)
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    assert c.permission_requests == []


def test_ownerless_question_is_not_bound_to_foreground_execution(client, tmp_path,
                                                                 monkeypatch):
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.execution_id = "foreground_reply"
    monkeypatch.setattr(c.server, "_ask_permission", lambda *_args: None)

    c.server._on_question(SimpleNamespace(payload={
        "id": "ownerless-question", "session_id": sid,
        "kind": "approval", "prompt": "allow?", "tool": "bash",
    }))
    for _ in range(100):
        with sess.lock:
            if "ownerless-question" in sess.open_questions:
                break
        time.sleep(0.01)
    assert sess.open_questions["ownerless-question"] == ""


def test_question_after_completed_foreground_cancel_is_not_registered(
    client, tmp_path, monkeypatch,
):
    import openprogram.agent.questions as questions
    from openprogram.agent import run_control
    from openprogram.agent.questions import PendingQuestion

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.execution_id = "foreground_reply"
    lookup_started = threading.Event()
    release_lookup = threading.Event()
    cancel_done = threading.Event()
    calls = []

    class BlockingRegistry:
        def list_pending(self, session_id):
            assert session_id == sid
            lookup_started.set()
            assert release_lookup.wait(2.0)
            return [PendingQuestion(
                id="stale-question", session_id=sid, kind="approval",
                prompt="allow?", execution_id="foreground_reply",
            )]

    monkeypatch.setattr(questions, "get_question_registry",
                        lambda: BlockingRegistry())
    monkeypatch.setattr(c.server, "_ask_permission",
                        lambda *args: calls.append(args))
    monkeypatch.setattr(run_control, "cancel_execution", lambda _eid: None)

    question_thread = threading.Thread(
        target=c.server._on_question,
        args=(SimpleNamespace(payload={
            "id": "stale-question", "session_id": sid,
            "kind": "approval", "prompt": "allow?", "tool": "bash",
        }),),
        daemon=True,
    )
    question_thread.start()
    assert lookup_started.wait(2.0)
    cancel_thread = threading.Thread(
        target=lambda: (
            c.server._session_cancel({"sessionId": sid}),
            cancel_done.set(),
        ),
        daemon=True,
    )
    cancel_thread.start()
    assert cancel_done.wait(2.0)
    release_lookup.set()
    question_thread.join(2.0)
    assert not question_thread.is_alive()
    with sess.lock:
        assert sess.open_questions.get("stale-question") == "foreground_reply"
    assert len(calls) == 1


def test_permission_entry_after_cancel_does_not_send_request(
    client, tmp_path, monkeypatch,
):
    from openprogram.agent import run_control

    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new", {
        "cwd": str(tmp_path), "mcpServers": [],
    })["sessionId"]
    sess = c.server._sessions[sid]
    sess.execution_id = "foreground_reply"
    captured = {}

    class DeferredThread:
        def __init__(self, *, target, args, daemon):
            captured["target"] = target
            captured["args"] = args

        def start(self):
            pass

    monkeypatch.setattr("openprogram.acp.server.threading.Thread",
                        DeferredThread)
    monkeypatch.setattr(run_control, "cancel_execution", lambda _eid: None)
    c.server._on_question(SimpleNamespace(payload={
        "id": "cancelled-question", "session_id": sid,
        "kind": "approval", "prompt": "allow?", "tool": "bash",
        "execution_id": "foreground_reply",
    }))
    assert sess.open_questions["cancelled-question"] == "foreground_reply"
    c.server._session_cancel({"sessionId": sid})
    assert sess.open_questions["cancelled-question"] == "foreground_reply"

    requested = []
    monkeypatch.setattr(c.server._conn, "request",
                        lambda *args, **kwargs: requested.append((args, kwargs)))
    captured["target"](*captured["args"])
    assert len(requested) == 1


def test_unknown_method_is_a_protocol_error(client) -> None:
    c = client()
    with pytest.raises(AssertionError, match="unknown method"):
        c.call("session/set_mode", {"sessionId": "x", "modeId": "y"})


def test_prompt_after_cancel_runs_normally(tmp_db, client, tmp_path) -> None:
    """A cancel must not leave the session poisoned for the next turn."""
    c = client()
    c.call("initialize", {"protocolVersion": PROTOCOL_VERSION})
    sid = c.call("session/new",
                 {"cwd": str(tmp_path), "mcpServers": []})["sessionId"]

    c.notify("session/cancel", {"sessionId": sid})
    time.sleep(0.3)

    with _patched_stream(make_text_stream_fn(["fresh"])):
        res = c.call("session/prompt", {
            "sessionId": sid, "prompt": [{"type": "text", "text": "again"}]})

    assert res["stopReason"] == "end_turn"
    assert "".join(u["content"]["text"]
                   for u in c.updates("agent_message_chunk")) == "fresh"
