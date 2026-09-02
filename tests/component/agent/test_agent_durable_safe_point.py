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


@pytest.mark.parametrize(
    ("safe_point", "provider_calls", "tool_calls"),
    [
        ("after_provider", 1, 0),
        ("after_tool", 1, 1),
    ],
)
def test_real_restart_after_provider_or_tool_does_not_repeat_user_finalize_or_dag(
    tmp_path, safe_point, provider_calls, tool_calls
):
    """Restart resumes from the frontier and does not replay committed work."""

    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.agent.production_driver import AgentDriverError, AgentProductionDriver

    calls = {"provider": 0, "tool": 0, "user": 0, "placeholder": 0, "finalize": 0, "dag": 0}

    def turn_runner(*, request, cancel_event, on_safe_point):
        del request, cancel_event
        on_safe_point(
            phase=safe_point,
            provider_call=lambda: calls.__setitem__("provider", calls["provider"] + 1),
            tool_call=lambda: calls.__setitem__("tool", calls["tool"] + 1),
            user_message=lambda: calls.__setitem__("user", calls["user"] + 1),
            placeholder=lambda: calls.__setitem__("placeholder", calls["placeholder"] + 1),
            finalize=lambda: calls.__setitem__("finalize", calls["finalize"] + 1),
            dag_write=lambda: calls.__setitem__("dag", calls["dag"] + 1),
        )

    driver = AgentProductionDriver(store, turn_runner=turn_runner)
    assert set(driver.capabilities().safe_point_kinds) == {
        "agent.provider.decision.after",
        "agent.tool.action.after",
    }
    async def _start_to_safe_point():
        binding = await driver.activate(active, None)
        return await driver.run_until_safe_point(
            binding, safe_point_kind=f"agent.{safe_point.replace('_', '.')}"
        )

    first = asyncio.run(_start_to_safe_point())
    assert first.checkpoint.safe_point["phase"] == safe_point

    reopened = ExecutionStore(store.path)
    reopened_control = RuntimeControlService(
        reopened, AttemptStore(reopened), DriverRegistry()
    )
    restarted = AgentProductionDriver(reopened, control_service=reopened_control, turn_runner=turn_runner)
    continuation = asyncio.run(
        reopened_control.request_continue(
            command_id="continue-after-restart",
            execution_id=running.execution_id,
            expected_version=first.execution.status_version,
            actor={"subject": "agent-owner"},
            driver=restarted,
        )
    )
    assert continuation.delivered is True
    assert continuation.execution.current_attempt_id != active.attempt_id
    assert calls == {
        "provider": provider_calls,
        "tool": tool_calls,
        "user": 1,
        "placeholder": 1,
        "finalize": 1,
        "dag": 1,
    }


def test_continue_reopens_store_binds_new_production_driver_with_checkpoint_fence(tmp_path):
    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.agent.production_driver import AgentDriverError, AgentProductionDriver
    from openprogram.execution import ActivationInput

    def turn_runner(*, on_safe_point, **_):
        on_safe_point(phase="after_provider")

    driver = AgentProductionDriver(store, turn_runner=turn_runner)
    async def _publish():
        binding = await driver.activate(active, None)
        return await driver.publish_safe_point(
            binding, safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider-1", "phase": "after_provider"},),
        )

    checkpoint = asyncio.run(_publish())
    reopened = ExecutionStore(store.path)
    control = RuntimeControlService(reopened, AttemptStore(reopened), DriverRegistry())
    seen: list[ActivationInput] = []
    restarted = AgentProductionDriver(
        reopened,
        control_service=control,
        turn_runner=lambda **_: {"ok": True},
        activation_observer=seen.append,
    )
    result = asyncio.run(
        control.request_continue(
            command_id="continue-with-fenced-driver",
            execution_id=running.execution_id,
            expected_version=checkpoint.execution.status_version,
            actor={"subject": "agent-owner"},
            driver=restarted,
        )
    )
    assert result.delivered is True
    assert seen and seen[0].checkpoint.checkpoint_id == checkpoint.checkpoint.checkpoint_id
    assert seen[0].checkpoint.execution_id == running.execution_id
    assert seen[0].checkpoint.created_by_attempt_id == active.attempt_id
    assert result.execution.owner_lease["generation"] > running.owner_lease["generation"]

    with pytest.raises(AgentDriverError) as stale:
        asyncio.run(
            restarted.activate(
                active,
                ActivationInput(checkpoint=checkpoint.checkpoint),
            )
        )
    assert getattr(stale.value, "code", None) == "stale_attempt"


def test_two_tool_step_duplicate_command_consumes_exactly_one_permit(tmp_path):
    store, _attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.execution.safe_points import AgentSafePointConflict
    from openprogram.agent.production_driver import AgentProductionDriver

    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    def turn_runner(*, on_safe_point, **_):
        on_safe_point(phase="after_provider")

    driver = AgentProductionDriver(store, control_service=control, turn_runner=turn_runner)
    async def _pause():
        binding = await driver.activate(active, None)
        return await driver.publish_safe_point(
            binding, safe_point_kind="agent.provider.decision.after",
            frontier=({"step_id": "provider-1", "phase": "after_provider"},),
        )

    paused = asyncio.run(_pause()).execution
    first = asyncio.run(
        control.request_step(
            command_id="step-tool-once",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"subject": "agent-owner"}, driver=driver,
        )
    )
    duplicate = asyncio.run(
        control.request_step(
            command_id="step-tool-once",
            execution_id=paused.execution_id,
            expected_version=paused.status_version,
            actor={"subject": "agent-owner"}, driver=driver,
        )
    )
    assert duplicate.command == first.command
    assert duplicate.execution.current_attempt_id == first.execution.current_attempt_id
    control.consume_agent_step_permit(
        execution_id=paused.execution_id,
        command_id="step-tool-once",
        action_id="tool-action-1",
    )
    with pytest.raises(AgentSafePointConflict) as second_permit:
        control.consume_agent_step_permit(
            execution_id=paused.execution_id,
            command_id="step-tool-once",
            action_id="tool-action-2",
        )
    assert second_permit.value.code == "step_permit_consumed"


@pytest.mark.parametrize("wait_kind", ["question", "approval"])
def test_question_and_approval_wait_stays_pausing_without_checkpoint_or_restart_wait(
    tmp_path, wait_kind
):
    store, attempts, active, running = _admitted_agent_execution(tmp_path)
    from openprogram.agent.production_driver import AgentProductionDriver

    def turn_runner(*, on_safe_point, **_):
        on_safe_point(phase="waiting")

    driver = AgentProductionDriver(store, control_service=RuntimeControlService(store, attempts, DriverRegistry()), turn_runner=turn_runner)
    async def _enter_wait():
        binding = await driver.activate(active, None)
        return binding, await driver.enter_wait(binding, kind=wait_kind, request_id=f"{wait_kind}-request-1")

    binding, waiting = asyncio.run(_enter_wait())
    assert waiting.execution.status is ExecutionStatus.RUNNING
    paused = asyncio.run(
        driver.request_pause_at_wait(
            binding,
            command_id=f"pause-{wait_kind}",
        )
    )
    assert paused.status is ExecutionStatus.PAUSING
    assert paused.checkpoint_head_id is None
    reopened = ExecutionStore(store.path)
    assert reopened.get_execution(running.execution_id).checkpoint_head_id is None
    assert reopened.get_agent_wait(running.execution_id, wait_kind) is None


def test_stale_continue_and_step_return_latest_snapshot_without_mutating_newer_state(public_chat):
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
    assert command["rejection_code"] == "stale_version"
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


def test_chat_ack_execution_id_drives_provider_pause_and_checkpoint(public_chat):
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
    assert current.safe_point["kind"] == "agent.provider.decision.after"
    assert current.status is ExecutionStatus.PAUSED
    assert current.checkpoint_head_id is not None
    checkpoint = control.checkpoints.get(current.checkpoint_head_id)
    assert checkpoint is not None
    assert checkpoint.frontier[-1]["phase"] == "after_provider"


def test_two_tool_actions_resume_from_checkpoint_with_a_new_activation_owner(public_chat):
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
    assert command["status"] in {"accepted", "applied"}
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.revision_id == execution.revision_id
    assert current.current_attempt_id != execution.current_attempt_id
    assert current.safe_point["phase"] in {"after_provider", "after_tool"}


def test_step_consumes_one_managed_action_and_returns_to_paused(public_chat):
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
    assert command["status"] in {"accepted", "applied"}
    current = store.get_execution(execution.execution_id)
    assert current is not None
    assert current.status is ExecutionStatus.PAUSED
    assert current.safe_point["phase"] in {"after_provider", "after_tool"}
    assert current.status_version > execution.status_version
    applied_actions = [
        event.payload.get("action_id")
        for event in store.list_events(execution.execution_id)
        if event.kind == "agent.action.completed"
    ]
    assert len(applied_actions) == 1


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


def test_checkpoint_retains_branch_anchors_current_decision_and_pause_sentinel(public_chat):
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
    assert current is not None and current.checkpoint_head_id is not None
    checkpoint = control.checkpoints.get(current.checkpoint_head_id)
    assert checkpoint is not None
    payload = checkpoint.to_dict()
    assert payload["turn"]["base_history_head_id"]
    assert payload["turn"]["user_message_id"]
    assert payload["current_decision"]["provider_action_id"]
    assert payload["current_decision"]["tool_call_ids"] == sorted(
        payload["current_decision"]["tool_call_ids"]
    )
    assert payload["safe_point"]["sentinel"] == "resume-from-checkpoint"


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
