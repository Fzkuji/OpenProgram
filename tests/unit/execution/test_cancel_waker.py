"""A blocking host wait registered with ``cancellation.on_cancel`` is woken
by the stop of its tool call, its turn, or the Worker stopping."""
from __future__ import annotations

import threading

from openprogram.host import cancellation
from openprogram.host._platform import changes
from openprogram.runtime.conversation import run_control
from openprogram.runtime.execution.run_control import binding, wakeups
from openprogram.runtime.execution.run_control.tokens import TURN_ENDED_TOPIC, CancellationToken


class _ToolSignal:
    """The shape of a tool's deadline wrapper: its own event and its parent."""

    def __init__(self, parent):
        self.parent = parent


def test_the_tool_signal_its_parent_and_the_turn_token_each_wake_the_wait(monkeypatch):
    parent = threading.Event()
    tool = _ToolSignal(parent)
    token = CancellationToken("conversation", "execution")
    woken = []
    reset_cancel = cancellation._current_cancel.set(tool)
    reset_token = binding._current_token.set(token)
    try:
        end = cancellation.on_cancel(lambda: woken.append(1))
    finally:
        cancellation._current_cancel.reset(reset_cancel)
        binding._current_token.reset(reset_token)
    wakeups.fire(tool)
    wakeups.set_and_fire(parent)
    token.cancel()
    assert woken == [1, 1, 1]
    end()
    assert wakeups.registered(parent) == 0 and wakeups.registered(token.event) == 0
    token2 = CancellationToken("conversation", "execution")
    token2.cancel()
    assert woken == [1, 1, 1]


def test_only_a_stopping_worker_wakes_the_wait_through_turn_ended(monkeypatch):
    woken = []
    end = cancellation.on_cancel(lambda: woken.append(1))
    try:
        changes.signal(TURN_ENDED_TOPIC, "conversation")
        assert woken == []
        monkeypatch.setattr(run_control._worker_stopping, "is_set", lambda: True)
        changes.signal(TURN_ENDED_TOPIC)
        assert woken == [1]
    finally:
        end()
