"""Public Agent safe-point contracts.

These tests deliberately enter through the WebSocket chat handler.  Control
commands must use the execution id returned by ``chat_ack`` and then pass the
same command envelope through the runtime action registry.  They must not
reach the control service directly from a transport test.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
from types import SimpleNamespace

import pytest

from tests.support.thread_scope import fake_threads_started_by

# The module whose run thread these tests replace.
TURN_LAUNCH_MODULE = "openprogram.interfaces.server.ws_actions.chat.turn_launch"

from openprogram.runtime.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.runtime.execution.driver import DriverAck, DriverBinding, DriverRegistry
from openprogram.runtime.execution.model import CapabilitySet, CommandKind, CommandStatus, ExecutionStatus
from openprogram.runtime.execution.effects import EffectStatus


class FakeWS:
    def __init__(self, *, actor: dict | None = None) -> None:
        self.frames: list[dict] = []
        self.actor = actor or {"subject": "owner-1", "session_id": "safe-point"}
        # ``runtime.py`` must derive actor identity from this authenticated
        # scope.  A command's actor/session fields are untrusted input.
        from openprogram.runtime.permissions.authority import owner_authority

        self.scope = {
            "state": {
                "authority": owner_authority("owner/install/0123456789abcdef"),
            },
        }

    async def send_text(self, text: str) -> None:
        self.frames.append(json.loads(text))


class ScriptedAgentOwner:
    """Minimal live owner used after the public chat admission.

    The owner records exact command ids.  Safe-point completion itself remains
    a control-service event, which mirrors the production boundary: the WS
    handler submits intent, while the owner publishes a checkpoint.
    """

    def __init__(self) -> None:
        self.pause_commands: list[str] = []
        self.activation_inputs = []
        self.handles: list[object] = []

    def capabilities(self) -> CapabilitySet:
        return CapabilitySet(
            pause=True,
            step=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ),
            state_schema_version=1,
        )

    async def request_pause(self, handle: object, command_id: str) -> DriverAck:
        self.pause_commands.append(command_id)
        return DriverAck(command_id=command_id, attempt_id=str(handle))

    async def request_cancel(self, handle: object, command_id: str) -> DriverAck:
        return DriverAck(command_id=command_id, attempt_id=str(handle))

    async def activate(self, attempt, activation):
        self.activation_inputs.append(activation)
        handle = object()
        self.handles.append(handle)
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=self,
            handle=handle,
        )

    async def inspect(self, handle: object):  # pragma: no cover - contract stub
        raise NotImplementedError

    async def terminate(self, handle: object, reason: str):  # pragma: no cover
        raise NotImplementedError


@pytest.fixture
def public_chat(tmp_path, monkeypatch):
    """Build an isolated canonical store while retaining the real chat entry."""

    from openprogram.state.store.session.session_store import SessionStore
    from openprogram.interfaces.server import server
    from openprogram.interfaces.server.ws_actions import chat as chat_actions
    import openprogram.runtime.conversation.session_config as session_config
    import openprogram.runtime.context.session_db as session_db
    import openprogram.state.store.session.session_store as session_store_module
    import openprogram.interfaces.server.ws_actions.session as session_actions

    execution_store = ExecutionStore(tmp_path / "execution.sqlite3")
    control = RuntimeControlService(
        execution_store,
        AttemptStore(execution_store),
        DriverRegistry(),
    )
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: execution_store)
    monkeypatch.setattr(
        "openprogram.runtime.execution.default_control_service", lambda: control,
    )

    session_store = SessionStore(tmp_path / "sessions-git")
    session_id = "safe-point-session"
    session_store.create_session(session_id, "main")
    conversation = {"id": session_id, "messages": []}
    monkeypatch.setattr(session_db, "default_db", lambda: session_store)
    monkeypatch.setattr(session_store_module.shared, "_default_store", session_store)
    monkeypatch.setattr(
        server,
        "_get_or_create_session",
        lambda sid, **_kwargs: conversation,
    )
    monkeypatch.setattr(chat_actions.turn, "_db_agent_id", lambda _sid: "main")
    monkeypatch.setattr(server, "_emit_running_task_event", lambda *_a, **_k: None)
    monkeypatch.setattr(session_actions, "broadcast_sessions_list", lambda: None)
    monkeypatch.setattr(server, "_broadcast", lambda *_a, **_k: None)
    # The real chat handler still executes all admission and ACK logic.  The
    # worker handoff is intentionally inert here so the command tests can
    # own the exact activation/recovery boundary without leaking a process-
    # global runtime registration between component tests.
    monkeypatch.setattr(server, "_try_reserve_run", lambda *_a, **_k: True)
    monkeypatch.setattr(server, "_activate_run_reservation", lambda *_a, **_k: True)
    monkeypatch.setattr(
        server,
        "_broadcast_envelope",
        lambda *_a, **_k: None,
        raising=False,
    )
    monkeypatch.setattr(
        session_config,
        "save_session_run_config",
        lambda *args, **kwargs: SimpleNamespace(
            tools_override=None,
            web_search=False,
            thinking_effort="medium",
            permission_mode="default",
            sandbox_enabled=None,
            additional_working_dirs=[],
        ),
    )
    monkeypatch.setattr(
        session_config,
        "permission_from_config",
        lambda _cfg, default=None: default or "default",
    )
    monkeypatch.setattr(
        session_config,
        "project_defaults",
        lambda _sid: {"permission_mode": "default"},
    )

    class NoopThread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    fake_threads_started_by(monkeypatch, TURN_LAUNCH_MODULE, NoopThread)

    try:
        yield session_id, execution_store, control, conversation
    finally:
        with server._running_tasks_lock:
            server._running_tasks.pop(session_id, None)


def _chat_ack(public_chat, *, text: str = "inspect this turn"):
    session_id, store, control, conversation = public_chat
    from openprogram.interfaces.server.ws_actions.chat import handle_chat

    ws = FakeWS()
    asyncio.run(handle_chat(ws, {"text": text, "session_id": session_id}))
    ack = next(frame for frame in ws.frames if frame.get("type") == "chat_ack")
    execution_id = ack["data"]["execution_id"]
    execution = store.get_execution(execution_id)
    assert execution is not None
    return ws, ack, execution, store, control


def _command_frame(ws: FakeWS) -> dict:
    return next(
        frame for frame in ws.frames if frame.get("type") == "execution.command.updated"
    )


def _command_payload(frame: dict) -> dict:
    return frame.get("command") or frame.get("data", {}).get("command")


def _run_action(action: str, ws: FakeWS, envelope: dict) -> None:
    """Submit a command only through the public runtime action registry."""

    from openprogram.interfaces.server.ws_actions import runtime

    envelope = {
        "type": "execution.command",
        "payload": {},
        **envelope,
    }
    handler = runtime.ACTIONS[action]
    asyncio.run(handler(ws, envelope))


def _agent_execution(
    tmp_path, *, execution_id: str = "exec-state-1", store: ExecutionStore | None = None
):
    """Create a canonical Agent execution and one active owner attempt."""

    store = store or ExecutionStore(tmp_path / "agent-state.sqlite3")
    revision = store.create_revision(
        revision_id=f"revision-{execution_id}",
        manifest={"entrypoint": "agent", "execution_id": execution_id},
    )
    execution = store.create_execution(
        execution_id=execution_id,
        run_id=f"run-{execution_id}",
        session_id=f"session-{execution_id}-{tmp_path.name}",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-state-owner",
        attempt_id=f"attempt-{execution_id}",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, active, running


def test_agent_state_blob_preserves_large_payloads_and_content_addressed_metadata(tmp_path):
    """Agent state refs preserve payloads and validate ownership and metadata."""

    store, _attempts, _active, execution = _agent_execution(tmp_path)
    from openprogram.runtime.execution.state_blobs import (
        ExecutionStateBlobStore,
        StateBlobConflict,
    )
    from openprogram.runtime.execution.store import MAX_AGENT_STATE_BLOB_BYTES

    blobs = ExecutionStateBlobStore(store)
    payload = b'{"answer":"persisted"}'
    record = blobs.put(
        execution_id=execution.execution_id,
        attempt_id=execution.current_attempt_id,
        name="assistant-message",
        payload=payload,
        media_type="application/json",
        schema_version=1,
    )
    expected_digest = hashlib.sha256(payload).hexdigest()
    assert record.ref == f"execstate://sha256/{expected_digest}"
    assert record.sha256 == expected_digest
    assert record.byte_length == len(payload)
    assert record.media_type == "application/json"
    assert record.schema_version == 1

    large_payload = b"x" * (MAX_AGENT_STATE_BLOB_BYTES + 1)
    large = blobs.put(
        execution_id=execution.execution_id,
        attempt_id=execution.current_attempt_id,
        name="large-result",
        payload=large_payload,
        media_type="application/octet-stream",
        schema_version=1,
    )
    assert store.get_state_blob(execution.execution_id, large.ref)["payload"] == large_payload

    with pytest.raises(StateBlobConflict) as invalid_ref:
        blobs.attach_ref(
            execution_id=execution.execution_id,
            ref="execstate://sha256/not-a-lowercase-64-hex-digest",
            name="bad-ref",
        )
    assert invalid_ref.value.code == "state_ref_invalid"


def test_agent_state_blob_ownership_and_gc_preserve_published_references(tmp_path):
    """GC must use durable references, never an in-memory registry."""

    store, _attempts, _active, first = _agent_execution(tmp_path, execution_id="exec-owner-1")
    _other_attempts, _ignored, _other_active, second = _agent_execution(
        tmp_path, execution_id="exec-owner-2", store=store
    )
    from openprogram.runtime.execution.state_blobs import ExecutionStateBlobStore

    blobs = ExecutionStateBlobStore(store)
    payload = b'{"shared":true}'
    first_blob = blobs.put(
        execution_id=first.execution_id,
        attempt_id=first.current_attempt_id,
        name="shared",
        payload=payload,
        media_type="application/json",
        schema_version=1,
    )
    second_blob = blobs.put(
        execution_id=second.execution_id,
        attempt_id=second.current_attempt_id,
        name="shared",
        payload=payload,
        media_type="application/json",
        schema_version=1,
    )
    assert second_blob.ref == first_blob.ref
    assert {
        first.execution_id,
        second.execution_id,
    } == blobs.owners(first_blob.ref)

    blobs.attach_ref(
        execution_id=first.execution_id,
        ref=first_blob.ref,
        name="checkpoint.state_refs.shared",
        reference_kind="checkpoint",
        reference_id="ckpt-owner-1",
    )
    blobs.gc(execution_id=first.execution_id)
    assert blobs.get(first_blob.ref, execution_id=first.execution_id) is not None

    blobs.detach_ref(
        execution_id=first.execution_id,
        ref=first_blob.ref,
        reference_kind="checkpoint",
        reference_id="ckpt-owner-1",
    )
    blobs.gc(execution_id=first.execution_id)
    assert blobs.get(first_blob.ref, execution_id=first.execution_id) is not None
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    finished = control.finish_attempt(
        attempt_id=first.current_attempt_id,
        generation=first.owner_lease["generation"],
        expected_execution_version=first.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    assert finished.execution.status is ExecutionStatus.COMPLETED
    blobs.gc(execution_id=first.execution_id)
    assert blobs.get(first_blob.ref, execution_id=first.execution_id) is None
    assert blobs.get(second_blob.ref, execution_id=second.execution_id) is not None


def _admitted_agent_execution(tmp_path, *, execution_id: str = "exec-restart-1"):
    store = ExecutionStore(tmp_path / "admitted-agent.sqlite3")
    revision = store.create_revision(
        revision_id=f"revision-{execution_id}", manifest={"entrypoint": "agent"}
    )
    execution = store.admit_execution(
        execution_id=execution_id,
        run_id=f"run-{execution_id}",
        session_id=f"session-{execution_id}-{tmp_path.name}",
        revision_id=revision.revision_id,
        input_ref=f"input:{execution_id}",
        input_hash=f"hash:{execution_id}",
        entrypoint="openprogram.runtime.conversation.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "agent-owner"},
        config_snapshot_ref=f"config:{execution_id}",
        user_message_id=f"user:{execution_id}",
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
                "agent.wait.before_tool",
            ),
            state_schema_version=1,
        ),
        agent_turn_payload={
            "version": 1,
            "kind": "chat",
            "request": {
                "user_text": "resume exactly once",
                "agent_id": "main",
                "source": "component",
            },
        },
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id=f"owner-{execution_id}",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, active, running


def checkpoint_snapshot(store, execution):
    from openprogram.runtime.execution.agent_journal import latest_pointer, load_journal

    pointer = latest_pointer(execution.session_id, execution.execution_id)
    return load_journal(execution.session_id, pointer).snapshot


def _real_provider_safe_point(tmp_path, *, tool_calls=True, pause=True, expose_hook=None, expected_status=None):
    """Create a checkpoint only through provider before/after callbacks."""

    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
    from openprogram.runtime.execution.model import CommandKind
    from openprogram.models.providers.types import AssistantMessage, Model

    control = RuntimeControlService(store, attempts, DriverRegistry())
    driver = AgentProductionDriver(store, control_service=control)
    request = TurnRequest(
        session_id=running.session_id,
        user_text="resume exactly once",
        agent_id="main",
        source="component",
        user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    hook = driver._safe_point_hook(active, request, threading.Event())
    if expose_hook is not None:
        expose_hook.append(hook)
    snapshot = runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system", tools=[], request=request,
    )
    assert hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}}) is False
    current = store.get_execution(running.execution_id)
    assert current is not None
    command = None
    if pause:
        command, _pausing, duplicate = store.accept_command_with_transition(
            command_id="pause-real-provider",
            execution_id=running.execution_id,
            expected_version=current.status_version,
            kind=CommandKind.PAUSE,
            target=ExecutionStatus.PAUSING,
            payload={},
            actor={"subject": "agent-owner"},
        )
        assert duplicate is False
    tool_content = (
        [{"type": "toolCall", "id": "tool-1", "name": "echo", "arguments": {}}]
        if tool_calls else [{"type": "text", "text": "terminal"}]
    )
    tool_call_ids = ["tool-1"] if tool_calls else []
    # Match agent_loop's durable payload, including model defaults, so
    # restoring AssistantMessage preserves the checkpoint's content hash.
    message = AssistantMessage(
        content=tool_content, api="openai-completions", provider="openai",
        model="fake", timestamp=1,
    ).model_dump(mode="json")
    assert hook("provider.after", {
        "message": message,
        "resolved_snapshot": snapshot,
        "turn": {
            "user_message_id": "user-anchor", "assistant_message_id": "assistant-anchor",
            "base_history_head_id": "user-anchor", "branch_id": "main",
        },
        "tool_call_ids": tool_call_ids,
        "next_tool_index": 0,
    }) is pause
    paused = store.get_execution(running.execution_id)
    assert paused is not None and paused.status is (expected_status or (ExecutionStatus.PAUSED if pause else ExecutionStatus.RUNNING))
    if pause:
        checkpoint = control.checkpoints.get(paused.checkpoint_head_id)
    else:
        checkpoint = control.publish_agent_position(
            attempt_id=active.attempt_id, generation=active.generation, fragment=hook._fragment(control),
        )
        paused = store.get_execution(running.execution_id)
    assert checkpoint is not None
    return store, control, active, paused, checkpoint, command


def test_approval_wait_hands_off_before_tool_effect_and_resumes_from_checkpoint(tmp_path):
    """An approval is created before tool dispatch, never by a blocked tool."""
    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution.waits import DurableWaitStore, WaitStatus
    from openprogram.models.providers.types import Model

    request = TurnRequest(
        session_id=running.session_id, user_text="approve", agent_id="main",
        source="component", user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    snapshot = runtime_contract_snapshot(
        model=Model(
            id="fake", name="fake", api="openai-completions", provider="openai",
            base_url="https://example.invalid/v1",
        ),
        system_prompt="system", tools=[], request=request,
    )
    activations = []

    async def activate(next_attempt, activation):
        activations.append(activation.checkpoint.checkpoint_id)
        return None

    control = RuntimeControlService(store, attempts, DriverRegistry(), activator=activate)
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active, request, threading.Event(),
    )
    assert hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}}) is False
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": [{
                "type": "toolCall", "id": "tool-approval", "name": "write", "arguments": {"path": "x"},
            }], "api": "openai-completions", "provider": "openai", "model": "fake", "timestamp": 1,
        },
        "resolved_snapshot": snapshot,
        "turn": {"user_message_id": "user-anchor", "assistant_message_id": "assistant-anchor", "base_history_head_id": "user-anchor", "branch_id": "main"},
        "tool_call_ids": ["tool-approval"], "next_tool_index": 0,
    }) is False
    assert hook("tool.before", {
        "tool_call_id": "tool-approval", "tool_name": "write", "arguments": {"path": "x"},
        "next_tool_index": 0,
        "pre_wait": {
            "kind": "approval", "prompt": "Allow?", "options": ["允许", "拒绝"],
            "detail": "write", "request_metadata": {"tool": "write", "tool_call_id": "tool-approval"},
            "policy_snapshot": {"version": 1, "on_answer": "continue", "on_decline": "fail", "on_timeout": "fail"},
        },
    }) is True
    paused = store.get_execution(running.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    waits = DurableWaitStore(store).list_open(execution_id=running.execution_id)
    assert len(waits) == 1 and waits[0].checkpoint_id == paused.checkpoint_head_id
    assert store._connect().count("effects", execution_id=running.execution_id) == 0

    dispatch = asyncio.run(control.request_wait_answer(
        command_id="answer-approval", execution_id=running.execution_id,
        expected_version=paused.status_version, actor={"surface": "test"},
        wait_id=waits[0].wait_id, generation=waits[0].claim_generation,
        answer={"answer": "允许", "scope": "once"},
    ))
    assert dispatch.execution.status is ExecutionStatus.RUNNING
    assert activations == [paused.checkpoint_head_id]
    assert DurableWaitStore(store).get_wait(waits[0].wait_id).status is WaitStatus.RESOLVED


def test_step_commits_one_real_tool_callback_and_consumes_the_permit_atomically(tmp_path):
    store, control, _active, paused, checkpoint, _command = _real_provider_safe_point(tmp_path)
    from openprogram.runtime.conversation.checkpoint import AgentContinuation
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution.driver import DriverBinding
    from openprogram.models.providers.types import AssistantMessage

    class StubDriver:
        def capabilities(self):  # pragma: no cover - registry only
            return paused.capabilities

    stub = StubDriver()

    async def activate(attempt, _activation):
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=stub,
            handle=object(),
        )

    dispatched = asyncio.run(control.request_step(
        command_id="step-real-tool",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"subject": "agent-owner"},
        activator=activate,
    ))
    active = control.attempts.get(dispatched.execution.current_attempt_id)
    assert active is not None
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=checkpoint,
        request=TurnRequest(
            session_id=paused.session_id, user_text="resume exactly once",
            agent_id="main", source="component", user_msg_id="user-anchor",
        ),
    )
    driver = AgentProductionDriver(store, control_service=control)
    hook = driver._safe_point_hook(
        active, continuation.request, threading.Event(), continuation=continuation,
    )
    assert hook("tool.before", {
        "tool_call_id": "tool-1", "tool_name": "echo", "arguments": {},
    }) is False
    assert hook("tool.after", {
        "tool_call_id": "tool-1", "tool_name": "echo",
        "result": {
            "role": "toolResult", "tool_call_id": "tool-1", "tool_name": "echo",
            "content": [{"type": "text", "text": "ok"}], "timestamp": 2,
        },
        "is_error": False, "next_tool_index": 1,
        "repeat_failures": {}, "tool_call_ids": ["tool-1"],
    }) is True
    command = store.get_command("step-real-tool")
    current = store.get_execution(paused.execution_id)
    assert command is not None and command.status is CommandStatus.APPLIED
    assert current is not None and current.status is ExecutionStatus.PAUSED
    assert command.result_json.get("managed_action_id")


def test_step_rejects_terminal_provider_checkpoint_without_leaving_a_permit_applying(tmp_path):
    store, control, _active, paused, _checkpoint, _command = _real_provider_safe_point(
        tmp_path, tool_calls=False,
    )

    dispatched = asyncio.run(control.request_step(
        command_id="step-terminal-provider",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"subject": "agent-owner"},
        activator=lambda *_args: pytest.fail("terminal STEP must not activate an owner"),
    ))

    command = store.get_command("step-terminal-provider")
    current = store.get_execution(paused.execution_id)
    assert dispatched.delivered is False
    assert command is not None
    assert command.status is CommandStatus.REJECTED
    assert command.rejection_code == "no_next_action"
    assert current == paused
    assert store._connect().count("attempts", execution_id=paused.execution_id) == 1


def test_chat_ack_pause_before_initial_activation_continues_the_admitted_turn_once(tmp_path):
    from openprogram.runtime.conversation.production_driver import (
        AgentProductionDriver, CanonicalAgentEntry,
    )

    store = ExecutionStore(tmp_path / "initial-pause.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    provider_starts = []

    def run_turn(*, request, cancel_event):
        provider_starts.append((request.session_id, cancel_event.is_set()))
        return SimpleNamespace(failed=False)

    driver = AgentProductionDriver(
        store, control_service=control, turn_runner=run_turn,
    )
    entry = CanonicalAgentEntry(store, driver)
    admission = entry.admit(
        session_id="initial-pause-session",
        turn_payload={
            "version": 1, "kind": "chat",
            "request": {"user_text": "pause before start", "agent_id": "main", "source": "component"},
        },
        trusted_actor={"subject": "agent-owner"},
        user_message_id="user-initial", assistant_message_id="assistant-initial",
        config_snapshot_ref="config:initial",
    )
    queued = store.get_execution(admission.execution_id)
    assert queued is not None
    paused = asyncio.run(control.request_pause(
        command_id="pause-before-initial-activation",
        execution_id=queued.execution_id,
        expected_version=queued.status_version,
        actor={"subject": "agent-owner"},
    ))
    assert paused.execution.status is ExecutionStatus.PAUSED
    assert paused.execution.checkpoint_head_id is None
    assert asyncio.run(entry.activate(admission)) is None
    assert provider_starts == []

    continued = asyncio.run(control.request_continue(
        command_id="continue-after-initial-pause",
        execution_id=paused.execution.execution_id,
        expected_version=paused.execution.status_version,
        actor={"subject": "agent-owner"},
        activator=driver.activate,
    ))
    assert continued.command.status is CommandStatus.APPLIED
    deadline = time.monotonic() + 2.0
    while not provider_starts and time.monotonic() < deadline:
        threading.Event().wait(0.01)
    assert provider_starts == [("initial-pause-session", False)]
    assert store._connect().count("attempts", execution_id=admission.execution_id) == 1


def test_public_cancel_uses_a_durable_exact_command_envelope(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat)
    cancel_ws = FakeWS()
    _run_action(
        "execution.cancel",
        cancel_ws,
        {
            "action": "execution.cancel",
            "command_id": "cancel-envelope-1",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )
    command = _command_payload(_command_frame(cancel_ws))
    current = store.get_execution(execution.execution_id)
    assert command["status"] == CommandStatus.APPLIED.value
    assert command["execution_id"] == execution.execution_id
    assert current is not None and current.status is ExecutionStatus.CANCELLED
    stored = store.get_command("cancel-envelope-1")
    assert stored is not None and stored.actor.get("subject") != "spoofed-client"

    retry_ws = FakeWS()
    _run_action(
        "execution.cancel", retry_ws,
        {
            "action": "execution.cancel", "command_id": "cancel-envelope-1",
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
        },
    )
    assert _command_payload(_command_frame(retry_ws))["status"] == CommandStatus.APPLIED.value

    # A stop names no version: the same command id seen at another version
    # is the same stop, not a different request.
    replay_ws = FakeWS()
    _run_action(
        "execution.cancel", replay_ws,
        {
            "action": "execution.cancel", "command_id": "cancel-envelope-1",
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version + 1,
        },
    )
    assert _command_payload(_command_frame(replay_ws))["status"] == CommandStatus.APPLIED.value
    assert len(store.list_commands(execution.execution_id, kinds=(CommandKind.CANCEL,))) == 1
def test_stale_step_without_a_live_owner_returns_latest_snapshot_without_mutation(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat)
    first_ws = FakeWS()
    _run_action(
        "execution.pause",
        first_ws,
        {
            "action": "execution.pause",
            "command_id": "pause-for-stale",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )
    current = store.get_execution(execution.execution_id)
    assert current is not None
    events_before = store.list_events(execution.execution_id)
    stale_ws = FakeWS()
    _run_action(
        "execution.step",
        stale_ws,
        {
            "action": "execution.step",
            "command_id": "step-stale",
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version,
        },
    )
    command = _command_payload(_command_frame(stale_ws))
    assert command["status"] == CommandStatus.REJECTED.value
    assert command["rejection_code"] in {"stale_version", "activation_unavailable"}
    assert command["latest_snapshot"]["status_version"] == current.status_version
    assert store.get_execution(execution.execution_id) == current
    assert store.list_events(execution.execution_id) == events_before


def test_unsupported_handler_and_missing_activation_are_rejected_without_mutation(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat, text="/forced_tool")
    before = store.get_execution(execution.execution_id)
    assert before is not None
    action_ws = FakeWS()
    _run_action(
        "execution.pause",
        action_ws,
        {
            "action": "execution.pause",
            "command_id": "pause-unsupported",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": before.status_version,
        },
    )
    rejected = _command_payload(_command_frame(action_ws))
    assert rejected["status"] == CommandStatus.REJECTED.value
    assert rejected["rejection_code"] == "unsupported_capability"
    assert store.get_execution(execution.execution_id) == before

    from openprogram.runtime.execution.safe_points import AgentSafePointConflict
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    with pytest.raises(AgentSafePointConflict) as unavailable:
        asyncio.run(
            control.request_continue(
                command_id="continue-without-activator",
                execution_id=execution.execution_id,
                expected_version=before.status_version,
                actor={"subject": "owner-1"},
                activator=None,
                driver=None,
            )
        )
    assert unavailable.value.code == "activation_unavailable"
    assert store.get_execution(execution.execution_id) == before


def test_chat_ack_pause_intent_does_not_fabricate_a_checkpoint_without_callback(public_chat):
    _ws, ack, execution, store, control = _chat_ack(public_chat)
    action_ws = FakeWS()

    _run_action(
        "execution.pause",
        action_ws,
        {
            "action": "execution.pause",
            "command_id": "pause-provider-1",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )

    command = _command_payload(_command_frame(action_ws))
    assert command["execution_id"] == ack["data"]["execution_id"]
    assert command["status"] in {"accepted", "applied"}
    current = store.get_execution(execution.execution_id)
    assert current is not None
    # This fixture deliberately suppresses the worker thread.  The public
    # handler may submit pause intent, but only a real provider/tool callback
    # can publish a checkpoint (covered by _real_provider_safe_point above).
    assert current.checkpoint_head_id is None
    assert current.status in {
        ExecutionStatus.RUNNING, ExecutionStatus.PAUSING, ExecutionStatus.PAUSED,
    }


def test_continue_without_a_published_checkpoint_is_rejected(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat)
    continue_ws = FakeWS()

    _run_action(
        "execution.continue",
        continue_ws,
        {
            "action": "execution.continue",
            "command_id": "continue-tools-1",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )

    command = _command_payload(_command_frame(continue_ws))
    assert command["execution_id"] == execution.execution_id
    assert command["status"] == "rejected"
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current == execution


def test_step_without_a_published_checkpoint_is_rejected(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat)
    step_ws = FakeWS()

    _run_action(
        "execution.step",
        step_ws,
        {
            "action": "execution.step",
            "command_id": "step-one-action",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )

    command = _command_payload(_command_frame(step_ws))
    assert command["status"] == "rejected"
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current == execution


def test_commands_have_cas_actor_and_idempotent_outcomes(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat)
    first_ws = FakeWS()
    envelope = {
        "action": "execution.pause",
        "command_id": "pause-idempotent-1",
        "execution_id": ack["data"]["execution_id"],
        "expected_version": execution.status_version,
    }
    _run_action("execution.pause", first_ws, envelope)
    first = _command_payload(_command_frame(first_ws))
    after_first = store.get_execution(execution.execution_id)
    assert after_first is not None

    duplicate_ws = FakeWS()
    _run_action("execution.pause", duplicate_ws, envelope)
    duplicate = _command_payload(_command_frame(duplicate_ws))
    assert duplicate["command_id"] == first["command_id"]
    assert duplicate["status"] == first["status"]
    assert len(store.list_commands(execution.execution_id)) == 1

    stale_ws = FakeWS()
    _run_action(
        "execution.pause",
        stale_ws,
        {
            **envelope,
            "command_id": "pause-stale-version",
            "expected_version": execution.status_version,
        },
    )
    stale = _command_payload(_command_frame(stale_ws))
    assert stale["status"] == CommandStatus.REJECTED.value
    assert stale["rejection_code"] == "stale_version"
    stored = store.get_command(first["command_id"])
    assert stored is not None
    assert stored.actor.get("subject") != "spoofed-client"


def test_no_callback_path_has_no_checkpoint_to_expose(public_chat):
    _ws, ack, execution, store, control = _chat_ack(public_chat)
    pause_ws = FakeWS()
    _run_action(
        "execution.pause",
        pause_ws,
        {
            "action": "execution.pause",
            "command_id": "pause-anchor-1",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": execution.status_version,
        },
    )
    current = store.get_execution(execution.execution_id)
    assert current is not None and current.checkpoint_head_id is None


def test_effect_dispatch_crash_blocks_continue_and_step_until_reconciled(public_chat):
    _ws, ack, execution, store, control = _chat_ack(public_chat)
    effects = control.effects
    # The public command path must observe this unresolved effect and reject
    # resume; dispatch without a terminal receipt is never resumable state.
    attempts = control.attempts
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="effect-owner",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    from openprogram.runtime.execution.effects import EffectClassification

    effect = effects.register(
        effect_id="effect-crash-1",
        execution_id=execution.execution_id,
        attempt_id=active.attempt_id,
        action_id="tool-action-1",
        classification=EffectClassification.NONREPEATABLE,
        idempotency_key=None,
        metadata={"tool": "fixture"},
    )
    effects.mark_dispatched(effect.effect_id, expected_status=effect.status)
    effects.mark_uncertain(effect.effect_id, expected_status=EffectStatus.DISPATCHED)

    resume_ws = FakeWS()
    _run_action(
        "execution.continue",
        resume_ws,
        {
            "action": "execution.continue",
            "command_id": "continue-after-effect-crash",
            "execution_id": ack["data"]["execution_id"],
            "expected_version": running.status_version,
        },
    )
    command = _command_payload(_command_frame(resume_ws))
    assert command["status"] == CommandStatus.REJECTED.value
    assert command["rejection_code"] == "unresolved_effect"
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.RECONCILIATION_REQUIRED


@pytest.mark.parametrize("text", ["/forced_tool", "/spawn child", "/merge child"])
def test_nonordinary_agent_entries_do_not_claim_pause_step_or_durable_wait(
    public_chat, text,
):
    _ws, ack, execution, _store, _control = _chat_ack(public_chat, text=text)
    capabilities = execution.capabilities
    assert not capabilities.pause
    assert not capabilities.step
    assert capabilities.safe_point_kinds == ()
    assert "execution.pause" not in capabilities
    assert "execution.step" not in capabilities


@pytest.mark.parametrize("stop_reason,structured", [
    ("error", False), ("aborted", False), ("error", True), ("length", True), ("invalid", True), ("repair", True),
])
def test_returned_provider_failure_finishes_without_attention(tmp_path, stop_reason, structured):
    from openprogram.runtime.agent.loop import agent_loop
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.agent.loop.types import AgentContext, AgentLoopConfig
    from openprogram.models.providers.types import AssistantMessage, EventError, EventDone, Model, TextContent
    from openprogram.models.providers.structured_output import (
        normalize_response_format, StructuredOutputGenerationError, StructuredOutputValidationError,
    )

    store, attempts, active, execution = _admitted_agent_execution(tmp_path)
    control = RuntimeControlService(store, attempts, DriverRegistry())
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active, SimpleNamespace(session_id=execution.session_id, user_msg_id="user-anchor", _execution_revision_id=execution.revision_id), threading.Event(),
    )

    async def safe_point(kind, payload):
        return hook(kind, payload)

    responses = []

    async def provider(model, context, options):
        content = "{}" if stop_reason == "repair" and responses else "invalid json"
        responses.append(content)
        message = AssistantMessage(
            content=[TextContent(text=content)], api=model.api, provider=model.provider, model=model.id,
            stop_reason="stop" if stop_reason in {"invalid", "repair"} else stop_reason,
            error_message="provider stopped", timestamp=1,
        )
        if stop_reason in {"error", "aborted"}:
            yield EventError(reason=stop_reason, error=message)
        else:
            yield EventDone(reason=message.stop_reason, message=message)

    async def run():
        from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
        snapshot = runtime_contract_snapshot(
            model=Model(id="fake", name="fake", api="openai-completions", provider="openai", base_url="https://example.invalid/v1"),
            system_prompt="", tools=[], request=SimpleNamespace(_execution_revision_id=execution.revision_id),
        )
        stream = agent_loop([], AgentContext(messages=[], tools=[], runtime_contract=snapshot), AgentLoopConfig(
            model=Model(id="fake", name="fake", api="openai-completions", provider="openai",
                        base_url="https://example.invalid/v1"),
            convert_to_llm=lambda messages: messages, safe_point_hook=safe_point,
            response_format=normalize_response_format({
                "type": "json_schema", "schema": {"type": "object"},
                "fallback": "prompt", "max_validation_retries": 1 if stop_reason == "repair" else 0,
            }) if structured else None,
        ), stream_fn=provider)
        async for _ in stream:
            pass
        await stream.result()

    if stop_reason in {"length", "invalid"}:
        with pytest.raises((StructuredOutputGenerationError, StructuredOutputValidationError)):
            asyncio.run(run())
    else:
        asyncio.run(run())
    finished = control.finish_attempt(
        attempt_id=active.attempt_id, generation=active.generation,
        expected_execution_version=store.get_execution(execution.execution_id).status_version,
        target=ExecutionStatus.COMPLETED if stop_reason == "repair" else ExecutionStatus.FAILED,
        outcome=stop_reason,
    )
    assert finished.execution.status is (ExecutionStatus.COMPLETED if stop_reason == "repair" else ExecutionStatus.FAILED)
    if stop_reason == "repair":
        assert responses == ["invalid json", "{}"]
    assert control.effects.list_unresolved(execution.execution_id) == []
    # A returned provider failure is still an authoritative result. Its exact
    # message is in the journal, while the execution remains failed.
    from openprogram.runtime.execution.agent_journal import latest_pointer, load_journal
    state = load_journal(execution.session_id, latest_pointer(execution.session_id, execution.execution_id))
    message = state.last.message
    assert message["content"][0]["text"] == responses[-1]
    assert message["stop_reason"] == ("stop" if stop_reason in {"invalid", "repair"} else stop_reason)


@pytest.mark.parametrize("kind,orphan", [("provider.before", True), ("tool.before", False)])
@pytest.mark.parametrize("attention", [None, "wait", "command", "cancel", "cancel_applying"])
def test_snapshot_distinguishes_ended_provider_receipt_from_action_attention(tmp_path, monkeypatch, kind, orphan, attention):
    from openprogram.runtime.execution.effects import EffectClassification, EffectStatus
    from openprogram.runtime.execution.public import execution_snapshot

    store, attempts, active, execution = _admitted_agent_execution(tmp_path)
    control = RuntimeControlService(store, attempts, DriverRegistry())
    control.effects.register(effect_id="effect-orphan", execution_id=execution.execution_id,
                             attempt_id=active.attempt_id, action_id="action-orphan",
                             classification=EffectClassification.NONREPEATABLE,
                             idempotency_key=None, metadata={"kind": kind})
    control.effects.mark_dispatched("effect-orphan", expected_status=EffectStatus.PLANNED)
    if attention == "wait":
        from openprogram.runtime.execution.waits import DurableWaitStore
        DurableWaitStore(store).open_wait(
            execution_id=execution.execution_id, attempt_id=active.attempt_id,
            generation=active.generation, kind="confirm", request={"prompt": "Confirm?"},
            policy_snapshot={}, expires_at=time.time() + 60,
        )
    elif attention in {"command", "cancel", "cancel_applying"}:
        from openprogram.runtime.execution.model import CommandKind, CommandStatus
        store.accept_command(command_id="pause-attention", execution_id=execution.execution_id,
                             expected_version=execution.status_version, kind=CommandKind.PAUSE if attention == "command" else CommandKind.CANCEL,
                             payload={}, actor={"subject": "owner"})
        if attention == "cancel_applying":
            store.transition_command("pause-attention", expected_status=CommandStatus.ACCEPTED,
                                     target=CommandStatus.APPLYING)
    assert execution_snapshot(execution, store=store).effect_summary.get("provider_response_incomplete") is not True
    ended = control.finish_attempt(
        attempt_id=active.attempt_id, generation=active.generation,
        expected_execution_version=execution.status_version,
        target=ExecutionStatus.FAILED, outcome="error",
    ).execution
    snapshot = execution_snapshot(ended, store=store)
    assert snapshot.effect_summary.get("provider_response_incomplete", False) is (orphan and attention in {None, "cancel", "cancel_applying"})
    assert snapshot.status == "reconciliation_required"
    assert len(control.effects.list_unresolved(execution.execution_id)) == 1
    from openprogram.programs.tools.planning.goal.execution import goal_execution_state
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    observed = goal_execution_state({"execution_id": ended.execution_id}, ended.session_id)
    assert observed["finished"] is False
    # A new inspection turn is allowed for either unknown outcome, but it
    # never resumes the old tool invocation or resolves its effect receipt.
    assert observed["can_start_new_turn"] is (attention in {None, "cancel", "cancel_applying"})
    if observed["can_start_new_turn"]:
        assert observed["recovery_mode"] == "restricted_new_turn"


def test_normal_provider_completion_records_continuation_without_pause(tmp_path):
    store, control, active, running, checkpoint, command = _real_provider_safe_point(tmp_path, pause=False)
    assert command is None
    assert store.list_commands(running.execution_id) == []
    assert running.current_attempt_id == active.attempt_id
    assert checkpoint.created_by_attempt_id == active.attempt_id
    assert control.effects.list_unresolved(running.execution_id) == []
    assert checkpoint.state_refs["agent_position"]["next_tool_index"] == 0
    assert list(checkpoint.state_refs["agent_position"]["current_decision"]["tool_call_ids"]) == ["tool-1"]


@pytest.mark.parametrize("boundary", ["tool.before", "tool.started", "provider.before"])
@pytest.mark.parametrize("timing", ["before", "admission"])
def test_pause_between_agent_effects_hands_off_without_failure(tmp_path, monkeypatch, boundary, timing):
    hooks = []
    store, control, active, running, checkpoint, _ = _real_provider_safe_point(
        tmp_path, pause=False, expose_hook=hooks,
    )
    hook = hooks[0]
    tool_payload = {"tool_call_id": "tool-1", "tool_name": "echo", "arguments": {}}
    if boundary == "tool.started":
        assert hook("tool.before", tool_payload) is False
    from openprogram.runtime.execution.model import CommandKind
    def pause():
        store.accept_command_with_transition(
            command_id="pause-between-effects", execution_id=running.execution_id,
            expected_version=store.get_execution(running.execution_id).status_version,
            kind=CommandKind.PAUSE, target=ExecutionStatus.PAUSING,
            payload={}, actor={"subject": "agent-owner"},
        )
    if timing == "before":
        pause()
    else:
        original = store.get_execution
        def race(*args, **kwargs):
            monkeypatch.setattr(store, "get_execution", original)
            pause()
            return original(*args, **kwargs)
        monkeypatch.setattr(store, "get_execution", race)
    tool_payload["resolved_snapshot"] = checkpoint_snapshot(store, running)
    assert hook(boundary, tool_payload) is True
    paused = store.get_execution(running.execution_id)
    assert paused.status is ExecutionStatus.PAUSED
    assert store.get_command("pause-between-effects").status is CommandStatus.APPLIED
    position = control.checkpoints.get(paused.checkpoint_head_id).state_refs["agent_position"]
    assert position["next_tool_index"] == 0
    assert list(position["current_decision"]["tool_call_ids"]) == ["tool-1"]

    # A new owner must admit the untouched tool once, including when an old
    # owner had registered (but never dispatched) that exact tool identity.
    monkeypatch.undo()
    async def activate(next_attempt, _activation):
        return DriverBinding(execution_id=next_attempt.execution_id,
            attempt_id=next_attempt.attempt_id, generation=next_attempt.generation,
            driver=SimpleNamespace(), handle=object())

    resumed = asyncio.run(control.request_continue(
        command_id="resume-between-effects", execution_id=paused.execution_id,
        expected_version=paused.status_version, actor={"subject": "agent-owner"},
        activator=activate,
    ))
    assert resumed.delivered
    from openprogram.runtime.conversation.checkpoint import AgentContinuation
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.models.providers.types import AssistantMessage
    request = TurnRequest(session_id=paused.session_id, user_text="resume exactly once",
                          agent_id="main", source="component", user_msg_id="user-anchor")
    continuation = AgentContinuation.from_checkpoint(
        store=store, checkpoint=control.checkpoints.get(paused.checkpoint_head_id), request=request,
    )
    next_attempt = control.attempts.get(resumed.execution.current_attempt_id)
    resumed_hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        next_attempt, request, threading.Event(), continuation=continuation)
    assert resumed_hook("tool.before", tool_payload) is False
    assert resumed_hook("tool.started", tool_payload) is False


@pytest.mark.parametrize("timing", ["before", "dispatch"])
def test_active_initial_pause_resumes_original_admission(tmp_path, monkeypatch, timing):
    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
    from openprogram.runtime.execution.model import CommandKind
    from openprogram.models.providers.types import Model
    control = RuntimeControlService(store, attempts, DriverRegistry())
    request = TurnRequest(session_id=running.session_id, user_text="original",
                          agent_id="main", source="component", user_msg_id="user-anchor")
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active, request, threading.Event())
    snapshot = runtime_contract_snapshot(
        model=Model(id="fake", name="fake", api="openai-completions", provider="openai",
                    base_url="https://example.invalid/v1"),
        system_prompt="", tools=[], request=request)

    def pause():
        store.accept_command_with_transition(
            command_id="pause-initial-active", execution_id=running.execution_id,
            expected_version=store.get_execution(running.execution_id).status_version,
            kind=CommandKind.PAUSE, target=ExecutionStatus.PAUSING,
            payload={}, actor={"subject": "agent-owner"})
    if timing == "before":
        pause()
    else:
        original = store.get_execution
        def race(*args, **kwargs):
            monkeypatch.setattr(store, "get_execution", original)
            pause()
            return original(*args, **kwargs)
        monkeypatch.setattr(store, "get_execution", race)
    assert hook("provider.before", {"resolved_snapshot": snapshot, "context": {}}) is True
    paused = store.get_execution(running.execution_id)
    assert paused.status is ExecutionStatus.PAUSED
    assert paused.checkpoint_head_id is None
    assert store.get_command("pause-initial-active").status is CommandStatus.APPLIED
    monkeypatch.undo()
    activated = []
    async def activate(next_attempt, activation):
        activated.append(activation)
        return DriverBinding(execution_id=next_attempt.execution_id,
            attempt_id=next_attempt.attempt_id, generation=next_attempt.generation,
            driver=SimpleNamespace(), handle=object())
    resumed = asyncio.run(control.request_continue(
        command_id="resume-initial-active", execution_id=paused.execution_id,
        expected_version=paused.status_version, actor={"subject": "agent-owner"}, activator=activate))
    assert resumed.delivered and len(activated) == 1
    assert activated[0].checkpoint is None
    next_attempt = attempts.get(resumed.execution.current_attempt_id)
    next_hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        next_attempt, request, threading.Event())
    assert next_hook("provider.before", {"resolved_snapshot": snapshot, "context": {}}) is False

