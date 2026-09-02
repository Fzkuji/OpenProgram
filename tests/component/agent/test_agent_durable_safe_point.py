"""Public Agent safe-point contracts.

These tests deliberately enter through the WebSocket chat handler.  Control
commands must use the execution id returned by ``chat_ack`` and then pass the
same command envelope through the runtime action registry.  They must not
reach the control service directly from a transport test.
"""

from __future__ import annotations

import asyncio
import json
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
