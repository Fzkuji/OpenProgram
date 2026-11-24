"""Ending what computer use keeps, and machines that are switched off or are this computer.

A provider is closed (``Provider.close``) once per agent that operated it:
when a run that was an agent of its own ends, when its conversation is
deleted, and when its machine is switched off or removed. A target of a
machine that is off is not listed, so a call to it answers UNKNOWN_TARGET.
An OSWorld server on a loopback address whose screenshot is this
computer's own display is refused when the machine is saved.
"""
from __future__ import annotations

import logging
import time
from types import SimpleNamespace

import pytest

from openprogram.host import machines
from openprogram.host.machines import Connection, Machine
from openprogram.host.machines.kinds import KindError
from openprogram.programs.tools.computer_use.computer_use import code as code_mode
from openprogram.programs.tools.computer_use.computer_use import errors, locks, registry, tool
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.providers import local_screen, osworld
from openprogram.programs.tools.computer_use.computer_use.tool import computer_use

from tests.support.screen_targets import PNG, FakeProvider, environment, install_machines, local, machine


class Closing(FakeProvider):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.closed: list[tuple[str, str]] = []

    def close(self, call: Call) -> None:
        self.closed.append((call.conversation, call.agent))


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(registry, "_operated", {})
    monkeypatch.setattr(registry, "_running", {})
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")
    monkeypatch.setattr(tool, "_code_available", lambda: False)
    environment(monkeypatch, local())


@pytest.fixture
def fleet(monkeypatch):
    app = Closing("window")
    lab = Closing("screen", "lab")
    listed = [local(window="fake"), machine("lab", screen="fake")]
    install_machines(monkeypatch, listed, {"local": [app], "lab": [lab]})
    return SimpleNamespace(app=app, lab=lab, listed=listed)


def _operate(provider, conversation, agent):
    with registry.operating(provider, Call(conversation=conversation, agent=agent)):
        pass


def test_closing_a_machine_closes_each_agent_that_operated_it_once(fleet):
    _operate(fleet.lab, "c1", "c1")
    _operate(fleet.lab, "c1", "c1")
    _operate(fleet.lab, "c2", "c2\njob")
    _operate(fleet.app, "c1", "c1")
    assert registry.release(machine="lab") == 2
    assert sorted(fleet.lab.closed) == [("c1", "c1"), ("c2", "c2\njob")]
    assert fleet.app.closed == []
    # What was closed is forgotten: closing again closes nothing.
    assert registry.release(machine="lab") == 0


def test_closing_a_conversation_closes_its_agents_on_every_machine(fleet):
    _operate(fleet.lab, "c1", "c1")
    _operate(fleet.app, "c1", "c1\njob")
    _operate(fleet.app, "c2", "c2")
    registry.release(conversation="c1")
    assert fleet.lab.closed == [("c1", "c1")] and fleet.app.closed == [("c1", "c1\njob")]


def test_a_removed_machine_is_closed_from_what_was_operated(fleet, monkeypatch):
    _operate(fleet.lab, "c1", "c1")
    install_machines(monkeypatch, [local(window="fake")], {"local": [fleet.app]})
    registry.release(machine="lab")
    assert fleet.lab.closed == [("c1", "c1")]


def test_a_provider_that_fails_to_close_leaves_the_others(fleet, monkeypatch):
    def broken(call):
        raise RuntimeError("gone")

    monkeypatch.setattr(fleet.app, "close", broken)
    _operate(fleet.app, "c1", "c1")
    _operate(fleet.lab, "c1", "c1")
    assert registry.release(conversation="c1") == 2
    assert fleet.lab.closed == [("c1", "c1")]


def test_a_call_records_its_target_and_counts_while_it_runs(fleet, monkeypatch):
    seen = {}

    def act(subject, steps, call):
        seen["running"] = dict(registry._running)
        return tool.ToolReturn(text="Done")

    monkeypatch.setattr(tool, "_session_id", lambda: "c1")
    monkeypatch.setattr(fleet.lab, "_act", act)
    computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 2})
    assert seen["running"] == {("lab", "c1", "c1"): 1}
    assert registry._running == {}
    assert list(registry._operated) == [("lab", "lab screen", "c1", "c1")]


def test_an_action_call_ends_within_the_time_a_release_waits(fleet, monkeypatch):
    seen = {}

    def act(subject, steps, call):
        seen["left"] = call.deadline - time.monotonic()
        return tool.ToolReturn(text="Done")

    monkeypatch.setattr(fleet.lab, "_act", act)
    computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 2})
    assert registry.FINISH_SECONDS - 5 < seen["left"] <= registry.FINISH_SECONDS


def test_a_call_whose_machine_was_switched_off_after_it_resolved_sends_nothing(fleet, monkeypatch):
    install_machines(monkeypatch, [local(window="fake"), machine("lab", screen="fake", enabled=False)])
    with pytest.raises(errors.MachineUnreachable, match="switched off or removed; nothing was sent"):
        _operate(fleet.lab, "c1", "c1")
    assert registry._running == {} and registry._operated == {}
    # Through the tool: the target resolved while the machine was on.
    monkeypatch.setattr(registry, "resolve", lambda call, conversation="": fleet.lab)
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 2})
    assert result.is_error and result.text.startswith('[device="lab", type="screen"]\nMACHINE_UNREACHABLE: ')
    assert result.json_data == {"action_dispatched": False}
    assert fleet.lab.calls == [] and registry._operated == {}


def test_a_removed_machine_refuses_a_call_that_resolved_before(fleet, monkeypatch):
    install_machines(monkeypatch, [local(window="fake")])
    with pytest.raises(errors.MachineUnreachable):
        _operate(fleet.lab, "c1", "c1")


def test_ending_one_agent_does_not_wait_for_another_agents_call(fleet, caplog):
    registry._running[("lab", "c1", "c1")] = 1
    began = time.monotonic()
    with caplog.at_level(logging.WARNING, logger=registry.__name__):
        registry.release(conversation="c1", agent="c1\njob", wait=5)
    assert time.monotonic() - began < 1
    assert "still run" not in caplog.text


def test_a_switched_off_machine_answers_unknown_target_with_the_targets_that_exist(monkeypatch):
    install_machines(monkeypatch, [local(window="fake"), machine("lab", screen="fake", enabled=False)],
                     {"local": [FakeProvider("window")], "lab": [FakeProvider("screen", "lab")]})
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 1})
    assert result.is_error
    assert result.text == "UNKNOWN_TARGET: nothing on device 'lab' can be operated now. Targets now: type=\"window\"."


def test_switching_off_or_removing_a_machine_reaches_the_providers(fleet, monkeypatch):
    assert tool._released in machines.releasers
    _operate(fleet.lab, "c1", "c1")
    # Still set up and on: a removal asked before it came back is stale, so nothing is closed.
    machines.remove_instances(machine="lab")
    assert fleet.lab.closed == []
    install_machines(monkeypatch, [local(window="fake"), machine("lab", screen="fake", enabled=False)],
                     {"local": [fleet.app], "lab": [fleet.lab]})
    machines.remove_instances(machine="lab")
    assert fleet.lab.closed == [("c1", "c1")]


def test_deleting_a_conversation_stops_its_interpreters_and_closes_its_providers(fleet, monkeypatch):
    stopped: list[str] = []
    monkeypatch.setattr(code_mode, "close_conversation", stopped.append)
    _operate(fleet.app, "c1", "c1")
    machines.remove_instances(conversation="c1")
    assert fleet.app.closed == [("c1", "c1")] and stopped == ["c1"]


def test_a_run_that_ends_closes_its_interpreter_and_what_its_agent_kept(fleet, monkeypatch):
    stopped: list[str] = []
    monkeypatch.setattr(code_mode, "close", stopped.append)
    _operate(fleet.app, "c1", "c1")
    _operate(fleet.app, "c1", "c1\nrun-7")
    tool.run_ended("c1", "run-7")
    assert stopped == [code_mode.run_slot("c1", "run-7")]
    # The conversation's own turns share one agent, which ends with the conversation.
    assert fleet.app.closed == [("c1", "c1\nrun-7")]


def test_interpreters_of_one_conversation_stop_together(monkeypatch):
    class Runner:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    runners = {code_mode.run_slot("c1", "a"): (None, None, Runner()),
               code_mode.run_slot("c1", "b"): (None, None, Runner()),
               code_mode.run_slot("c2", "a"): (None, None, Runner())}
    kept = dict(runners)
    monkeypatch.setattr(code_mode, "_runners", runners)
    code_mode.close_conversation("c1")
    assert list(runners) == [code_mode.run_slot("c2", "a")]
    assert [held[2].closed for held in kept.values()] == [True, True, False]


def test_a_missing_permission_is_a_sentence_after_the_target_line(monkeypatch):
    app = FakeProvider("window", line="application windows; args.app names the application",
                       missing="OpenProgram needs Accessibility (System Settings > Privacy & Security > "
                               "Accessibility).")
    install_machines(monkeypatch, [local(window="fake")], {"local": [app]})
    text = tool.devices_text()
    assert ('  - type="window": application windows; args.app names the application. Not usable yet: OpenProgram '
            "needs Accessibility") in text


# -- an OSWorld server on this computer ----------------------------------------------

class _Client:
    def __init__(self, requests):
        self.requests = requests

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url):
        self.requests.append(url)
        return SimpleNamespace(content=PNG, raise_for_status=lambda: None)


@pytest.fixture
def server(monkeypatch):
    requests: list[str] = []
    monkeypatch.setattr(osworld.OSWorldScreen, "_client", lambda self: _Client(requests))
    return requests


@pytest.mark.parametrize("url", ["http://localhost:5000", "http://127.0.0.1:5000", "http://[::1]:5000"])
def test_a_loopback_server_showing_this_display_is_refused(server, monkeypatch, url):
    monkeypatch.setattr(local_screen, "shows_this_display", lambda png: png == PNG)
    with pytest.raises(KindError) as caught:
        osworld.KIND.admit({"url": url})
    assert str(caught.value) == (f"Refused: the OSWorld server at {url} shows this computer's own display, and "
                                 "this computer is operated as app and screen, never as a machine.")
    assert server == [f"{url}/screenshot"]


@pytest.mark.parametrize("shown", [False, None])
def test_a_loopback_server_is_saved_when_its_display_is_another_or_cannot_be_compared(server, monkeypatch, shown):
    monkeypatch.setattr(local_screen, "shows_this_display", lambda png: shown)
    osworld.KIND.admit({"url": "http://127.0.0.1:5000"})


def test_an_unreachable_loopback_server_claims_nothing(monkeypatch):
    class Down(_Client):
        def get(self, url):
            raise OSError("connection refused")

    monkeypatch.setattr(osworld.OSWorldScreen, "_client", lambda self: Down([]))
    monkeypatch.setattr(local_screen, "shows_this_display", lambda png: pytest.fail("nothing to compare"))
    osworld.KIND.admit({"url": "http://127.0.0.1:5000"})


def test_a_server_elsewhere_is_never_asked_at_save(server, monkeypatch):
    monkeypatch.setattr(local_screen, "shows_this_display", lambda png: pytest.fail("nothing to compare"))
    osworld.KIND.admit({"url": "http://192.168.64.2:5000"})
    assert server == []


def test_the_screen_address_is_the_servers_url():
    assert osworld.KIND.address({"url": "http://192.168.64.2:5000"}) == "http://192.168.64.2:5000"
    lab = Machine("lab", "Lab", screen=Connection("osworld", {"url": "HTTP://192.168.64.2:5000/"}))
    assert machines.screen_address(lab) == "192.168.64.2:5000"


# -- this computer's own display -----------------------------------------------------

class _Display(SimpleNamespace):
    pass


class _Driver:
    def __init__(self, granted=True):
        self.granted = granted
        self.captured: list[tuple] = []

    def grants(self):
        return {"screen_recording": self.granted, "accessibility": True}

    def displays(self):
        return [_Display(id=1, size=(1512.0, 982.0)), _Display(id=2, size=(2560.0, 1440.0))]

    def capture(self, display, scale):
        self.captured.append((display.id, round(scale, 4)))
        return b"display-%d" % display.id


@pytest.fixture
def thumbnails(monkeypatch):
    shades = {PNG: bytes([10] * 640), b"display-1": bytes([200] * 640), b"display-2": bytes([14] * 640),
              b"other": bytes([90] * 640)}
    monkeypatch.setattr(local_screen, "_gray", lambda png: shades.get(png))


def test_a_screenshot_of_one_of_this_computers_displays_is_recognized(monkeypatch, thumbnails):
    driver = _Driver()
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: lambda: driver)
    assert local_screen.shows_this_display(PNG) is True
    assert local_screen.shows_this_display(b"other") is False
    # Each display is captured at thumbnail size, never at full size.
    assert all(scale < 0.1 for _, scale in driver.captured)


def test_without_screen_recording_or_a_driver_nothing_is_claimed(monkeypatch, thumbnails):
    driver = _Driver(granted=False)
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: lambda: driver)
    assert local_screen.shows_this_display(PNG) is None
    assert driver.captured == []
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: None)
    assert local_screen.shows_this_display(PNG) is None


def test_an_image_that_cannot_be_decoded_claims_nothing(monkeypatch, thumbnails):
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: lambda: _Driver())
    assert local_screen.shows_this_display(b"not a png") is None
