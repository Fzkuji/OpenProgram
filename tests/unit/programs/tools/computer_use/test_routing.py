"""computer_use routing, and the browser and osworld targets through their providers, without a host screen."""
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from openprogram.host import machines
from openprogram.programs.tools.computer_use.computer_use import errors, locks
from openprogram.programs.tools.computer_use.computer_use import tool as entry
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.providers import built_in_browser, osworld
from openprogram.programs.tools.computer_use.computer_use.providers.desktop import AppProvider
from tests.support.screen_targets import PNG, FakeProvider, environment, install, install_machines, local, machine

URL = "http://vm.test:5000"


class _Driver:
    """The tab driver: answers each action in turn and records what it was asked."""

    def __init__(self, *replies):
        self.replies, self.runs = list(replies), []

    def run(self, action, args, session, agent=""):
        self.runs.append((action, dict(args), session))
        return dict(self.replies.pop(0))

    def known(self, tab):
        return None


def _browser(driver):
    @contextmanager
    def batch():
        yield
    return built_in_browser.BuiltInBrowser(SimpleNamespace(tabs=SimpleNamespace(driver=lambda: driver, batch=batch)))


@pytest.fixture
def listed(monkeypatch, tmp_path):
    """Make the given providers this computer's targets, as the tool lists them."""
    monkeypatch.setattr(locks, "_root", lambda: tmp_path / "locks")
    environment(monkeypatch, None)

    def make(*providers):
        install_machines(monkeypatch, [local(window="fake", browser="fake")], {"local": list(providers)})
    return make


# ----------------------------------------------------------------- routing

def test_steps_take_nested_or_flat_arguments():
    assert entry._steps("click", {"ref": "e1"}, None) == [("click", {"ref": "e1"})]
    steps = entry._steps("", {}, [{"action": "type", "args": {"ref": "e3", "text": "hi"}},
                                  {"action": "press", "key": "Enter"}])
    assert steps == [("type", {"ref": "e3", "text": "hi"}), ("press", {"key": "Enter"})]
    assert entry._steps("", {}, None) == []


def test_code_and_actions_are_one_or_the_other(listed):
    browser = FakeProvider("browser")
    listed(browser)
    result = entry.computer_use(type="browser", action="observe", code="pass")
    assert result.is_error and result.text.startswith("BAD_ARGUMENT:") and "not both" in result.text
    assert browser.calls == []


def test_a_target_that_is_not_set_up_is_unknown_and_the_answer_lists_the_targets(monkeypatch, listed):
    # A machine reached only by shell has no target.
    install_machines(monkeypatch, [local(window="fake"), machine("devbox", shell={"kind": "ssh", "target": "me@devbox"})],
                     {"local": [FakeProvider("window")]})
    result = entry.computer_use(device="devbox", type="screen")
    assert result.is_error and result.text.startswith("UNKNOWN_TARGET:")
    assert 'Targets now: type="window".' in result.text


def test_json_results_keep_their_data(listed):
    listed(_browser(_Driver({"ok": True, "tabs": []})))
    result = entry.computer_use(type="browser")
    assert result.json_data == {"ok": True, "tabs": []}


def test_the_caller_operates_the_screen_itself():
    """The tool has no way to pass the task to another agent: whoever calls it carries it through."""
    assert not {"context", "brief"} & set(entry._PARAMETERS["properties"])
    assert "new agent" not in entry.DESCRIPTION
    with pytest.raises(TypeError):
        entry.computer_use(type="window", app="Notes", context="fresh", brief="b")


# ----------------------------------------------------------------- browser

def test_browser_actions_reach_the_tab_driver_and_read_as_text(listed):
    driver = _Driver({"ok": True, "tab": "t2", "url": "https://a.test/", "title": "A", "done": "clicked e2",
                      "changes": '- <changed> button "Go" [ref=e2]'})
    listed(_browser(driver))
    # A carried-over argument name reaches the driver in ours.
    result = entry.computer_use(type="browser", action="click", args={"tab": "t2", "ref": "e2"})
    assert driver.runs == [("click", {"tab": "t2", "element": "e2"}, entry._session_id())]
    assert result.text.splitlines()[:3] == ["clicked e2", "tab t2 · A · https://a.test/", "changes:"]


def test_browser_steps_stop_at_the_first_failure(listed):
    driver = _Driver({"ok": True, "done": "typed"}, {"ok": False, "reason_code": "element_gone", "error": "gone"},
                     {"ok": True})
    listed(_browser(driver))
    result = entry.computer_use(type="browser", actions=[
        {"action": "type", "args": {"element": "e1", "text": "a"}}, {"action": "click", "args": {"element": "e2"}},
        {"action": "press", "args": {"key": "Enter"}}])
    assert result.is_error and [run[0] for run in driver.runs] == ["type_text", "click"]
    assert result.text.startswith("ELEMENT_GONE: step 2 (click): gone Steps done: 1. type_text.")
    # A step went out before the failure, so the answer does not claim nothing was sent.
    assert result.json_data is None


def test_a_browser_step_that_fails_without_a_code_keeps_its_words_and_counts_the_steps_done(listed):
    driver = _Driver({"ok": True, "done": "typed"}, {"ok": False, "error": "the button is disabled"}, {"ok": True})
    listed(_browser(driver))
    result = entry.computer_use(type="browser", actions=[
        {"action": "type_text", "args": {"element": "e1", "text": "a"}},
        {"action": "click", "args": {"element": "e2"}}, {"action": "press_key", "args": {"key": "Return"}}])
    assert result.is_error and len(result.json_data) == 2 and len(driver.runs) == 2
    assert result.text.endswith("step 2 failed; the steps after it did not run. 1 of 3 steps done.")


def test_the_built_in_browser_asks_for_uploads_only(listed):
    listed(_browser(_Driver()))
    assert entry.requires_approval(type="browser", action="click", args={"ref": "e1"}) is False
    assert entry.requires_approval(type="browser", action="navigate", args={"url": "http://localhost:3000/"}) is False
    # Opening a site is what a browser is for; web_fetch reaches the same address unasked.
    assert entry.requires_approval(type="browser", action="navigate", args={"url": "https://example.com/a"}) is False
    assert "Upload" in entry.requires_approval(type="browser", action="upload", args={"path": "/tmp/x"})


def test_a_click_that_opened_a_file_chooser_tells_the_model_how_to_answer_it():
    render = built_in_browser.render
    text = render({"ok": True, "done": "clicked e5", "tab": "t1", "url": "https://x.test/", "changes": "",
                   "file_chooser": {"multiple": False, "accept": ".pdf"}})
    assert "the page opened a file chooser for .pdf; upload_file(path) without element answers it" in text
    assert "file chooser" not in render({"ok": True, "done": "clicked e5", "tab": "t1"})


# ----------------------------------------------------------------- a machine behind an OSWorld server

def test_osworld_steps_become_pyautogui_with_literal_values():
    assert 'pyautogui.click(10, 20, button="right", clicks=2' in osworld._script(
        "click", {"x": 10.4, "y": 20, "mouse_button": "right", "click_count": 2})
    assert osworld._script("type_text", {"text": "a\"); import os; ("}) == \
        'import pyautogui; pyautogui.write("a\\"); import os; (", interval=0.02)'
    assert osworld._script("press_key", {"key": "ctrl+shift+t"}).endswith('hotkey(*["ctrl", "shift", "t"])')
    assert "pyautogui.scroll(-5)" in osworld._script("scroll", {"direction": "down", "pages": 1})
    with pytest.raises(errors.BadArgument):
        osworld._script("click", {"x": "left"})


def test_osworld_steps_execute_then_return_a_new_screenshot(monkeypatch):
    executed = []
    provider = osworld.OSWorldScreen(machine("lab", "Lab VM", screen={"kind": "osworld", "url": URL}))
    monkeypatch.setattr(provider, "_screenshot", lambda: PNG)
    monkeypatch.setattr(provider, "_execute", lambda script, sent: executed.append(script))
    monkeypatch.setattr(osworld, "time", SimpleNamespace(sleep=lambda seconds: None))
    result = provider.act({}, [("click", {"x": 1, "y": 2}), ("type_text", {"text": "x"})], Call("s", "s"))
    assert not result.is_error and result.images == [PNG] and len(executed) == 2
    assert result.text.startswith("Done:\n1. click\n2. type_text")


def test_a_set_up_machine_is_operated_without_asking_and_its_rule_names_the_machine(monkeypatch, listed):
    from openprogram.runtime.permissions.rule import parse_command
    install_machines(monkeypatch, [local(), machine("lab", "Lab VM", screen={"kind": "osworld", "url": URL})])
    call = {"device": "lab", "type": "screen", "action": "click", "args": {"x": 1, "y": 2}}
    assert parse_command("computer_use", call) == "lab screen"
    assert entry.requires_approval(**call) is False


def test_the_virtual_machine_setting_is_gone():
    from openprogram.state.settings import SETTINGS
    assert not [item.key for item in SETTINGS if item.key.startswith("computer_use.vm")]


def test_the_guides_ship_in_the_wheel():
    import fnmatch
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[5]
    patterns = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]["openprogram"]
    guides = sorted((root / "openprogram/programs/tools/computer_use/computer_use/guides").glob("*.md"))
    assert guides
    for path in guides:
        rel = path.relative_to(root / "openprogram").as_posix()
        assert any(fnmatch.fnmatch(rel, pattern) for pattern in patterns), rel


def test_opening_the_window_target_without_an_app_only_lists_apps(monkeypatch):
    import openprogram.runtime  # noqa: F401 - fills system_access's port
    from openprogram.host.system_access import _desktop_operation

    install(monkeypatch, AppProvider(machines.local_machine({}), "s", system="Darwin"))
    assert entry.requires_approval(type="window") is False
    assert entry.requires_approval(type="window", app="Notes")
    assert entry.requires_approval(type="window", args={"app": "Notes"})
    assert _desktop_operation("computer_use", {"type": "window"}) is None
    assert _desktop_operation("computer_use", {"type": "window", "app": "Notes"})
