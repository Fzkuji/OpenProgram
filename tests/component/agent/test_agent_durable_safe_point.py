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
import sqlite3
import threading
from types import SimpleNamespace

import pytest

from openprogram.execution import AttemptStore, ExecutionStore, RuntimeControlService
from openprogram.execution.driver import DriverAck, DriverBinding, DriverRegistry
from openprogram.execution.model import CapabilitySet, CommandStatus, ExecutionStatus
from openprogram.execution.effects import EffectStatus


class FakeWS:
    def __init__(self, *, actor: dict | None = None) -> None:
        self.frames: list[dict] = []
        self.actor = actor or {"subject": "owner-1", "session_id": "safe-point"}
        # ``runtime.py`` must derive actor identity from this authenticated
        # scope.  A command's actor/session fields are untrusted input.
        from openprogram.agent.authority import owner_authority

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

    from openprogram.store.session.session_store import SessionStore
    from openprogram.webui import server
    from openprogram.webui.ws_actions import chat as chat_actions
    import openprogram.agent.session_config as session_config
    import openprogram.agent.session_db as session_db
    import openprogram.store.session.session_store as session_store_module
    import openprogram.webui.ws_actions.session as session_actions

    execution_store = ExecutionStore(tmp_path / "execution.sqlite3")
    control = RuntimeControlService(
        execution_store,
        AttemptStore(execution_store),
        DriverRegistry(),
    )
    monkeypatch.setattr("openprogram.execution.default_store", lambda: execution_store)
    monkeypatch.setattr(
        "openprogram.execution.default_control_service", lambda: control,
    )

    session_store = SessionStore(tmp_path / "sessions-git")
    session_id = "safe-point-session"
    session_store.create_session(session_id, "main")
    conversation = {"id": session_id, "messages": []}
    monkeypatch.setattr(session_db, "default_db", lambda: session_store)
    monkeypatch.setattr(session_store_module, "_default_store", session_store)
    monkeypatch.setattr(
        server,
        "_get_or_create_session",
        lambda sid, **_kwargs: conversation,
    )
    monkeypatch.setattr(chat_actions, "_db_agent_id", lambda _sid: "main")
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
            tools_enabled=True,
            tools_override=None,
            web_search=False,
            toolset=None,
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

    monkeypatch.setattr(threading, "Thread", NoopThread)
    monkeypatch.setattr(
        "openprogram.agent.workspace_alignment.get_workspace_alignment",
        lambda _sid: {"status": "aligned"},
    )

    try:
        yield session_id, execution_store, control, conversation
    finally:
        with server._running_tasks_lock:
            server._running_tasks.pop(session_id, None)


def _chat_ack(public_chat, *, text: str = "inspect this turn"):
    session_id, store, control, conversation = public_chat
    from openprogram.webui.ws_actions.chat import handle_chat

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

    from openprogram.webui.ws_actions import runtime

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
        session_id=f"session-{execution_id}",
        revision_id=revision.revision_id,
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
            ),
            state_schema_version=1,
        ),
    )
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id,
        expected_version=execution.status_version,
        owner_id="agent-state-owner",
        ttl_seconds=30,
        attempt_id=f"attempt-{execution_id}",
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, active, running


def test_agent_state_blob_enforces_caps_and_content_addressed_metadata(tmp_path):
    """Agent state refs must be bounded, canonical, and self-describing."""

    store, _attempts, _active, execution = _agent_execution(tmp_path)
    from openprogram.execution.state_blobs import (
        MAX_AGENT_STATE_BLOB_BYTES,
        ExecutionStateBlobStore,
        StateBlobConflict,
    )

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

    with pytest.raises(StateBlobConflict) as too_large:
        blobs.put(
            execution_id=execution.execution_id,
            attempt_id=execution.current_attempt_id,
            name="oversized",
            payload=b"x" * (MAX_AGENT_STATE_BLOB_BYTES + 1),
            media_type="application/octet-stream",
            schema_version=1,
        )
    assert too_large.value.code == "state_blob_too_large"

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
    from openprogram.execution.state_blobs import ExecutionStateBlobStore

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


@pytest.mark.parametrize(
    ("safe_point_kind", "action_id"),
    [
        ("agent.provider.decision.after", "provider-action-1"),
        ("agent.tool.action.after", "tool-action-1"),
    ],
)
def test_agent_terminal_receipt_blob_checkpoint_frontier_roll_back_atomically(
    tmp_path, safe_point_kind, action_id
):
    """A fault after receipt persistence must roll back the whole safe point."""

    store, _attempts, _active, execution = _agent_execution(tmp_path)
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    from openprogram.execution.effects import EffectClassification
    from openprogram.execution.safe_points import AgentSafePointConflict

    effect = control.effects.register(
        effect_id=f"effect-{action_id}",
        execution_id=execution.execution_id,
        attempt_id=execution.current_attempt_id,
        action_id=action_id,
        classification=EffectClassification.IDEMPOTENT,
        idempotency_key=f"{execution.execution_id}:{action_id}",
        metadata={"kind": safe_point_kind},
    )
    control.effects.mark_dispatched(effect.effect_id, expected_status=effect.status)
    before = store.get_execution(execution.execution_id)
    assert before is not None
    before_events = store.list_events(execution.execution_id)
    terminal_receipt = (
        {"provider_request_id": f"request-{action_id}"}
        if "provider" in safe_point_kind
        else {"tool_invocation_id": f"invocation-{action_id}", "result": "ok"}
    )

    with pytest.raises(AgentSafePointConflict) as injected:
        control.commit_agent_safe_point(
            execution_id=execution.execution_id,
            attempt_id=execution.current_attempt_id,
            generation=execution.owner_lease["generation"],
            expected_version=before.status_version,
            safe_point_kind=safe_point_kind,
            frontier=({"step_id": action_id, "phase": "after_provider" if "provider" in safe_point_kind else "after_tool"},),
            state_refs={},
            effect_id=effect.effect_id,
            terminal_receipt=terminal_receipt,
            receipt_blob=b'{"receipt":"terminal"}',
            fault_at="after_receipt_blob",
        )
    assert injected.value.code == "injected_failure"

    after = store.get_execution(execution.execution_id)
    assert after is not None
    assert after.status_version == before.status_version
    assert after.checkpoint_head_id is None
    assert control.effects.get(effect.effect_id).status.value == "dispatched"
    assert store.list_events(execution.execution_id) == before_events
    assert not control.state_blobs.list(execution.execution_id)
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM checkpoints WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()[0] == 0


def _admitted_agent_execution(tmp_path, *, execution_id: str = "exec-restart-1"):
    store = ExecutionStore(tmp_path / "admitted-agent.sqlite3")
    revision = store.create_revision(
        revision_id=f"revision-{execution_id}", manifest={"entrypoint": "agent"}
    )
    execution = store.admit_execution(
        execution_id=execution_id,
        run_id=f"run-{execution_id}",
        session_id=f"session-{execution_id}",
        revision_id=revision.revision_id,
        input_ref=f"input:{execution_id}",
        input_hash=f"hash:{execution_id}",
        entrypoint="openprogram.agent.production_driver:AgentProductionDriver",
        trusted_actor={"subject": "agent-owner"},
        config_snapshot_ref=f"config:{execution_id}",
        user_message_id=f"user:{execution_id}",
        capabilities=CapabilitySet(
            pause=True,
            step=True,
            safe_point_kinds=(
                "agent.provider.decision.after",
                "agent.tool.action.after",
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
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    return store, attempts, active, running


def _real_provider_safe_point(tmp_path, *, tool_calls=True):
    """Create a checkpoint only through provider before/after callbacks."""

    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.model import CommandKind

    control = RuntimeControlService(store, attempts, DriverRegistry())
    driver = AgentProductionDriver(store, control_service=control)
    request = TurnRequest(
        session_id=running.session_id,
        user_text="resume exactly once",
        agent_id="main",
        source="component",
        user_msg_id="user-anchor",
    )
    hook = driver._safe_point_hook(active, request, threading.Event())
    snapshot = {"model": {"id": "fake"}, "system_prompt": "system", "tools": []}
    assert hook("provider.before", {"resolved_snapshot": snapshot, "context": {"messages": []}}) is False
    current = store.get_execution(running.execution_id)
    assert current is not None
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
    assert hook("provider.after", {
        "message": {
            "role": "assistant", "content": tool_content,
            "api": "openai-completions", "provider": "openai", "model": "fake", "timestamp": 1,
        },
        "resolved_snapshot": snapshot,
        "turn": {
            "user_message_id": "user-anchor", "assistant_message_id": "assistant-anchor",
            "base_history_head_id": "user-anchor", "branch_id": "main",
        },
        "tool_call_ids": tool_call_ids,
        "next_tool_index": 0,
    }) is True
    paused = store.get_execution(running.execution_id)
    assert paused is not None and paused.status is ExecutionStatus.PAUSED
    checkpoint = control.checkpoints.get(paused.checkpoint_head_id)
    assert checkpoint is not None
    return store, control, active, paused, checkpoint, command


def test_real_provider_callback_persists_v1_content_addressed_checkpoint(tmp_path):
    store, _control, active, paused, checkpoint, command = _real_provider_safe_point(tmp_path)
    from openprogram.agent.continuation import AgentCheckpointV1

    state = AgentCheckpointV1.load(store, checkpoint)
    stored_command = store.get_command(command.command_id)
    assert stored_command is not None and stored_command.status is CommandStatus.APPLIED
    assert paused.owner_lease == {}
    assert checkpoint.created_by_attempt_id == active.attempt_id
    assert state.payload["turn"] == {
        "user_message_id": "user-anchor",
        "assistant_message_id": "assistant-anchor",
        "base_history_head_id": "user-anchor",
    }
    assert state.payload["current_decision"]["tool_call_ids"] == ["tool-1"]
    assert state.payload["next_tool_index"] == 0
    assert state.payload["assistant_message_delta_ref"]["ref"].startswith("execstate://sha256/")
    assert state.payload["resolved_model_system_tool_snapshot_ref"]["media_type"] == "application/json"


def test_published_agent_checkpoint_children_survive_terminal_gc_and_load(tmp_path):
    """Terminal GC keeps every blob reachable through the Agent schema."""
    store, control, _active, paused, checkpoint, _command = _real_provider_safe_point(tmp_path)
    from openprogram.agent.continuation import AgentCheckpointV1

    state = AgentCheckpointV1.load(store, checkpoint)
    child_refs = {
        descriptor["ref"] for descriptor in state.payload["state_refs"].values()
    }
    with store._connect() as connection:
        registered = {
            row["ref"] for row in connection.execute(
                "SELECT ref FROM execution_state_blob_refs WHERE execution_id = ? "
                "AND reference_kind = 'checkpoint' AND reference_id = ?",
                (paused.execution_id, checkpoint.checkpoint_id),
            )
        }
    assert child_refs <= registered

    async def activate(attempt, _activation):
        return DriverBinding(
            execution_id=attempt.execution_id,
            attempt_id=attempt.attempt_id,
            generation=attempt.generation,
            driver=SimpleNamespace(),
            handle=object(),
        )

    resumed = asyncio.run(control.request_continue(
        command_id="continue-for-gc",
        execution_id=paused.execution_id,
        expected_version=paused.status_version,
        actor={"subject": "agent-owner"},
        activator=activate,
    ))
    active = control.attempts.get(resumed.execution.current_attempt_id)
    assert active is not None
    finished = control.finish_attempt(
        attempt_id=active.attempt_id,
        generation=active.generation,
        expected_execution_version=resumed.execution.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    assert finished.execution.status is ExecutionStatus.COMPLETED
    assert control.state_blobs.gc(execution_id=paused.execution_id) == 0
    loaded = AgentCheckpointV1.load(store, checkpoint)
    assert loaded.payload == state.payload


def test_terminal_gc_deletes_unreferenced_agent_state_blob(tmp_path):
    store, _attempts, _active, execution = _agent_execution(tmp_path)
    orphan = store.put_state_blob(execution.execution_id, b'{"orphan":true}')
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    finished = control.finish_attempt(
        attempt_id=execution.current_attempt_id,
        generation=execution.owner_lease["generation"],
        expected_execution_version=execution.status_version,
        target=ExecutionStatus.COMPLETED,
        outcome="completed",
    )
    assert finished.execution.status is ExecutionStatus.COMPLETED
    assert store.gc_state_blobs(execution.execution_id) == 1
    assert store.get_state_blob(execution.execution_id, orphan["ref"]) is None


@pytest.mark.parametrize("supports_key", [True, False])
def test_provider_effect_classification_requires_declared_key_support(
    tmp_path, supports_key
):
    store, _attempts, active, execution = _admitted_agent_execution(tmp_path)
    from openprogram.agent.production_driver import AgentProductionDriver

    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active,
        SimpleNamespace(user_msg_id="user-anchor"),
        threading.Event(),
    )
    payload = {
        "resolved_snapshot": {"model": {"id": "fake"}, "system_prompt": "system", "tools": []},
        "context": {"messages": []},
        "supports_idempotency_key": supports_key,
    }
    assert hook("provider.before", payload) is False
    with store._connect() as connection:
        row = connection.execute(
            "SELECT classification, idempotency_key, metadata_json FROM effects "
            "WHERE execution_id = ?",
            (execution.execution_id,),
        ).fetchone()
    assert row is not None
    assert row["classification"] == ("idempotent" if supports_key else "nonrepeatable")
    if supports_key:
        assert row["idempotency_key"]
        assert payload["idempotency_key"] == row["idempotency_key"]
    else:
        assert row["idempotency_key"] is None
        assert payload["idempotency_key"] is None


def test_v1_checkpoint_rejects_tampered_state_blob_hash(tmp_path):
    store, _control, _active, _paused, checkpoint, _command = _real_provider_safe_point(tmp_path)
    from openprogram.agent.continuation import AgentCheckpointError, AgentCheckpointV1

    ref = checkpoint.state_refs["agent_checkpoint"]
    with store._transaction() as connection:
        connection.execute(
            "UPDATE execution_state_blobs SET payload = ? WHERE execution_id = ? AND ref = ?",
            (b"{}", checkpoint.execution_id, ref["ref"]),
        )
    with pytest.raises(AgentCheckpointError) as tampered:
        AgentCheckpointV1.load(store, checkpoint)
    assert tampered.value.code == "state_blob_corrupt"


def test_step_commits_one_real_tool_callback_and_consumes_the_permit_atomically(tmp_path):
    store, control, _active, paused, checkpoint, _command = _real_provider_safe_point(tmp_path)
    from openprogram.agent.continuation import AgentCheckpointV1, AgentContinuation
    from openprogram.agent.dispatcher.types import TurnRequest
    from openprogram.agent.production_driver import AgentProductionDriver
    from openprogram.execution.driver import DriverBinding
    from openprogram.providers.types import AssistantMessage

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
    state = AgentCheckpointV1.load(store, checkpoint)
    assistant = AssistantMessage.model_validate(state.read_json_ref(
        store, paused.execution_id, state.payload["assistant_message_delta_ref"],
    ))
    continuation = AgentContinuation(
        request=TurnRequest(
            session_id=paused.session_id, user_text="resume exactly once",
            agent_id="main", source="component", user_msg_id="user-anchor",
        ),
        checkpoint=checkpoint,
        state=state,
        assistant_message=assistant,
        tool_results=(),
        resolved_snapshot=state.read_json_ref(
            store, paused.execution_id,
            state.payload["resolved_model_system_tool_snapshot_ref"],
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
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE execution_id = ?",
            (paused.execution_id,),
        ).fetchone()[0] == 1


def test_chat_ack_pause_before_initial_activation_continues_the_admitted_turn_once(tmp_path):
    from openprogram.agent.production_driver import (
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
    assert provider_starts == [("initial-pause-session", False)]
    with sqlite3.connect(store.path) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM attempts WHERE execution_id = ?",
            (admission.execution_id,),
        ).fetchone()[0] == 1


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
            "actor": {"subject": "spoofed-client"},
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

    collision_ws = FakeWS()
    _run_action(
        "execution.cancel", collision_ws,
        {
            "action": "execution.cancel", "command_id": "cancel-envelope-1",
            "execution_id": execution.execution_id,
            "expected_version": execution.status_version + 1,
        },
    )
    collision = _command_payload(_command_frame(collision_ws))
    assert collision["status"] == CommandStatus.REJECTED.value
    assert collision["rejection_code"] == "idempotency_collision"


@pytest.mark.parametrize("wait_kind", ["question", "approval"])
def test_question_and_approval_wait_stays_pausing_without_checkpoint_or_restart_wait(
    tmp_path, wait_kind
):
    store, _attempts, _active, running = _admitted_agent_execution(tmp_path)
    # P0 deliberately has no durable Question/approval resource.  A restart
    # cannot synthesize an answer or turn the wait into an Agent checkpoint.
    assert store.get_agent_wait(running.execution_id, wait_kind) is None
    assert running.checkpoint_head_id is None


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

    from openprogram.execution.safe_points import AgentSafePointConflict
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
        "actor": {"subject": "spoofed-client"},
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
        ttl_seconds=30,
    )
    active, running = attempts.activate(
        leased.attempt_id,
        generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    from openprogram.execution.effects import EffectClassification

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


def test_question_or_approval_wait_is_excluded_from_p0_restart_contract(public_chat):
    _ws, ack, execution, store, _control = _chat_ack(public_chat, text="ask before continuing")
    assert ack["data"]["execution_id"] == execution.execution_id
    assert execution.status not in {
        ExecutionStatus.PAUSED,
        ExecutionStatus.RECONCILIATION_REQUIRED,
    }
    assert execution.checkpoint_head_id is None
    assert store.list_events(execution.execution_id)
