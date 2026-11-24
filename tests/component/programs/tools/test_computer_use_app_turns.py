"""Agents of one conversation take turns on an application: a call holds it until it returns."""
from __future__ import annotations

import functools
import threading

import pytest

from openprogram.host import machines
from openprogram.programs.tools.computer_use.computer_use import locks
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.desktop import base
from openprogram.programs.tools.computer_use.computer_use.dispatch import run_steps
from openprogram.programs.tools.computer_use.computer_use.providers import desktop


class _Desk:
    """An application backend that records what reached it; agent "a" stays in a step until released."""

    def __init__(self):
        self.log = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def act(self, session, action, step):
        self.log.append((session, action, step.get("element")))
        if session == "a":
            self.entered.set()
            self.release.wait(10)
        return f"did {action}"

    def settle(self, session, app):
        pass

    def get_state(self, session, app, **kwargs):
        self.log.append((session, "state", None))
        return base.Observation(text="tree", image=None, app=app)


@pytest.fixture
def desk(monkeypatch, tmp_path):
    computer = _Desk()
    monkeypatch.setattr("openprogram.programs.tools.computer_use.computer_use.desktop.backends.backend",
                        lambda: computer)
    for name in ("_acted", "_seen"):
        monkeypatch.setattr(desktop, name, {})
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")
    return computer


def _run(steps, agent, cancelled=None):
    """One call of this computer's app target on Notes by ``agent``, once the tool has checked it."""
    provider = desktop.AppProvider(machines.local_machine({}), "s", system="Darwin")
    return run_steps(provider, {"app": "Notes"}, steps, Call(conversation="s", agent=agent,
                                                             check_cancelled=cancelled))


def _start(desk, steps):
    thread = threading.Thread(target=_run, args=(steps, "a"))
    thread.start()
    assert desk.entered.wait(10)
    return thread


def test_a_list_of_steps_is_not_interleaved_with_another_agents_call(desk):
    first = _start(desk, [("click", {"element": 1}), ("click", {"element": 2})])
    second = threading.Thread(target=_run, args=([("click", {"element": 9})], "b"))
    second.start()
    second.join(0.6)
    # The other agent's click waits while the list is running.
    assert second.is_alive() and desk.log == [("a", "click", 1)]
    desk.release.set()
    first.join(10)
    second.join(10)
    assert desk.log == [("a", "click", 1), ("a", "click", 2), ("a", "state", None),
                        ("b", "click", 9), ("b", "state", None)]


def test_a_call_that_waits_too_long_for_another_agent_sends_nothing(desk, monkeypatch):
    monkeypatch.setattr(locks, "held", functools.partial(locks.held, seconds=0.5))
    first = _start(desk, [("click", {"element": 1})])
    try:
        result = _run([("click", {"element": 9})], "b")
    finally:
        desk.release.set()
        first.join(10)
    assert result.is_error and result.text.startswith("SCREEN_BUSY: another agent has been operating type=\"window\" for ")
    assert "nothing was sent" in result.text
    assert result.json_data == {"action_dispatched": False}
    assert ("b", "click", 9) not in desk.log


def test_a_stopped_turn_stops_waiting_for_the_application(desk):
    class Stopped(BaseException):
        pass

    def stop():
        raise Stopped()

    first = _start(desk, [("click", {"element": 1})])
    try:
        with pytest.raises(Stopped):
            _run([("click", {"element": 9})], "b", cancelled=stop)
    finally:
        desk.release.set()
        first.join(10)
    # The application is free again for the next call.
    assert not _run([("click", {"element": 3})], "b").is_error


def test_batch_can_end_with_a_tree_observation(desk):
    result = _run([("click", {"element": 1}), ("get_state", {"include": "tree"})], "b")
    assert not result.is_error
    assert desk.log == [("b", "click", 1), ("b", "state", None)]
    assert "tree" in result.text
