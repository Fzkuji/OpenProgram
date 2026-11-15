"""A channel turn interrupted mid-run continues from its checkpoint.

Channel transports (WeChat, Telegram, ...), the scheduler and spawned
agents admit a turn without a message id of their own. The turn runtime
minted one only when the turn ran, so the immutable input kept no message
anchors, and every continuation of such a turn failed the anchor fence in
``AgentProductionDriver.activate`` with ``checkpoint_schema_invalid``: the
worker-restart continue ended ``activation_failed`` and the reply was lost.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openprogram.runtime.agent.turn_request import TurnRequest
from openprogram.runtime.execution import AttemptStore, ExecutionStore
from openprogram.runtime.execution.control import RuntimeControlService
from openprogram.runtime.execution.driver import ActivationInput, DriverRegistry


class _ReachedRuntime(Exception):
    """The activation passed the anchor fence and resolved the runtime."""


def _channel_request(session_id: str) -> TurnRequest:
    return TurnRequest(
        session_id=session_id, user_text="build 1.0.1", agent_id="main",
        source="wechat", peer_id="peer@im.wechat", peer_display="peer",
    )


def _adapter(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.production_driver import CanonicalAgentAdapter

    store = ExecutionStore(tmp_path / "executions.sqlite3")
    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_control_service", lambda: control)
    return store, control, CanonicalAgentAdapter(store=store)


def test_channel_admission_records_the_anchors_the_runtime_uses(tmp_path, monkeypatch):
    store, _control, adapter = _adapter(tmp_path, monkeypatch)
    request = _channel_request("channel-anchors")
    admission = adapter.admit(
        request, trusted_actor={"subject": "owner"},
        user_message_id=request.user_msg_id, config_snapshot_ref="channel:wechat",
    )
    record = store.get_execution_input(admission.execution_id)
    payload = store.get_agent_turn_input(admission.execution_id)
    assert request.user_msg_id
    # The frozen request carries the id, so the runtime persists the user
    # node and its "<id>_reply" placeholder under exactly these anchors.
    assert payload["request"]["user_msg_id"] == request.user_msg_id
    assert record.user_message_id == request.user_msg_id
    assert record.assistant_message_id == f"{request.user_msg_id}_reply"


def test_transport_anchors_are_kept(tmp_path, monkeypatch):
    store, _control, adapter = _adapter(tmp_path, monkeypatch)
    request = _channel_request("web-anchors")
    request.user_msg_id = "web-msg"
    admission = adapter.admit(
        request, trusted_actor={"subject": "owner"}, user_message_id="web-msg",
        assistant_message_id="web-msg_reply", config_snapshot_ref="session:web",
    )
    record = store.get_execution_input(admission.execution_id)
    assert (record.user_message_id, record.assistant_message_id) == ("web-msg", "web-msg_reply")


def _activate_continuation(store, control, execution_id, request, monkeypatch):
    """Run ``AgentProductionDriver.activate`` for a resumed attempt up to
    runtime resolution, with a checkpoint written by the interrupted turn."""
    from openprogram.runtime.conversation import checkpoint as checkpoint_module
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.conversation.turn_runtime import loop_runner

    turn_user = request.user_msg_id or "minted-at-run"
    continuation = SimpleNamespace(
        user_message_id=turn_user,
        assistant_message_id=f"{turn_user}_reply",
        resolved_snapshot={},
    )
    monkeypatch.setattr(checkpoint_module.AgentContinuation, "from_checkpoint",
                        classmethod(lambda cls, **kw: continuation))
    monkeypatch.setattr("openprogram.state.store.SessionNodeWriter", lambda *a, **kw: SimpleNamespace(
        load=lambda: SimpleNamespace(nodes={})))

    def reached(*args, **kwargs):
        raise _ReachedRuntime()
    monkeypatch.setattr(loop_runner, "resolve_agent_runtime", reached)
    monkeypatch.setattr("openprogram.runtime.conversation.workdir.runtime_location_for",
                        lambda *a, **kw: {"workdir": None})

    execution = store.get_execution(execution_id)
    attempt, leased = control.attempts.lease(
        execution_id, expected_version=execution.status_version,
        owner_id="control-service",
    )
    active, _running = control.attempts.activate(
        attempt.attempt_id, generation=attempt.generation,
        expected_execution_version=leased.status_version,
    )
    driver = AgentProductionDriver(store, control_service=control)
    checkpoint = SimpleNamespace(execution_id=execution_id, state_refs={})
    return asyncio.run(driver.activate(active, ActivationInput(checkpoint=checkpoint, steer_inputs=())))


def test_resumed_channel_turn_passes_the_anchor_fence(tmp_path, monkeypatch):
    store, control, adapter = _adapter(tmp_path, monkeypatch)
    request = _channel_request("channel-resume")
    admission = adapter.admit(
        request, trusted_actor={"subject": "owner"},
        user_message_id=request.user_msg_id, config_snapshot_ref="channel:wechat",
    )
    with pytest.raises(_ReachedRuntime):
        _activate_continuation(store, control, admission.execution_id, request, monkeypatch)


def test_input_without_anchors_still_fails_the_fence(tmp_path, monkeypatch):
    """The incident's input shape: the fence stays strict for it."""
    from openprogram.runtime.conversation.production_driver import shared

    store, control, adapter = _adapter(tmp_path, monkeypatch)
    request = _channel_request("legacy-channel")
    admission = adapter.entry.admit(
        session_id=request.session_id, turn_payload=adapter.payload_for(request),
        trusted_actor={"subject": "owner"}, user_message_id=None,
        assistant_message_id=None, config_snapshot_ref="channel:wechat",
    )
    with pytest.raises(shared.AgentDriverError) as error:
        _activate_continuation(store, control, admission.execution_id, request, monkeypatch)
    assert error.value.code == "checkpoint_schema_invalid"


@pytest.mark.parametrize(("source", "resumed", "expected"), [
    ("wechat", False, False),   # first attempt: the inbound dispatcher replies
    ("wechat", True, True),     # resumed attempt: nobody else is waiting
    ("web", False, True),
    ("agent_spawn", True, False),
])
def test_bound_reply_sends_resumed_channel_turns(source, resumed, expected):
    from openprogram.runtime.conversation.bound_reply import delivered_here

    assert delivered_here(source, "wechat", resumed=resumed) is expected
