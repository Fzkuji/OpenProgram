"""computer_use on another computer's OpenProgram: forwarded whole on this side, run there only when allowed.

The other computer is faked at ``httpx.post``; the receiving side runs with
a fake tool. Nothing reaches a real computer, window or network.
"""
from __future__ import annotations

import base64
import os
import threading
import time

import httpx
import pytest

from openprogram.host import machines
from openprogram.host.machines import status
from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use.routing import registry
from openprogram.programs.tools.computer_use.computer_use import tool
from openprogram.programs.tools.computer_use.computer_use.providers import paired
from openprogram.programs.tools.computer_use.computer_use import devices as cu_devices

_real_check = status._check

TOKEN = "opd1.0123456789abcdef.not-a-real-secret"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "config.json"
    p.write_text("{}", encoding="utf-8")
    os.chmod(p, 0o600)
    monkeypatch.setattr("openprogram.state.paths.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.config.get_config_path", lambda: p)
    monkeypatch.setattr("openprogram.state.paths.get_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(machines, "_remove_instances_later", lambda machine_id: None)
    status.forget()
    monkeypatch.setattr(status, "_check", lambda machine: None)
    yield p
    status.forget()


@pytest.fixture
def studio(cfg_path):
    """A computer paired with this one, reached at two addresses."""
    return machines.save_machine({"name": "Studio", "connection": {
        "kind": "openprogram", "url": "http://192.168.1.20:18100", "routes": ["https://studio.tail1.ts.net"],
        "token": TOKEN, "device_id": "0123456789abcdef"}})


class _Peer:
    """The other Worker: records each request, answers with ``responses`` in turn."""

    def __init__(self, monkeypatch, *responses):
        self.requests: list[dict] = []
        self.responses = list(responses)
        monkeypatch.setattr(httpx, "post", self.post)

    def post(self, url, json=None, headers=None, timeout=None, trust_env=True):
        self.requests.append({"url": url, "json": json, "authorized": headers.get("Authorization") == f"Bearer {TOKEN}",
                              "trust_env": trust_env})
        answer = self.responses.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _ok(text="done there", images=(), is_error=False):
    return httpx.Response(200, json={"text": text, "images": [base64.b64encode(image).decode() for image in images],
                                     "is_error": is_error})


# -- this side: forwarding ------------------------------------------------------------

def test_a_paired_device_lists_its_three_types_without_asking_it(studio, monkeypatch):
    peer = _Peer(monkeypatch)
    found = cu_devices.devices()["studio"].splitlines()
    assert found[0].startswith('- studio (device="studio"): Studio, ')
    assert [line.split(":")[0] for line in found[1:]] == [
        '  - device="studio", type="window"', '  - device="studio", type="screen"', '  - device="studio", type="browser"']
    assert peer.requests == []


def test_the_whole_call_goes_over_the_credential_and_the_answer_comes_back_headed(studio, monkeypatch):
    peer = _Peer(monkeypatch, _ok("tabs there", images=[PNG]))
    result = tool.run({"device": "Studio", "type": "browser", "app": "Google Chrome", "action": "get_state",
                       "args": {"tab": "t1"}, "timeout": 30}, "conv")
    [sent] = peer.requests
    assert sent["url"] == "http://192.168.1.20:18100/api/computer-use/call"
    assert sent["authorized"] and sent["trust_env"] is False
    # The other computer operates itself: no device is sent, the browser is named as resolved here.
    assert sent["json"] == {"type": "browser", "app": "chrome", "action": "get_state", "args": {"tab": "t1"},
                            "timeout": 30}
    assert result.text == '[device="studio", type="browser", app="chrome"]\ntabs there'
    assert result.images == [PNG] and not result.is_error
    assert status.state_of(machines.get_machine("studio"), refresh=False)["state"] == status.CONNECTED


def test_the_next_address_is_tried_when_one_cannot_be_reached(studio, monkeypatch):
    peer = _Peer(monkeypatch, httpx.ConnectError("away from home"), _ok())
    result = tool.run({"device": "studio", "type": "window", "app": "Safari", "action": "list_windows"}, "conv")
    assert [request["url"].split("/api")[0] for request in peer.requests] == [
        "http://192.168.1.20:18100", "https://studio.tail1.ts.net"]
    assert result.text == '[device="studio", type="window"]\ndone there'
    assert peer.requests[1]["json"]["app"] == "Safari"


def test_an_error_there_stays_an_error_here(studio, monkeypatch):
    _Peer(monkeypatch, _ok("UNKNOWN_TARGET: type=\"screen\" is not available here.", is_error=True))
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and result.text.startswith('[device="studio", type="screen"]\nUNKNOWN_TARGET')


def test_a_computer_that_does_not_allow_it_is_answered_with_where_to_turn_it_on(studio, monkeypatch):
    _Peer(monkeypatch, httpx.Response(403, json={"detail": paired.OFF}))
    result = tool.run({"device": "studio", "type": "window", "app": "Notes", "action": "click",
                       "args": {"x": 1, "y": 1}}, "conv")
    assert result.is_error
    assert "Studio does not let paired devices operate it" in result.text
    assert "Settings → Devices → This computer → Let paired devices operate this computer" in result.text


class _Clock:
    """``status``'s clock, moved by hand."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(status, "_now", lambda: self.now)


def _fail_for(machine, clock, seconds, message="no route to host"):
    """Checks of ``machine`` that fail at the start and the end of ``seconds``."""
    status.note(machine, False, message)
    clock.now += seconds
    status.note(machine, False, message)


def test_an_offline_device_is_answered_at_once_with_nothing_sent_and_checked_again(studio, monkeypatch):
    peer = _Peer(monkeypatch)
    clock = _Clock(monkeypatch)
    checked = []
    monkeypatch.setattr(status, "_check", lambda machine: checked.append(machine.id) or status._checking.discard(
        machine.id))
    _fail_for(machines.get_machine("studio"), clock, status.OFFLINE_AFTER)
    clock.now += paired.RECHECK_AFTER + 1
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and "Studio is offline (no route to host); nothing was sent." in result.text
    assert peer.requests == []
    for _ in range(100):
        if checked:
            break
        threading.Event().wait(0.01)
    assert checked == ["studio"]


def test_a_device_that_only_just_failed_is_still_tried(studio, monkeypatch):
    peer = _Peer(monkeypatch, _ok())
    _Clock(monkeypatch)
    status.note(machines.get_machine("studio"), False, "no route to host")
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert not result.is_error and len(peer.requests) == 1
    assert status.unreachable(machines.get_machine("studio")) is None


# -- <devices>: offline and back ------------------------------------------------------

def test_a_single_failed_check_does_not_mark_the_device_offline(studio, monkeypatch):
    clock = _Clock(monkeypatch)
    before = cu_devices.devices()
    status.note(machines.get_machine("studio"), False, "no route to host")
    clock.now += status.OFFLINE_AFTER * 3
    assert cu_devices.devices_note(before, cu_devices.devices()) == ""


def test_failing_checks_put_the_device_offline_and_a_success_brings_it_back(studio, monkeypatch):
    clock = _Clock(monkeypatch)
    machine = machines.get_machine("studio")
    before = cu_devices.devices()
    _fail_for(machine, clock, status.OFFLINE_AFTER / 2)
    assert cu_devices.devices() == before
    clock.now += status.OFFLINE_AFTER / 2
    status.note(machine, False, "ConnectError: no route to host " + "x" * 200)
    offline = cu_devices.devices()
    note = cu_devices.devices_note(before, offline)
    assert note.startswith("<devices>\nChanged devices, as they are now:\n- studio (device=\"studio\")")
    line = offline["studio"].splitlines()[1]
    assert line.startswith("  Cannot be reached right now (last check: ConnectError: no route to host x")
    assert "…); calls to it will most likely fail. A later message says when it is back." in line
    # The targets stay listed: a call still reaches it once it is back.
    assert 'device="studio", type="window"' in offline["studio"]

    status.note(machine, True, "http://192.168.1.20:18100")
    back = cu_devices.devices()
    assert back == before
    lines = cu_devices.devices_note(offline, back).splitlines()
    assert lines[1] == "Changed devices, as they are now:"
    assert lines[2].startswith("- studio (device=\"studio\")")
    assert lines[3] == "  Reachable again: calls to it go through."
    assert lines[4].startswith('  - device="studio", type="window"')
    # Once the model has read it, nothing more is said.
    assert cu_devices.devices_note(back, cu_devices.devices()) == ""


def test_building_the_list_never_waits_on_the_network(studio, monkeypatch):
    peer = _Peer(monkeypatch)
    clock = _Clock(monkeypatch)
    from openprogram.host.machines import check
    calls = []
    release = threading.Event()

    def slow_check(machine):
        calls.append(threading.current_thread() is threading.main_thread())
        release.wait(5)
        return False, "still away"

    monkeypatch.setattr(check, "check_machine", slow_check)
    monkeypatch.setattr(status, "_check", _real_check)
    _fail_for(machines.get_machine("studio"), clock, status.OFFLINE_AFTER)
    clock.now += status.STALE_AFTER + 1
    started = time.monotonic()
    found = cu_devices.devices()
    assert time.monotonic() - started < 2
    assert "Cannot be reached right now" in found["studio"]
    release.set()
    for _ in range(100):
        if calls:
            break
        threading.Event().wait(0.01)
    # The stale failing device was checked again, behind the answer and off this thread.
    assert calls == [False]
    assert peer.requests == []


def test_every_address_down_marks_the_device_offline(studio, monkeypatch):
    _Peer(monkeypatch, httpx.ConnectError("x"), httpx.ConnectError("y"))
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and "Studio is offline" in result.text
    assert status.state_of(machines.get_machine("studio"), refresh=False)["state"] == status.ERROR


def test_a_revoked_credential_says_to_pair_again(studio, monkeypatch):
    _Peer(monkeypatch, httpx.Response(401, json={"detail": "unauthorized"}))
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and "revoked this computer; pair it again" in result.text


def test_an_older_openprogram_there_says_to_update_it(studio, monkeypatch):
    _Peer(monkeypatch, httpx.Response(404, json={"detail": "Not Found"}))
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and "update it there" in result.text


def test_a_switched_off_device_offers_nothing(studio, monkeypatch):
    peer = _Peer(monkeypatch)
    machines.set_enabled("studio", False)
    assert not [provider for provider in registry.providers() if provider.startswith("studio")]
    result = tool.run({"device": "studio", "type": "screen", "action": "screenshot"}, "conv")
    assert result.is_error and peer.requests == []


def test_rule_keys_name_the_device(studio):
    assert tool.rule_key({"device": "studio", "type": "window", "app": "Safari", "action": "click"}) == \
        "studio window safari"
    target = registry.resolve({"device": "studio", "type": "window"})
    assert target.approval({"app": ""}, [("list_apps", {})]) is None
    assert registry.resolve({"device": "studio", "type": "screen"}).approval({}, [("click", {})]).rule == \
        "studio screen"


# -- the other side: receiving ---------------------------------------------------------

def test_the_switch_is_off_by_default():
    from openprogram.state.settings import SETTINGS
    [spec] = [spec for spec in SETTINGS if spec.key == "computer_use.paired_devices"]
    assert spec.default is False and spec.widget == "toggle"
    assert not paired.allowed({})
    assert not paired.allowed({"computer_use": {"paired_devices": "yes"}})
    assert paired.allowed({"computer_use": {"paired_devices": True}})


def _served(monkeypatch, rules=None):
    ran: list[tuple[dict, str]] = []
    monkeypatch.setattr(tool, "run", lambda call, session: ran.append((call, session)) or ToolReturn(
        text="ran here", images=[PNG]))
    monkeypatch.setattr("openprogram.state.config.read_config",
                        lambda: {"tools": {"permission_rules": rules or {}}})
    return ran


def test_a_forwarded_call_runs_on_this_computer_only(monkeypatch):
    ran = _served(monkeypatch)
    result = paired.serve({"device": "lab", "type": "window", "app": "Safari", "action": "click",
                           "args": {"x": 1, "y": 2}, "extra": "dropped"}, "0123456789abcdef")
    [(call, session)] = ran
    assert call == {"device": "", "type": "window", "app": "Safari", "action": "click", "args": {"x": 1, "y": 2}}
    assert session == "paired:0123456789abcdef"
    assert paired.encoded(result) == {"text": "ran here", "images": [base64.b64encode(PNG).decode()],
                                      "is_error": False}


@pytest.mark.parametrize("behavior", ["deny", "ask"])
def test_this_computers_rules_refuse_a_forwarded_call(monkeypatch, behavior):
    ran = _served(monkeypatch, {behavior: ["computer_use(window safari)"]})
    result = paired.serve({"type": "window", "app": "Safari", "action": "click", "args": {"x": 1, "y": 2}}, "d")
    assert result.is_error and ran == []
    assert "computer_use(window safari)" in result.text
    # Another application is not covered by the rule.
    assert not paired.serve({"type": "window", "app": "Notes", "action": "click", "args": {"x": 1, "y": 2}},
                            "d").is_error
