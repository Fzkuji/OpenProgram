"""Agents of one conversation take turns on an application: a call holds it until it returns."""
from __future__ import annotations

import threading

import pytest

from openprogram.programs.tools.computer_use.computer_use_agent.desktop import base
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app as app_target


class _Desk:
    """An application backend that records what reached it; agent "a" stays in a step until released."""

    def __init__(self):
        self.log = []
        self.entered = threading.Event()
        self.release = threading.Event()

    def act(self, session, action, step):
        self.log.append((session, action, step.get("element_index")))
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
def desk(monkeypatch):
    computer = _Desk()
    monkeypatch.setattr("openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends.backend",
                        lambda: computer)
    monkeypatch.setattr(base, "check_cancelled", lambda: None)
    for name in ("_locks", "_acted", "_seen"):
        monkeypatch.setattr(app_target, name, {})
    return computer


def _start(desk, steps):
    thread = threading.Thread(target=app_target.run, args=(steps, {"app": "Notes"}, "a"))
    thread.start()
    assert desk.entered.wait(10)
    return thread


def test_a_list_of_steps_is_not_interleaved_with_another_agents_call(desk):
    first = _start(desk, [("click", {"element_index": 1}), ("click", {"element_index": 2})])
    second = threading.Thread(target=app_target.run, args=([("click", {"element_index": 9})], {"app": "Notes"}, "b"))
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
    monkeypatch.setattr(app_target, "APP_WAIT_SECONDS", 0.5)
    first = _start(desk, [("click", {"element_index": 1})])
    try:
        result = app_target.run([("click", {"element_index": 9})], {"app": "Notes"}, "b")
    finally:
        desk.release.set()
        first.join(10)
    assert result.is_error and "another agent has been operating Notes" in result.text
    assert result.json_data == {"action_dispatched": False}
    assert ("b", "click", 9) not in desk.log


def test_a_stopped_turn_stops_waiting_for_the_application(desk, monkeypatch):
    class Stopped(BaseException):
        pass

    def stop():
        raise Stopped()

    first = _start(desk, [("click", {"element_index": 1})])
    try:
        monkeypatch.setattr(base, "check_cancelled", stop)
        with pytest.raises(Stopped):
            app_target.run([("click", {"element_index": 9})], {"app": "Notes"}, "b")
    finally:
        desk.release.set()
        first.join(10)
    # The application is free again for the next call.
    monkeypatch.setattr(base, "check_cancelled", lambda: None)
    assert not app_target.run([("click", {"element_index": 3})], {"app": "Notes"}, "b").is_error


def test_batch_can_end_with_a_tree_observation(desk):
    result = app_target.run(
        [("click", {"element_index": 1}), ("get_app_state", {"include": "tree"})],
        {"app": "Notes"}, "b",
    )
    assert not result.is_error
    assert desk.log == [("b", "click", 1), ("b", "state", None)]
    assert "tree" in result.text
