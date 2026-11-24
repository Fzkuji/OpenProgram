"""Kind osworld: a machine's screen behind an OSWorld server, reached through a fake HTTP layer."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from openprogram.programs.tools.computer_use.computer_use import errors, locks, registry, tool
from openprogram.programs.tools.computer_use.computer_use.contract import Approval, Call
from openprogram.programs.tools.computer_use.computer_use.providers import osworld
from openprogram.programs.tools.computer_use.computer_use.tool import computer_use

from tests.support.screen_targets import PNG, environment, install_machines, local, machine

URL = "http://192.168.64.2:5000"


class ConnectError(Exception):
    """Named as httpx names a request that never reached the server."""


class ReadTimeout(Exception):
    """Named as httpx names a request that was sent and got no answer."""


class _Response:
    def __init__(self, content: bytes = b"", status: int = 200):
        self.content, self.status = content, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class _Server:
    """What the machine answers, and every request that reached it."""

    def __init__(self):
        self.requests: list[tuple] = []
        # Raised by the next requests, in order, before an answer.
        self.failures: list[Exception] = []
        self.reply = {"returncode": 0}

    def client(self):
        server = self

        class _Client:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url):
                server.requests.append(("GET", url[len(URL):]))
                if server.failures:
                    raise server.failures.pop(0)
                return _Response(PNG)

            def post(self, url, content, headers):
                if server.failures:
                    raise server.failures.pop(0)
                body = json.loads(content)
                server.requests.append(("POST", url[len(URL):], body["command"][2]))
                return _Response(json.dumps(server.reply).encode())

        return _Client()

    def scripts(self) -> list[str]:
        return [request[2] for request in self.requests if request[0] == "POST"]


@pytest.fixture
def server(monkeypatch, tmp_path):
    found = _Server()
    monkeypatch.setattr(osworld.OSWorldScreen, "_client", lambda self: found.client())
    monkeypatch.setattr(osworld, "time", SimpleNamespace(sleep=lambda seconds: None))
    # Requests run in this thread; the thread that lets a cancelled call go is tested as a component.
    monkeypatch.setattr(osworld, "_bounded", lambda fn, call: fn())
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")
    install_machines(monkeypatch, [local(), machine("lab", "Lab VM", screen={"kind": "osworld", "url": URL})])
    environment(monkeypatch, local())
    monkeypatch.setattr(tool, "_code_available", lambda: False)
    return found


def test_a_machine_with_an_osworld_screen_yields_screen_of_that_machine(server):
    provider = registry.resolve({"device": "lab", "type": "screen"})
    assert isinstance(provider, osworld.OSWorldScreen)
    assert (provider.family, provider.machine) == ("screen", "lab")
    assert provider.capabilities == frozenset({"pixels", "pointer", "hover", "keyboard"})
    assert "tree" not in provider.capabilities


def test_get_state_is_a_screenshot(server):
    result = computer_use(device="lab", type="screen")
    assert result.images == [PNG] and not result.is_error
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nScreenshot of the machine.")
    assert server.requests == [("GET", "/screenshot")]


@pytest.mark.parametrize("action, args, script", [
    ("click", {"x": 10, "y": 20},
     'import time, pyautogui; pyautogui.click(10, 20, button="left", clicks=1, interval=0.08)'),
    ("double_click", {"coordinate": [3, 4]},
     'import time, pyautogui; pyautogui.click(3, 4, button="left", clicks=2, interval=0.08)'),
    ("right_click", {"x": 1, "y": 2},
     'import time, pyautogui; pyautogui.click(1, 2, button="right", clicks=1, interval=0.08)'),
    ("click", {"x": 1, "y": 2, "modifiers": "cmd"},
     'import time, pyautogui; pyautogui.keyDown("win"); pyautogui.click(1, 2, button="left", clicks=1, '
     'interval=0.08); pyautogui.keyUp("win")'),
    ("hover", {"x": 5, "y": 6},
     "import time, pyautogui; pyautogui.moveTo(5, 6, duration=0.2); time.sleep(0.8)"),
    ("left_click_drag", {"start_coordinate": [1, 2], "coordinate": [3, 4]},
     "import pyautogui; pyautogui.moveTo(1, 2); pyautogui.dragTo(3, 4, duration=0.5, button='left')"),
    ("scroll", {"x": 7, "y": 8, "direction": "down"},
     "import pyautogui; pyautogui.moveTo(7, 8); pyautogui.scroll(-5)"),
    ("type", {"text": "héllo"},
     'import pyautogui; pyautogui.write("h\\u00e9llo", interval=0.02)'),
    ("press_key", {"key": "Enter"}, 'import pyautogui; pyautogui.hotkey(*["enter"])'),
    ("hotkey", {"keys": ["Control", "Shift", "T"]}, 'import pyautogui; pyautogui.hotkey(*["ctrl", "shift", "t"])'),
])
def test_pointer_and_key_steps_become_the_servers_requests(server, action, args, script):
    result = computer_use(device="lab", type="screen", action=action, args=args)
    assert not result.is_error, result.text
    assert server.requests == [("POST", "/execute", script), ("GET", "/screenshot")]
    assert result.images == [PNG]


def test_steps_run_in_order_and_the_batch_says_how_many_are_done(server):
    result = computer_use(device="lab", type="screen", actions=[
        {"action": "click", "args": {"x": 1, "y": 1}}, {"action": "type_text", "args": {"text": "a"}}])
    assert [request[0] for request in server.requests] == ["POST", "POST", "GET"]
    assert "2 of 2 steps done." in result.text


def test_get_state_in_a_batch_counts_as_done(server):
    result = computer_use(device="lab", type="screen", actions=[
        {"action": "click", "args": {"x": 1, "y": 1}}, {"action": "get_state"},
        {"action": "press_key", "args": {"key": "Return"}}])
    assert not result.is_error, result.text
    assert "2. get_state: the screen is read after the steps" in result.text
    assert "3 of 3 steps done." in result.text
    assert len(server.scripts()) == 2


@pytest.mark.parametrize("bad", [
    {"action": "wait", "args": {"seconds": "soon"}},
    {"action": "click", "args": {"x": "left", "y": 1}},
    {"action": "type_text", "args": {"text": ""}},
])
def test_a_bad_argument_in_a_later_step_is_refused_before_anything_is_sent(server, bad):
    result = computer_use(device="lab", type="screen", actions=[{"action": "type_text", "args": {"text": "hello"}}, bad])
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nBAD_ARGUMENT: step 2 (")
    assert result.json_data == {"action_dispatched": False}
    assert server.requests == []


def test_a_failed_step_stops_the_batch(server):
    server.reply = {"returncode": 1, "stderr": "no display"}
    result = computer_use(device="lab", type="screen", actions=[
        {"action": "click", "args": {"x": 1, "y": 1}}, {"action": "click", "args": {"x": 2, "y": 2}}])
    assert result.is_error and "step 1 (click) failed: no display" in result.text
    assert "0 of 2 steps done." in result.text
    assert len(server.scripts()) == 1


def test_a_machine_that_does_not_answer_before_anything_is_sent_is_unreachable(server):
    server.failures = [ConnectError("connection refused")]
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 1})
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nMACHINE_UNREACHABLE: machine lab at ")
    assert "Nothing was sent." in result.text
    assert result.json_data == {"action_dispatched": False}
    server.failures = [ConnectError("connection refused")]
    result = computer_use(device="lab", type="screen")
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nMACHINE_UNREACHABLE: ")
    assert result.json_data == {"action_dispatched": False}


def test_a_step_sent_without_an_answer_is_an_unknown_outcome(server):
    server.failures = [ReadTimeout("timed out")]
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 1})
    assert result.is_error and result.text.startswith("[device=\"lab\", type=\"screen\"]\nOUTCOME_UNKNOWN: ")
    assert "do not repeat the step" in result.text
    assert result.json_data is None


def test_an_unknown_outcome_in_a_batch_names_the_steps_done(server):
    server.failures = []
    calls = {"n": 0}
    original = server.client

    def flaky():
        calls["n"] += 1
        if calls["n"] == 2:
            server.failures = [ReadTimeout("timed out")]
        return original()

    server.client = flaky
    result = computer_use(device="lab", type="screen", actions=[
        {"action": "click", "args": {"x": 1, "y": 1}}, {"action": "click", "args": {"x": 2, "y": 2}}])
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nOUTCOME_UNKNOWN: Steps done: 1. click.")


def test_a_pixels_only_screen_refuses_elements_and_names_what_works(server):
    result = computer_use(device="lab", type="screen", action="click", args={"element": 3})
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nNOT_SUPPORTED: device=\"lab\", type=\"screen\" shows pixels only")
    assert server.requests == []


def test_the_machine_is_taken_exclusively(server):
    provider = registry.resolve({"device": "lab", "type": "screen"})
    assert provider.lock({}, [("click", {})]) == locks.Locks(exclusive=(locks.machine_key("lab"),))
    with locks.held(locks.Locks(exclusive=(locks.machine_key("lab"),))):
        with pytest.raises(locks.Busy):
            with locks.held(provider.lock({}, [("get_state", {})]), seconds=0.1):
                pass


def test_a_set_up_machine_asks_nothing_and_a_rule_can_name_it(server):
    provider = registry.resolve({"device": "lab", "type": "screen"})
    assert provider.approval({}, [("click", {"x": 1, "y": 1})]) == Approval(None, "lab screen")
    call = {"device": "lab", "type": "screen", "action": "click", "args": {"x": 1, "y": 1}}
    assert tool.rule_key(call) == "lab screen"
    assert tool.requires_approval(**call) is False


def test_its_description_and_note_say_what_it_lacks(server):
    provider = registry.resolve({"device": "lab", "type": "screen"})
    assert provider.describe().line == "virtual machine, pixels only"
    note = provider.note()
    assert 'device="lab" is the whole screen of a machine (Lab VM)' in note and "pixels only" in note
    assert "super" in note
    assert '  - device="lab", type="screen": virtual machine, pixels only' in tool.devices()["lab"]


def test_one_display_only(server):
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 1, "display": 2})
    assert result.text.startswith("[device=\"lab\", type=\"screen\"]\nNOT_SUPPORTED: device=\"lab\", type=\"screen\" has one display")
    assert server.requests == []


def test_the_url_is_checked_when_the_machine_is_saved():
    from openprogram.host.machines import kinds
    assert kinds.validated("screen", {"kind": "osworld", "url": URL + "/"}) == {"kind": "osworld", "url": URL}
    with pytest.raises(kinds.KindError):
        kinds.validated("screen", {"kind": "osworld", "url": "ftp://lab"})


def test_a_call_out_of_time_names_the_steps_that_took_effect(server):
    provider = registry.resolve({"device": "lab", "type": "screen"})
    checks = iter([None, None, errors.Timeout("the call ran out of time")])

    class _Late(Call):
        def check(self):
            failure = next(checks)
            if failure is not None:
                raise failure

    with pytest.raises(errors.Timeout) as caught:
        provider.act({}, [("click", {"x": 1, "y": 1}), ("click", {"x": 2, "y": 2})], _Late("conv", "agent"))
    assert "Steps that took effect: 1. click, 2. click." in str(caught.value) and caught.value.sent
    # A call out of time takes no closing screenshot.
    assert [request[0] for request in server.requests] == ["POST", "POST"]
    late = Call("conv", "agent", deadline=0.0)
    with pytest.raises(errors.Timeout) as caught:
        provider.act({}, [("click", {"x": 1, "y": 1})], late)
    assert not caught.value.sent


def test_the_kind_reads_in_chinese_and_says_where_without_a_sentence():
    assert osworld.KIND.label_zh == "OSWorld 服务器"
    assert osworld.KIND.settings["properties"]["url"]["x-zh"]["title"] == "服务器地址"
    assert osworld.KIND.where({"url": URL}) == URL
