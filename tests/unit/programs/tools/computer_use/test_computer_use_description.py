"""computer_use's description is fixed; <devices> names only the targets set up now, by device."""
from types import SimpleNamespace

import pytest

from openprogram.programs import agent_tools
from openprogram.programs.tools.computer_use.computer_use import code as code_mode
from openprogram.programs.tools.computer_use.computer_use import tool
from openprogram.programs.tools.computer_use.computer_use.providers import built_in_browser
from tests.support.screen_targets import FakeProvider, environment, install_machines, local, machine, user_browsers

LAB = machine("lab", "Lab VM", screen={"kind": "osworld", "url": "http://192.168.64.2:5000"})


@pytest.fixture
def set_up(monkeypatch):
    """This computer with its window target and OpenProgram's browser (with the extension in ``own``), plus
    ``others``; code runs where ``code_on`` says."""
    environment(monkeypatch, None)

    def make(*others, code_on=True, own=()):
        service = SimpleNamespace(extension=user_browsers(*own))
        monkeypatch.setattr(built_in_browser, "service", lambda: service)
        monkeypatch.setattr(code_mode, "available", lambda: code_on)
        install_machines(monkeypatch, [local(window="fake", browser="built_in"), *others],
                         {"local": [FakeProvider("window", line="application windows.")]})
    return make


def test_the_description_and_parameters_do_not_change_with_what_is_set_up(set_up):
    set_up()
    listed = [t for t in agent_tools(toolset="full") if t.name == tool.NAME]
    assert listed and listed[0].description == tool.DESCRIPTION
    set_up(LAB, own=("chrome",), code_on=False)
    listed = [t for t in agent_tools(toolset="full") if t.name == tool.NAME]
    assert listed[0].description == tool.DESCRIPTION
    properties = listed[0].parameters["properties"]
    assert properties["type"]["enum"] == ["window", "screen", "browser"]
    assert {"device", "app", "display", "title", "code", "timeout"} <= set(properties)
    assert listed[0].parameters["required"] == ["type"]
    assert "lab" not in tool.DESCRIPTION and "<devices>" in tool.DESCRIPTION


def test_a_machine_that_is_not_set_up_is_not_listed(set_up):
    set_up()
    found = tool.devices()
    assert list(found) == [tool.THIS_COMPUTER]
    assert '  - type="window": application windows.' in found[tool.THIS_COMPUTER]
    assert '  - type="browser": OpenProgram\'s built-in browser' in found[tool.THIS_COMPUTER]
    assert "lab" not in tool.devices_text()


def test_a_set_up_machine_is_listed_as_a_device_of_its_own(set_up):
    set_up(LAB)
    found = tool.devices()
    assert list(found) == [tool.THIS_COMPUTER, "lab"]
    assert found["lab"] == ('- lab (device="lab"): virtual machine, pixels only.\n'
                            '  - device="lab", type="screen": virtual machine, pixels only')


def test_the_users_chrome_is_listed_only_with_its_extension(set_up):
    set_up(own=())
    assert 'app="chrome"' not in tool.devices_text()
    set_up(own=("chrome",))
    text = tool.devices_text()
    assert '  - type="browser", app="chrome": the user\'s own' in text and "edge" not in text


def test_code_is_offered_only_where_it_can_run(set_up):
    set_up(code_on=False)
    assert "calls may send code" not in tool.devices()[tool.THIS_COMPUTER]
    set_up(code_on=True)
    assert "calls may send code" in tool.devices()[tool.THIS_COMPUTER]


def test_the_whole_list_opens_and_closes_the_devices_note(set_up):
    set_up(LAB)
    text = tool.devices_text()
    assert text.startswith("<devices>\nWhat computer_use can operate now, by device:\n- this computer (device left "
                           "out): ")
    assert text.endswith("A target not listed here is refused. A later message says what changes.\n</devices>")


def test_with_nothing_set_up_the_note_says_so():
    assert tool.devices_note(None, {}) == ("<devices>\nNothing can be operated now.\nA target not listed here is "
                                           "refused. A later message says what changes.\n</devices>")


def test_a_later_note_says_only_what_changed():
    before = {"this computer": "- this computer: a", "lab": "- lab: b", "old": "- old: c"}
    assert tool.devices_note(before, dict(before)) == ""
    after = {"this computer": "- this computer: a, now with chrome", "lab": "- lab: b", "new": "- new: d"}
    assert tool.devices_note(before, after) == (
        "<devices>\nAdded devices:\n- new: d\nChanged devices, as they are now:\n- this computer: a, now with "
        "chrome\nRemoved devices (no longer operated):\n- old\n</devices>")


def test_a_later_note_with_one_kind_of_change_has_only_that_part():
    before = {"this computer": "- this computer: a"}
    assert tool.devices_note(before, {**before, "lab": "- lab: b"}) == "<devices>\nAdded devices:\n- lab: b\n</devices>"
    assert tool.devices_note({**before, "lab": "- lab: b"}, before) == (
        "<devices>\nRemoved devices (no longer operated):\n- lab\n</devices>")


def test_the_code_parameter_shows_how_an_action_is_called():
    # A model that no longer has the code guide in view wrote ``await click(...)`` and lost the call.
    code = tool._PARAMETERS["properties"]["code"]["description"]
    assert "await cu.click(element=3)" in code
    # cu is the call's target: no target is reached through it by name.
    assert not [name for name in ("cu.app", "cu.browser", "cu.screen", "cu.vm") if name in code]


def test_the_code_guide_names_no_target_through_cu():
    guide = code_mode.guide()
    assert "await cu.click(element=3)" in guide
    assert not [name for name in ("cu.app", "cu.browser", "cu.screen", "cu.vm") if name in guide]
