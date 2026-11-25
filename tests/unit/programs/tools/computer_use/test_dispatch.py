"""The tool and its one path to a target: checked steps reach the provider, and the model reads by machine."""
from __future__ import annotations

import pytest

from openprogram.models.providers.types import AgentToolResult, TextContent
from openprogram.programs import _is_available_agent_tool, get_agent_tool
from openprogram.programs._authoring import guides
from openprogram.programs._host.execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use.routing import dispatch
from openprogram.programs.tools.computer_use.computer_use import errors, tool
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.tool import computer_use

from tests.support.screen_targets import PIXELS, FakeProvider, app_approval, environment, install_machines, local, machine
from openprogram.programs.tools.computer_use.computer_use import (context as cu_context, devices as cu_devices, type_guides as cu_type_guides)



def _app() -> FakeProvider:
    return FakeProvider("window", extras=("list_apps", "list_windows"), approval=app_approval,
                        line="application windows, read and operated in the background.")


class _BuiltInBrowser(FakeProvider):
    """Says, as the built-in browser does, whose localhost it reaches when the shell runs elsewhere."""

    def environment_note(self, machine: str, targets: set[str]) -> str:
        own = f"{machine} browser"
        return " Its http://localhost is this computer's" + (
            f"; for {machine}'s use device=\"{machine}\", type=\"browser\"." if own in targets else ".")


def _browser() -> FakeProvider:
    return _BuiltInBrowser("browser", extras=("list_tabs", "navigate"), authority="browser.control",
                           line="OpenProgram's built-in browser, this conversation's pages.")


def _lab(machine_id: str = "lab") -> FakeProvider:
    return FakeProvider("screen", machine_id, capabilities=PIXELS,
                        line="a virtual machine, pixels only",
                        note=f"Note: device={machine_id} shows pixels only; super is its Windows key.")


@pytest.fixture
def here(monkeypatch):
    """This computer alone, as the conversation's environment."""
    providers = {"window": _app(), "browser": _browser()}
    install_machines(monkeypatch, [local()], {"local": list(providers.values())})
    environment(monkeypatch, local())
    monkeypatch.setattr(cu_context, "_code_available", lambda: False)
    return providers


@pytest.fixture
def fleet(monkeypatch):
    """This computer, an SSH host that is the conversation's environment, another computer and a VM."""
    devbox = machine("devbox", "devbox", "ssh", target="me@devbox")
    providers = {"window": _app(), "screen": FakeProvider("screen", line="a whole display."),
                 "browser": _browser(), "browser chrome": FakeProvider("browser", app="chrome", extras=("list_tabs",),
                                                                       line="the user's own Google Chrome."),
                 "studio window": FakeProvider("window", "studio"),
                 "studio screen": FakeProvider("screen", "studio"),
                 "studio browser": FakeProvider("browser", "studio"),
                 "lab screen": _lab()}
    install_machines(monkeypatch,
            [local(), machine("lab"), devbox, machine("studio"),
             machine("builder", "builder", "docker")],
            {"local": [providers[name] for name in ("window", "screen", "browser", "browser chrome")],
             "studio": [providers[name] for name in ("studio window", "studio screen", "studio browser")],
             "lab": [providers["lab screen"]]})
    environment(monkeypatch, devbox)
    monkeypatch.setattr(cu_context, "_code_available", lambda: False)
    return providers


# -- one path to the target --------------------------------------------------------

def test_a_list_action_reaches_the_providers_list(here):
    result = computer_use(type="window", action="list_apps")
    assert result.text == "apps of window" and not result.is_error
    assert here["window"].calls == [("list", "apps", {}, {})]


def test_get_state_reaches_the_provider_with_the_subject_and_its_options(here):
    result = computer_use(type="window", app="Notes", action="get_app_state", args={"changes_only": True})
    assert result.text == "state of window" and result.images
    assert here["window"].calls == [("get_state", {"app": "Notes"}, {"full": False}, True)]


def test_a_call_with_the_target_alone_reads_its_state(here):
    computer_use(type="window", app="Notes")
    assert here["window"].calls == [("get_state", {"app": "Notes"}, {}, True)]


def test_the_old_argument_names_inside_args_are_still_read(here):
    computer_use(type="window", args={"app": "Notes", "window_id": 7})
    assert here["window"].calls == [("get_state", {"app": "Notes", "window_id": 7}, {}, True)]


def test_title_names_a_window_by_its_title_or_its_id(here):
    computer_use(type="window", app="Notes", title="Shopping")
    computer_use(type="window", app="Notes", title="42")
    assert here["window"].calls == [("get_state", {"app": "Notes", "window": "Shopping"}, {}, True),
                                    ("get_state", {"app": "Notes", "window_id": 42}, {}, True)]


def test_title_names_a_browser_tab(here):
    computer_use(type="browser", title="t3", action="navigate", args={"url": "https://example.com"})
    assert here["browser"].calls == [("act", {"tab": "t3"}, [("navigate", {"url": "https://example.com"})])]


def test_steps_reach_act_in_our_names(here):
    result = computer_use(type="WINDOW", app="Notes", actions=[
        {"action": "left_click", "args": {"element_index": 4}},
        {"action": "type", "text": "hello"},
        {"action": "hotkey", "args": {"keys": ["cmd", "s"]}}])
    assert result.text == "Done: click, type_text, press_key"
    assert here["window"].calls == [("act", {"app": "Notes"}, [
        ("click", {"element": 4}), ("type_text", {"text": "hello"}), ("press_key", {"key": "super+s"})])]


def test_a_refused_step_sends_nothing(here):
    result = computer_use(type="window", action="click", args={"selector": "#go"})
    assert result.is_error and result.text.startswith("BAD_ARGUMENT: click does not take selector")
    assert result.json_data == {"action_dispatched": False}
    assert here["window"].calls == []
    result = computer_use(type="window", action="navigate", args={"url": "https://example.com"})
    assert result.text.startswith("UNKNOWN_ACTION: type=\"window\" has no action 'navigate'; it is an action of "
                                  'type="browser".')


def test_a_list_of_one_step_takes_no_step_arguments_from_args(here):
    result = computer_use(type="window", app="Notes", args={"x": 5, "y": 6},
                          actions=[{"action": "click", "args": {"element": 3}}])
    assert result.text.startswith("BAD_ARGUMENT: args beside a list of actions may only hold app, window, window_id")
    assert here["window"].calls == []
    from openprogram.programs.tools.computer_use.computer_use.tool import facts
    assert not facts({"type": "window", "app": "Notes", "args": {"x": 5},
                      "actions": [{"action": "click", "args": {"element": 3}}]}).runs


def test_an_unknown_target_lists_the_targets(here):
    result = computer_use(type="screen", action="get_state")
    assert result.is_error and result.text == ("UNKNOWN_TARGET: type='screen' is not available here. Targets here: "
                                               'type="window"; type="browser".')
    result = computer_use(type="window", device="vm", action="get_state")
    assert result.text == ("UNKNOWN_TARGET: nothing on device 'vm' can be operated now. Targets now: "
                           'type="window"; type="browser".')
    assert here["window"].calls == []


def test_a_providers_coded_failure_is_the_answer(here):
    def unreachable(subject, steps, call):
        raise errors.MachineUnreachable("machine lab is off. Nothing was sent.")

    provider = FakeProvider("screen", "lab", act=unreachable)
    result = dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 1})], Call("c", "a"))
    assert result.text == "MACHINE_UNREACHABLE: machine lab is off. Nothing was sent."
    assert result.json_data == {"action_dispatched": False}

    def lost(subject, steps, call):
        raise errors.OutcomeUnknown("click was sent and the machine did not answer.")

    provider = FakeProvider("screen", "lab", act=lost)
    result = dispatch.run_steps(provider, {}, [("click", {"x": 1, "y": 1})], Call("c", "a"))
    assert result.text.startswith("OUTCOME_UNKNOWN: ") and result.json_data is None


# -- the result header -------------------------------------------------------------

def test_a_result_from_another_machine_starts_with_its_address(fleet):
    result = computer_use(device="lab", type="screen", action="click", args={"x": 1, "y": 2})
    assert result.text.startswith('[device="lab", type="screen"]\n')
    refused = computer_use(device="lab", type="screen", action="click", args={"element": 3})
    assert refused.text.startswith('[device="lab", type="screen"]\nNOT_SUPPORTED: ')


def test_a_call_without_device_says_this_computer_where_the_shell_runs_elsewhere(fleet):
    assert computer_use(type="window", action="list_apps").text.startswith('[type="window", this computer]\n')


def test_a_call_without_device_has_no_header_where_the_shell_runs_here(here):
    assert computer_use(type="window", action="list_apps").text == "apps of window"


# -- <devices> -------------------------------------------------------------------------

def test_the_devices_are_listed_this_computer_then_the_environment_then_the_rest(fleet):
    found = cu_devices.devices()
    assert list(found) == [cu_devices.THIS_COMPUTER, "devbox", "lab", "studio"]
    lines = found[cu_devices.THIS_COMPUTER].splitlines()
    assert lines[0].startswith("- this computer (device left out): ") and lines[0].endswith(", where OpenProgram runs.")
    assert lines[1:] == [
        '  - type="window": application windows, read and operated in the background.',
        '  - type="screen": a whole display.',
        '  - type="browser": OpenProgram\'s built-in browser, this conversation\'s pages. Its http://localhost is '
        'this computer\'s.',
        '  - type="browser", app="chrome": the user\'s own Google Chrome.',
    ]
    assert found["devbox"].splitlines() == [
        '- devbox (device="devbox"): SSH host me@devbox; this conversation\'s shell and files are here.',
        '  nothing on it can be operated with computer_use.',
    ]
    assert found["studio"].splitlines()[1:] == [
        '  - device="studio", type="window": window of studio',
        '  - device="studio", type="screen": screen of studio',
        '  - device="studio", type="browser": browser of studio',
    ]
    assert found["lab"].splitlines() == ['- lab (device="lab"): Test device.',
                                         '  - device="lab", type="screen": a virtual machine, pixels only']
    # A machine with no target that is not the environment is not listed.
    assert "builder" not in cu_devices.devices_text()


def test_the_environments_entry_says_it_has_no_screen(monkeypatch):
    builder = machine("builder", "builder", "docker")
    install_machines(monkeypatch, [local(), builder], {"local": [_app()]})
    environment(monkeypatch, builder)
    monkeypatch.setattr(cu_context, "_code_available", lambda: False)
    text = cu_devices.devices()["builder"]
    assert text.startswith('- builder (device="builder"): Docker container')
    assert text.endswith("this conversation's shell and files are here.\n"
                         "  nothing on it can be operated with computer_use.")


def test_the_localhost_note_appears_only_when_the_shell_runs_elsewhere(here):
    text = cu_devices.devices_text()
    assert 'device="' not in text
    assert "Its http://localhost" not in text


def test_a_target_missing_a_permission_stays_listed_with_a_note(monkeypatch):
    install_machines(monkeypatch, [local(), machine("lab")],
            {"local": [FakeProvider("window", line="application windows.",
                                    missing="Accessibility is off: System Settings > Privacy & Security.")],
             "lab": [FakeProvider("screen", "lab", line="a VM", missing="the server needs a token")]})
    environment(monkeypatch, local())
    found = cu_devices.devices()
    assert ('  - type="window": application windows. Not usable yet: Accessibility is off: System Settings > '
            "Privacy & Security.") in found[cu_devices.THIS_COMPUTER]
    assert found["lab"].endswith('  - device="lab", type="screen": a VM. Not usable yet: the server needs a token')


def test_the_tool_is_hidden_when_no_target_exists(monkeypatch):
    install_machines(monkeypatch, [local()], {})
    assert cu_devices.available_targets() == []
    assert not _is_available_agent_tool(get_agent_tool("computer_use"))
    install_machines(monkeypatch, [local()], {"local": [_app()]})
    assert cu_devices.available_targets() == ["window"]
    assert _is_available_agent_tool(get_agent_tool("computer_use"))


# -- guides ----------------------------------------------------------------------------

def _result(text: str = "Done", error: bool = False) -> AgentToolResult:
    return AgentToolResult(content=[TextContent(text=text)], is_error=error)


def _attached(result: AgentToolResult) -> str:
    """The guides recorded beside a result, as the model reads them after it."""
    from openprogram.models.providers.shared.tool_attachments import record
    found = record(getattr(result, "details", None))
    return found["text"] if found else ""


@pytest.fixture
def loop():
    token = guides.begin_loop()
    try:
        yield
    finally:
        guides.end_loop(token)


def test_the_family_guide_and_the_target_note_arrive_with_the_first_call(monkeypatch, loop):
    install_machines(monkeypatch, [local(), machine("lab"), machine("lab-2")],
            {"lab": [_lab()], "lab-2": [_lab("lab-2")]})
    environment(monkeypatch, local())
    monkeypatch.setattr(cu_context, "_code_available", lambda: False)
    cu_devices.devices()
    call = {"device": "Lab", "type": "Screen", "action": "click", "args": {"x": 1, "y": 2}}
    first = _attached(guides.attach(tool.NAME, call, _result()))
    assert '<guide name="computer_use.screen">' in first
    assert '<guide name="computer_use.note.lab screen">' in first
    assert "Note: device=lab shows pixels only" in first
    assert '<guide name="computer_use.window">' not in first
    guides.observe_request([{"role": "user", "content": first}])
    assert _attached(guides.attach(tool.NAME, call, _result())) == ""
    # Another target of the family gets its own note only.
    second = _attached(guides.attach(tool.NAME, {**call, "device": "lab-2"}, _result()))
    assert '<guide name="computer_use.note.lab-2 screen">' in second
    assert '<guide name="computer_use.screen">' not in second


def test_a_call_to_an_unknown_target_carries_no_guide(monkeypatch, loop):
    install_machines(monkeypatch, [local(), machine("lab")], {"lab": [_lab()]})
    environment(monkeypatch, local())
    cu_devices.devices()
    assert _attached(guides.attach(tool.NAME, {"device": "vm", "type": "screen", "action": "click"},
                                   _result("UNKNOWN_TARGET: x", error=True))) == ""


def test_action_guide_shows_the_family_guide_and_the_note_again(monkeypatch):
    install_machines(monkeypatch, [local(), machine("lab")], {"lab": [_lab()]})
    environment(monkeypatch, local())
    monkeypatch.setattr(cu_context, "_code_available", lambda: False)
    result = computer_use(device="lab", type="screen", action="guide")
    assert '<guide name="computer_use.screen">' in result.text
    assert "Note: device=lab shows pixels only" in result.text
    assert '<guide name="computer_use.note.lab screen">' in cu_type_guides.guide({"device": "lab", "type": "screen"})
