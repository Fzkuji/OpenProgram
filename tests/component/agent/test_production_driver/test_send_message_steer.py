"""A message from a helper's spawner reaches the running helper as a steer.

send_message to a helper the sending turn started stores the message as a
durable steer command on the helper's execution (``send_message/steer.py``),
the path the person's "Add instruction" takes; the helper's next tool
boundary (a safe point) takes it. Until then the inbox entry written beside
it is the fallback, and ``steer.taken`` tells drain whether to drop it.

Design: docs/reference/design/runtime/collaboration/agent-collaboration.html.
"""

from __future__ import annotations

from types import SimpleNamespace

from openprogram.runtime.execution.model import CommandStatus

from .test_steer_withdraw import _running_turn


def _route_to(monkeypatch, store, tmp_path):
    from openprogram.runtime.execution.control import RuntimeControlService
    from openprogram.runtime.execution.driver import DriverRegistry
    from ._support import AttemptStore

    control = RuntimeControlService(store, AttemptStore(store), DriverRegistry())
    monkeypatch.setattr("openprogram.runtime.execution.default_store", lambda: store)
    monkeypatch.setattr("openprogram.runtime.execution.hooks.job_runner",
                        lambda _store: SimpleNamespace(_execution_control=control))


def test_the_spawners_message_lands_at_the_helpers_next_tool_boundary(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.peer_agents import steer

    store, execution, _active, queue, provider_step = _running_turn(tmp_path, "exec-helper")
    _route_to(monkeypatch, store, tmp_path)

    command_id = steer.submit(
        execution.execution_id, "[from parent] also check the tests",
        sender_session_id="parent", sender_name="Assistant", authority={},
    )
    assert command_id is not None
    command = store.get_command(command_id)
    # Stored as agent traffic, never as the person speaking.
    assert command.actor["surface"] == "agent" and command.actor["sender_session_id"] == "parent"
    assert command.payload["origin"] == "agent"
    assert steer.taken(command_id) is False  # the fallback entry would still deliver it

    provider_step("request-1")

    taken = [item for item in queue if item["command_id"] == command_id]
    assert taken and taken[0]["payload"]["message"] == "[from parent] also check the tests"
    assert store.get_command(command_id).status is CommandStatus.APPLYING
    assert steer.taken(command_id) is True  # drain drops the fallback entry


def test_an_ended_helper_takes_no_steer_and_the_message_queues(tmp_path, monkeypatch):
    from openprogram.runtime.conversation.peer_agents import steer

    store, execution, _active, _queue, _step = _running_turn(tmp_path, "exec-helper-ended")
    _route_to(monkeypatch, store, tmp_path)
    monkeypatch.setattr("openprogram.runtime.execution.model.TERMINAL_EXECUTION_STATUSES",
                        frozenset({store.get_execution(execution.execution_id).status}))
    assert steer.submit(execution.execution_id, "too late", sender_session_id="parent",
                        sender_name="Assistant", authority={}) is None
