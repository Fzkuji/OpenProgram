"""A steer taken back before its safe point never reaches the turn."""

from __future__ import annotations

from openprogram.runtime.execution.model import CommandStatus, ExecutionStatus
from ._support import AttemptStore, _admitted, threading


def _running_turn(tmp_path, execution_id):
    from openprogram.models.providers.types import Model
    from openprogram.runtime.agent.turn_request import TurnRequest
    from openprogram.runtime.conversation.checkpoint import runtime_contract_snapshot
    from openprogram.runtime.conversation.production_driver import AgentProductionDriver
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry

    store, execution = _admitted(tmp_path, execution_id=execution_id)
    attempts = AttemptStore(store)
    leased, reserved = attempts.lease(
        execution.execution_id, expected_version=execution.status_version,
        owner_id="owner-steer",
    )
    active, running = attempts.activate(
        leased.attempt_id, generation=leased.generation,
        expected_execution_version=reserved.status_version,
    )
    control = RuntimeControlService(store, attempts, DriverRegistry())
    for name in ("first", "second"):
        control.request_steer(
            command_id=f"steer-{name}", execution_id=execution.execution_id,
            expected_version=running.status_version, actor={"surface": "test"},
            payload={"message": f"{name} instruction"},
        )
    queue: list[dict] = []
    request = TurnRequest(
        session_id=running.session_id, user_text="durable agent turn",
        agent_id="default", source="component", user_msg_id="user-anchor",
    )
    request._execution_revision_id = running.revision_id
    hook = AgentProductionDriver(store, control_service=control)._safe_point_hook(
        active, request, threading.Event(), steer_queue=queue, steer_consumed_ids=set(),
    )
    snapshot = runtime_contract_snapshot(
        model=Model(id="fake", name="fake", api="openai-completions", provider="openai",
                    base_url="https://example.invalid/v1"),
        system_prompt="system", tools=[], request=request,
    )

    def provider_step(request_id):
        assert hook("provider.before", {
            "resolved_snapshot": snapshot, "context": {"messages": [request_id]},
            "supports_idempotency_key": True,
        }) is False
        assert hook("provider.after", {
            "message": {"role": "assistant", "content": [], "api": "fake",
                        "provider": "fake", "model": "fake"},
            "provider_request_id": request_id, "usage": {},
        }) is False

    return store, execution, active, queue, provider_step


def test_withdrawn_steer_is_skipped_by_the_next_safe_point(tmp_path):
    store, execution, active, queue, provider_step = _running_turn(tmp_path, "exec-withdraw")

    command, withdrawn = store.withdraw_steer("steer-first")
    provider_step("request-1")

    assert withdrawn is True
    assert command.status is CommandStatus.REJECTED and command.rejection_code == "withdrawn"
    assert [item["command_id"] for item in queue] == ["steer-second"]
    assert store.get_command("steer-first").status is CommandStatus.REJECTED
    assert store.get_command("steer-second").status is CommandStatus.APPLYING
    # The steer the safe point took can no longer be taken back.
    assert store.withdraw_steer("steer-second")[1] is False
    current = store.get_execution(execution.execution_id)
    assert current.status is ExecutionStatus.RUNNING
    assert current.current_attempt_id == active.attempt_id


def test_steer_withdrawn_after_the_driver_chose_it_does_not_fail_the_safe_point(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.production_driver import safe_points

    store, execution, active, queue, provider_step = _running_turn(tmp_path, "exec-withdraw-race")
    hook_class = next(
        value for value in vars(safe_points).values()
        if isinstance(value, type) and "_current_command" in vars(value)
    )
    chosen = hook_class._current_command
    taken_back: list[str] = []

    def choose_then_withdraw(service, execution_id):
        command = chosen(service, execution_id)
        if command is not None and command.command_id == "steer-first" and not taken_back:
            taken_back.append(command.command_id)
            assert store.withdraw_steer(command.command_id)[1] is True
        return command

    monkeypatch.setattr(hook_class, "_current_command", staticmethod(choose_then_withdraw))
    provider_step("request-1")

    assert taken_back == ["steer-first"]
    # The commit served the steer still open instead of the one taken back.
    assert [item["command_id"] for item in queue] == ["steer-second"]
    assert store.get_command("steer-first").rejection_code == "withdrawn"
    assert store.get_command("steer-second").status is CommandStatus.APPLYING
    assert store.get_execution(execution.execution_id).status is ExecutionStatus.RUNNING


def test_the_only_steer_withdrawn_at_the_boundary_is_ignored(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.production_driver import safe_points

    store, execution, active, queue, provider_step = _running_turn(tmp_path, "exec-withdraw-sole")
    assert store.withdraw_steer("steer-second")[1] is True
    hook_class = next(
        value for value in vars(safe_points).values()
        if isinstance(value, type) and "_current_command" in vars(value)
    )
    chosen = hook_class._current_command

    def choose_then_withdraw(service, execution_id):
        command = chosen(service, execution_id)
        if command is not None and command.status is CommandStatus.ACCEPTED:
            store.withdraw_steer(command.command_id)
        return command

    monkeypatch.setattr(hook_class, "_current_command", staticmethod(choose_then_withdraw))
    provider_step("request-1")

    assert queue == []
    assert {store.get_command(f"steer-{name}").rejection_code for name in ("first", "second")} == {"withdrawn"}
    current = store.get_execution(execution.execution_id)
    assert current.status is ExecutionStatus.RUNNING
