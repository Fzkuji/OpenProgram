"""computer_use_agent names only what this computer offers now."""
from openprogram.programs import agent_tools
from openprogram.programs.tools.computer_use.computer_use_agent import code as code_mode
from openprogram.programs.tools.computer_use.computer_use_agent import tool
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app, browser, vm


def _set(monkeypatch, *, app_on=True, browser_on=True, vm_on=False, code_on=True, own=()):
    monkeypatch.setattr(app, "available", lambda: app_on)
    monkeypatch.setattr(browser, "available", lambda: browser_on)
    monkeypatch.setattr(vm, "available", lambda: vm_on)
    monkeypatch.setattr(code_mode, "available", lambda: code_on)
    monkeypatch.setattr(browser, "own_browsers", lambda: own)


def test_an_unconfigured_vm_is_not_mentioned(monkeypatch):
    _set(monkeypatch, vm_on=False)
    described = tool.describe()
    assert "vm" not in described["parameters"]["properties"]["target"]["enum"]
    assert "virtual machine" not in described["description"] and "- vm:" not in described["description"]


def test_a_configured_vm_is_offered(monkeypatch):
    _set(monkeypatch, vm_on=True)
    described = tool.describe()
    assert described["parameters"]["properties"]["target"]["enum"] == ["app", "browser", "vm"]
    assert "- vm:" in described["description"]


def test_the_users_chrome_is_named_only_with_its_extension(monkeypatch):
    _set(monkeypatch, own=())
    text = tool.describe()["description"]
    assert "args.browser" not in text and "operated as an application window" in text
    _set(monkeypatch, own=("chrome",))
    text = tool.describe()["description"]
    assert 'args.browser="chrome" operates the user\'s own Chrome' in text and "Edge" not in text


def test_code_is_left_out_where_it_cannot_run(monkeypatch):
    _set(monkeypatch, code_on=False)
    described = tool.describe()
    assert "code" not in described["parameters"]["properties"]
    assert "timeout" not in described["parameters"]["properties"]
    assert "code: Python" not in described["description"]
    _set(monkeypatch, code_on=True)
    assert "code: Python that loops and decides" in tool.describe()["description"]


def test_the_code_parameter_shows_how_an_action_is_called(monkeypatch):
    # A model that no longer has the code guide in view wrote ``await click(...)`` and lost the call.
    _set(monkeypatch, app_on=False, vm_on=False)
    code = tool.describe()["parameters"]["properties"]["code"]["description"]
    assert "await cu.browser.read()" in code and "cu.app" not in code and "cu.vm" not in code
    _set(monkeypatch, app_on=True, vm_on=True)
    code = tool.describe()["parameters"]["properties"]["code"]["description"]
    assert 'await cu.app.click(app="Numbers", element_index=3)' in code and "await cu.vm.screenshot()" in code


def test_the_tool_list_carries_the_description_of_now(monkeypatch):
    _set(monkeypatch, vm_on=False)
    listed = [t for t in agent_tools(toolset="full") if t.name == tool.NAME]
    assert listed and "- vm:" not in listed[0].description
    _set(monkeypatch, vm_on=True)
    listed = [t for t in agent_tools(toolset="full") if t.name == tool.NAME]
    assert "- vm:" in listed[0].description


def test_the_code_guide_shows_only_present_targets(monkeypatch):
    _set(monkeypatch, vm_on=False)
    assert "cu.vm" not in code_mode.guide() and "cu.app" in code_mode.guide()
