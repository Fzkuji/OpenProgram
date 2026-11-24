"""The user's other browsers: the browser values a call takes, Safari through AppleScript, Firefox through
WebDriver BiDi, and the page layer both share. Every browser here is a fake: nothing starts or reaches one."""
from __future__ import annotations

import json
import queue
import re
from types import SimpleNamespace

import pytest

from openprogram.host.machines import local_machine
from openprogram.programs.applications.browser import catalog
from openprogram.programs.applications.browser import extension as extension_browser
from openprogram.programs.applications.browser import firefox, safari
from openprogram.programs.applications.browser import script_page as sp
from openprogram.programs.tools.computer_use.computer_use import dispatch, errors
from openprogram.programs.tools.computer_use.computer_use import tool as entry
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.desktop import macos
from openprogram.programs.tools.computer_use.computer_use.providers import built_in_browser, user_browser
from tests.support.screen_targets import user_browsers


# -- what a call may name --------------------------------------------------------------------

@pytest.mark.parametrize("app", ["safari", "Safari", "firefox", "Mozilla Firefox", "brave", "Brave Browser",
                                 "vivaldi", "opera", "arc", "chromium", "chrome-canary", "Microsoft Edge Beta", "edge"])
def test_every_users_browser_passes_the_combination_check(app):
    entry._check_combination({"type": "browser", "app": app})


def test_an_unknown_browser_is_refused_with_every_value_it_could_be():
    with pytest.raises(errors.BadArgument) as caught:
        entry._check_combination({"type": "browser", "app": "netscape"})
    said = caught.value.text()
    for name in catalog.NAMES:
        assert f'"{name}"' in said
    assert said.endswith("not app='netscape'.")


def test_the_description_and_parameters_list_every_browser_value():
    app = entry._PARAMETERS["properties"]["app"]["description"]
    for name in catalog.NAMES:
        assert f'"{name}"' in entry.DESCRIPTION and f'"{name}"' in app
    assert '"chromium" or "safari"' not in app and '"safari", "firefox"' not in app and app.count(" or ") == 1


# -- listing ---------------------------------------------------------------------------------

def test_safari_and_firefox_are_listed_as_targets_with_what_they_lack(monkeypatch):
    service = SimpleNamespace(extension=user_browsers("chrome", "brave", "safari", "firefox"))
    monkeypatch.setattr(built_in_browser, "service", lambda: service)
    found = {provider.target: provider for provider in built_in_browser.KIND.open(local_machine(), "s")}
    assert list(found) == ["browser", "browser chrome", "browser brave", "browser safari", "browser firefox"]
    brave, safari_target, firefox_target = found["browser brave"], found["browser safari"], found["browser firefox"]
    assert brave.describe().line.startswith("the user's own Brave with their sign-ins")
    assert "Playwright Extension" in brave.note()
    assert "upload" not in safari_target.capabilities and "upload" in firefox_target.capabilities
    lacks = {"upload_file", "read_console_messages", "read_network_requests", "list_panes"}
    assert not lacks & set(safari_target.extra_actions())
    assert {"upload_file", "read_console_messages"} <= set(firefox_target.extra_actions())
    assert "Allow JavaScript from Apple Events" in safari_target.note()
    assert "WebDriver BiDi" in firefox_target.note()
    assert safari_target.approval({}, [("click", {})]).rule == "browser safari"


def test_safari_is_reachable_only_on_a_mac_and_firefox_where_its_program_is(monkeypatch):
    monkeypatch.setattr(safari.sys, "platform", "linux")
    assert not safari.installed()
    monkeypatch.setattr(catalog, "executable", lambda name: "/usr/bin/firefox" if name == "firefox" else "")
    assert firefox.installed()
    monkeypatch.setattr(catalog, "executable", lambda name: "")
    assert not firefox.installed()


# -- the page layer --------------------------------------------------------------------------

_CALL = re.compile(r"return run\((\{.*\}), (.*)\);\n\}\)\(\)$", re.S)


class FakePage(sp.ScriptPage):
    """A backend whose page answers the library's actions from a table and records what it was asked."""

    browser, label = "safari", "Safari"
    lacks = dict(safari.SafariBrowser.lacks)

    def __init__(self, real_input=False):
        super().__init__()
        self.asked: list[dict] = []
        self.pointer: list[tuple] = []
        self.keys: list[tuple] = []
        self.real_input = real_input
        self.answers: dict[str, object] = {}

    def _tab(self):
        return "t1"

    def _evaluate(self, expression):
        found = _CALL.search(expression)
        assert found, expression[-200:]
        options = json.loads(found.group(1))
        self.asked.append(options)
        action = options["action"]
        if action in self.answers:
            return self.answers[action]
        if action == "snapshot":
            return {"url": "https://a.test/", "title": "A", "outline": '- button "Send" [ref=e5]'}
        if action == "viewport":
            return {"url": "https://a.test/", "title": "A", "ready": "complete", "visible": True, "width": 800,
                    "height": 600, "ratio": 2}
        if action == "point":
            return {"ok": True, "x": 40.5, "y": 20.0, "hidden": False}
        return {"ok": True}

    def _screenshot(self):
        return b"png", 2.0

    def _pointer(self, steps, modifiers):
        if self.real_input:
            self.pointer.append((steps, modifiers))
        return self.real_input

    def _keys(self, modifiers, key, seconds):
        if self.real_input:
            self.keys.append((modifiers, key, seconds))
        return self.real_input

    def actions(self):
        return [item["action"] for item in self.asked if item["action"] not in ("snapshot", "viewport", "point")]


def test_get_state_prints_the_outline_with_the_page_address_the_tab_row_reads():
    page = FakePage()
    result = page.run("get_state", {"include": "all"})
    assert result["ok"] and result["_images"] == [b"png"]
    assert extension_browser.ExtensionBrowser.page_url(result["result"]) == "https://a.test/"
    assert extension_browser.ExtensionBrowser.page_title(result["result"]) == "A"
    assert '- button "Send" [ref=e5]' in result["result"]


def test_a_click_by_element_runs_in_the_page_without_real_input():
    page = FakePage()
    result = page.run("click", {"element": "e5", "click_count": 2, "modifiers": "shift"})
    assert result["ok"], result
    click = next(item for item in page.asked if item["action"] == "click")
    assert click == {"action": "click", "element": "e5", "button": "left", "count": 2, "flags": {"shiftKey": True}}
    assert "### Ran" in result["result"] and "[ref=e5]" in result["result"]


def test_x_and_y_are_pixels_of_the_last_screenshot():
    page = FakePage()
    page.run("get_state", {"include": "screenshot"})
    page.run("click", {"x": 200, "y": 100})
    click = next(item for item in page.asked if item["action"] == "click")
    assert (click["x"], click["y"]) == (100, 50)


def test_with_real_input_a_click_is_pointer_actions_at_the_elements_centre():
    page = FakePage(real_input=True)
    assert page.run("click", {"element": "e5", "mouse_button": "right"})["ok"]
    steps, modifiers = page.pointer[0]
    assert steps == [{"type": "pointerMove", "x": 40.5, "y": 20.0}, {"type": "pointerDown", "button": 2},
                     {"type": "pointerUp", "button": 2}]
    assert "click" not in page.actions()


def test_holding_needs_real_input():
    result = FakePage().run("click", {"x": 1, "y": 1, "seconds": 2})
    assert result["reason_code"] == "unsupported_action" and "holding the button" in result["message"]
    page = FakePage(real_input=True)
    assert page.run("click", {"x": 1, "y": 1, "seconds": 2})["ok"]
    assert {"type": "pause", "duration": 2000} in page.pointer[0][0]


def test_keys_without_real_input_are_page_events_named_as_the_dom_names_them():
    page = FakePage()
    page.answers["key"] = {"ok": True, "default": "select all"}
    result = page.run("press_key", {"key": "ctrl+a"})
    key = next(item for item in page.asked if item["action"] == "key")
    assert key == {"action": "key", "key": "a", "code": "KeyA", "modifiers": ["Control"]}
    assert "as page events: select all" in result["result"]


def test_type_text_with_submit_types_then_presses_return():
    page = FakePage(real_input=True)
    assert page.run("type_text", {"element": "e3", "text": "Ada", "clear": True, "submit": True})["ok"]
    typed = next(item for item in page.asked if item["action"] == "type")
    assert typed == {"action": "type", "element": "e3", "text": "Ada", "clear": True}
    assert page.keys == [([], "Enter", 0.0)]


def test_set_value_sends_the_values_and_whether_a_box_ends_up_checked():
    page = FakePage()
    page.run("set_value", {"role": "combobox", "name": "Color", "value": ["Red", "Blue"]})
    page.run("set_value", {"element": "e7", "value": "on"})
    first, second = [item for item in page.asked if item["action"] == "set_value"]
    assert first["values"] == ["Red", "Blue"] and first["role"] == "combobox" and first["name"] == "Color"
    assert second["checked"] is True and second["text"] == "on"


def test_scroll_sends_the_direction_and_amount_the_page_measures():
    page = FakePage()
    page.answers["scroll"] = {"ok": True, "moved": True}
    page.run("scroll", {"direction": "up", "pages": 2})
    scroll = next(item for item in page.asked if item["action"] == "scroll")
    assert (scroll["direction"], scroll["pages"], scroll["pixels"], scroll["share"]) == ("up", 2.0, None, 0.9)
    assert page.run("scroll", {"direction": "sideways"})["reason_code"] == "invalid_direction"


def test_an_element_the_page_no_longer_has_is_element_gone():
    page = FakePage()
    page.answers["click"] = {"ok": False, "reason_code": "element_gone", "message": "element e9 is no longer on the page"}
    result = page.run("click", {"element": "e9"})
    assert result == {"ok": False, "reason_code": "element_gone", "message": "element e9 is no longer on the page"}


def test_an_action_the_browser_lacks_says_why():
    result = FakePage().run("upload_file", {"path": "/tmp/a.txt"})
    assert result["reason_code"] == "unsupported_action" and "Safari" in result["message"]


def test_evaluate_calls_the_script_with_the_element_and_returns_its_value():
    page = FakePage()
    page.answers["evaluate"] = {"answer": 42}
    seen = []
    page._evaluate = lambda expression, real=page._evaluate: seen.append(expression) or real(expression)
    result = page.run("evaluate", {"script": "(el) => el.id", "element": "e5"})
    assert result["result"].startswith('### Result\n{"answer": 42}')
    assert re.search(r"return run\(\{\"action\": \"evaluate\", \"element\": \"e5\"\}, .*\(el\) => el\.id", seen[0], re.S)


def test_chords_become_dom_key_names():
    assert sp.chord("Return") == ([], "Enter")
    assert sp.chord("ctrl+shift+Tab") == (["Control", "Shift"], "Tab")
    assert sp.key_code("a") == "KeyA" and sp.key_code("Enter") == "Enter" and sp.key_code("7") == "Digit7"


def test_the_page_script_carries_the_shared_scripts_and_the_options():
    script = sp.page_script({"action": "click", "element": "e1", "text": "“q”"})
    assert "const POINTER = (element, o)" in script.replace("\n", "") or "const POINTER = " in script
    assert '"text": "“q”"' in script and script.endswith("})()")
    assert "__POINTER__" not in script and "__MAX__" not in script


# -- Safari ------------------------------------------------------------------------------------

def test_applescript_strings_are_escaped():
    assert safari.applescript_string('a "b" \\c') == '"a \\"b\\" \\\\c"'
    script = safari.create_script('https://x.test/?q="1"', visible=False)
    assert 'make new document with properties {URL:"https://x.test/?q=\\"1\\""}' in script
    assert "set current tab to (make new tab" in safari.create_script("https://x.test", visible=True)


def test_a_tab_script_names_its_window_and_place_and_carries_the_expression_as_json():
    script = safari.run_script(7, 2, 'document.title + "\\n"', "t1")
    assert 'Application("Safari").windows.byId(7).tabs[1]' in script
    assert "doJavaScript(" in script and "delay(0.05)" in script
    assert json.dumps('document.title + "\\n"')[1:-1] in script
    assert safari.navigate_script(7, 2, "https://b.test") == (
        '(() => { Application("Safari").windows.byId(7).tabs[1].url = "https://b.test"; return "ok"; })()')


@pytest.mark.parametrize("said, kind, where", [
    ("execution error: Not authorized to send Apple events to Safari. (-1743)", extension_browser.Unavailable,
     "Automation"),
    ("Error: You must enable the 'Allow JavaScript from Apple Events' option in Safari's Develop menu to use "
     "'do JavaScript'. (8)", extension_browser.Unavailable, "Allow JavaScript from Apple Events"),
    ("Error: Error: Can't get object. (-1728)", extension_browser.Lost, ""),
    ("Error: Application isn't running. (-600)", extension_browser.Lost, ""),
    ("Error: SyntaxError: Unexpected token", sp.ScriptError, ""),
])
def test_osascript_errors_say_what_the_user_turns_on(said, kind, where):
    error = safari.failure(said)
    assert isinstance(error, kind)
    assert where in getattr(error, "permission", "")


def test_osascript_failures_are_raised_as_what_they_mean(monkeypatch):
    def run(command, **options):
        assert command == ["/usr/bin/osascript", "-l", "JavaScript", "-"] and options["input"] == "1"
        return SimpleNamespace(returncode=1, stdout="", stderr="Not authorized to send Apple events (-1743)")
    monkeypatch.setattr(safari.subprocess, "run", run)
    with pytest.raises(extension_browser.Unavailable) as caught:
        safari.osascript("1")
    assert caught.value.permission == safari.NOT_ALLOWED


def _rows(*tabs):
    return [{"window": window, "index": index, "url": url} for window, index, url in tabs]


def test_a_safari_tab_keeps_its_key_when_tabs_before_it_close_and_when_it_navigates():
    names = iter(f"s{number}" for number in range(1, 99))
    make = lambda: next(names)
    first = safari.track([], _rows((1, 1, "a"), (1, 2, "b"), (1, 3, "c")), make)
    assert [item.key for item in first] == ["s1", "s2", "s3"]
    closed = safari.track(first, _rows((1, 1, "b"), (1, 2, "c")), make)
    assert [item.key for item in closed] == ["s2", "s3"]
    moved = safari.track(closed, _rows((1, 1, "b"), (1, 2, "d")), make)
    assert [item.key for item in moved] == ["s2", "s3"]
    opened = safari.track(moved, _rows((1, 1, "b"), (1, 2, "d"), (1, 3, "d"), (2, 1, "e")), make)
    assert [item.key for item in opened] == ["s2", "s3", "s4", "s5"]


@pytest.fixture
def fake_safari(monkeypatch):
    """Safari with two windows; the scripts osascript would run are recorded and answered here."""
    monkeypatch.setattr(safari, "installed", lambda: True)
    monkeypatch.setattr(safari, "_seen", [])
    monkeypatch.setattr(safari, "_selected", {})
    monkeypatch.setattr(safari, "_sessions", set())
    state = {"rows": [
        {"window": 7, "index": 1, "url": "https://a.test/", "title": "A", "visible": False, "front": True},
        {"window": 7, "index": 2, "url": "https://b.test/", "title": "B", "visible": True, "front": True},
        {"window": 9, "index": 1, "url": "https://c.test/", "title": "C", "visible": True, "front": False}],
        "scripts": [], "js_off": False}

    def osascript(script, language="JavaScript", timeout=0):
        state["scripts"].append(script)
        if script == safari.list_script():
            return json.dumps(state["rows"])
        if "doJavaScript" in script:
            if state["js_off"]:
                raise safari.failure("You must enable 'Allow JavaScript from Apple Events' (8)")
            if '\\"action\\": \\"snapshot\\"' in script:
                return json.dumps({"value": {"url": "https://b.test/", "title": "B", "outline": "- text: \"hi\""}})
            return json.dumps({"value": {"ok": True, "url": "https://b.test/", "ready": "complete"}})
        return "ok"
    monkeypatch.setattr(safari, "osascript", osascript)
    return state


def test_safari_lists_every_tab_and_acts_on_the_front_one_until_another_is_selected(fake_safari):
    browser = safari.SafariBrowser("s")
    rows = browser.tabs()
    assert [(row["url"], row["current"]) for row in rows] == [
        ("https://a.test/", False), ("https://b.test/", True), ("https://c.test/", False)]
    browser._evaluate("1")
    assert "windows.byId(7).tabs[1]" in fake_safari["scripts"][-1]
    assert browser.select(2, "https://c.test/")
    browser._evaluate("1")
    assert "windows.byId(9).tabs[0]" in fake_safari["scripts"][-1]
    assert not browser.select(0, "https://elsewhere.test/")
    assert safari.connected("s") and not safari.connected("other")
    safari.close_session("s")
    assert not safari.connected("s")


def test_safari_with_its_scripting_off_answers_permission_missing_with_where_to_turn_it_on(fake_safari, monkeypatch):
    fake_safari["js_off"] = True
    monkeypatch.setattr(user_browser, "_tables", {})
    monkeypatch.setattr(user_browser, "_current", {})
    monkeypatch.setattr(user_browser, "_users", {})
    monkeypatch.setattr(user_browser, "_turns", {})
    from openprogram.host._platform import front
    monkeypatch.setattr(front, "kept", lambda operated: __import__("contextlib").nullcontext())
    provider = user_browser.UserBrowser(SimpleNamespace(extension=user_browsers("safari")), "safari")
    result = dispatch.run_steps(provider, {}, [("get_state", {"include": "tree"})], Call("s", "a1"))
    assert result.is_error and result.text.startswith("PERMISSION_MISSING: Safari's \"Allow JavaScript from Apple ")
    assert "Develop" in result.text and "Nothing was sent." in result.text
    fake_safari["js_off"] = False
    result = dispatch.run_steps(provider, {}, [("get_state", {"include": "tree"})], Call("s", "a1"))
    assert not result.is_error, result.text
    assert result.text.startswith("tab t") and "· B · https://b.test/" in result.text.splitlines()[0]


# -- Firefox -----------------------------------------------------------------------------------

def test_pointer_actions_hold_modifiers_across_the_same_ticks():
    built = firefox.pointer_actions("c1", [{"type": "pointerMove", "x": 10.4, "y": 20.6},
                                           {"type": "pointerDown", "button": 0}, {"type": "pointerUp", "button": 0}],
                                    ["Shift"])
    assert built["context"] == "c1"
    pointer, keys = built["actions"]
    assert pointer["type"] == "pointer" and pointer["parameters"] == {"pointerType": "mouse"}
    assert len(pointer["actions"]) == len(keys["actions"]) == 5
    assert pointer["actions"][1] == {"type": "pointerMove", "x": 10, "y": 21}
    assert keys["actions"][0] == {"type": "keyDown", "value": ""}
    assert keys["actions"][-1] == {"type": "keyUp", "value": ""}


def test_key_actions_press_the_chord_and_let_go_in_reverse():
    built = firefox.key_actions("c1", ["Control"], "Enter", 0.5)
    assert built["actions"][0]["actions"] == [
        {"type": "keyDown", "value": ""}, {"type": "keyDown", "value": ""},
        {"type": "pause", "duration": 500}, {"type": "keyUp", "value": ""}, {"type": "keyUp", "value": ""}]
    assert firefox.key_value("a") == "a" and firefox.key_value("F5") == ""


def test_firefox_is_started_in_the_background_with_its_agent_on(monkeypatch):
    monkeypatch.setattr(firefox.sys, "platform", "darwin")
    assert firefox.start_command("/Applications/Firefox.app/Contents/MacOS/firefox") == [
        "/usr/bin/open", "-g", "-a", "/Applications/Firefox.app", "--args", "--remote-debugging-port", "9222"]
    monkeypatch.setattr(firefox.sys, "platform", "linux")
    assert firefox.start_command("/usr/bin/firefox") == ["/usr/bin/firefox", "--remote-debugging-port", "9222"]


def test_a_firefox_open_without_its_agent_is_not_quit_and_the_call_says_what_to_do(monkeypatch):
    monkeypatch.setattr(firefox, "_connection", None)
    monkeypatch.setattr(firefox, "installed", lambda: True)
    monkeypatch.setattr(firefox, "listening", lambda port=9222: False)
    monkeypatch.setattr(firefox, "running", lambda: True)
    monkeypatch.setattr(firefox, "_start", lambda: pytest.fail("Firefox must not be started"))
    with pytest.raises(extension_browser.Unavailable) as caught:
        firefox.FirefoxBrowser("s")
    assert caught.value.permission == firefox.NOT_REMOTE and "--remote-debugging-port 9222" in firefox.NOT_REMOTE


class FakeAgent:
    """Firefox's remote agent behind a websocket: answers each command, records it, and can send events."""

    def __init__(self):
        self.sent: list[dict] = []
        self.inbox: queue.Queue = queue.Queue()
        self.contexts = [{"context": "c1", "url": "https://a.test/"}, {"context": "c2", "url": "https://b.test/"}]

    def connect(self, url, **options):
        assert url == "ws://127.0.0.1:9222/session"
        return self

    def __iter__(self):
        while True:
            item = self.inbox.get()
            if item is None:
                return
            yield item

    def send(self, text):
        message = json.loads(text)
        self.sent.append(message)
        method, params = message["method"], message["params"]
        result: dict = {}
        if method == "browsingContext.getTree":
            result = {"contexts": self.contexts}
        elif method == "script.evaluate":
            if params["expression"].startswith("JSON.stringify([document.title"):
                focused = params["target"]["context"] == "c2"
                value = json.dumps(["B" if focused else "A", "visible" if focused else "hidden", focused])
            elif '"action": "point"' in params["expression"]:
                value = json.dumps({"ok": True, "x": 12, "y": 34, "hidden": False})
            elif '"action": "snapshot"' in params["expression"]:
                value = json.dumps({"url": "https://b.test/", "title": "B", "outline": "- text: \"hi\""})
            elif '"action": "viewport"' in params["expression"]:
                value = json.dumps({"ratio": 2, "visible": True, "url": "https://b.test/", "ready": "complete"})
            else:
                value = json.dumps({"ok": True})
            result = {"type": "success", "result": {"type": "string", "value": value}}
        elif method == "browsingContext.captureScreenshot":
            result = {"data": "cG5n"}
        elif method == "browsingContext.create":
            result = {"context": "c3"}
        self.inbox.put(json.dumps({"type": "success", "id": message["id"], "result": result}))

    def event(self, method, params):
        self.inbox.put(json.dumps({"type": "event", "method": method, "params": params}))

    def close(self):
        self.inbox.put(None)

    def methods(self):
        return [message["method"] for message in self.sent if message["method"] != "script.evaluate"]


@pytest.fixture
def fake_firefox(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(firefox, "_connection", None)
    monkeypatch.setattr(firefox, "_selected", {})
    monkeypatch.setattr(firefox, "_sessions", set())
    monkeypatch.setattr(firefox, "installed", lambda: True)
    monkeypatch.setattr(firefox, "listening", lambda port=9222: True)
    monkeypatch.setattr(sp, "SETTLE_SECONDS", 0)
    yield agent
    agent.close()


def test_firefox_opens_a_session_and_acts_on_the_focused_tab_with_real_input(fake_firefox):
    browser = firefox.FirefoxBrowser("s", connect=fake_firefox.connect)
    assert fake_firefox.methods()[:2] == ["session.new", "session.subscribe"]
    rows = browser.tabs()
    assert [(row["key"], row["title"], row["current"]) for row in rows] == [("c1", "A", False), ("c2", "B", True)]
    result = browser.run("click", {"element": "e5"})
    assert result["ok"], result
    performed = next(message for message in fake_firefox.sent if message["method"] == "input.performActions")
    assert performed["params"]["context"] == "c2"
    assert performed["params"]["actions"][0]["actions"][0] == {"type": "pointerMove", "x": 12, "y": 34}
    assert fake_firefox.methods()[-1] == "input.releaseActions"


def test_firefox_screenshots_navigation_and_tabs_are_bidi_commands(fake_firefox):
    browser = firefox.FirefoxBrowser("s", connect=fake_firefox.connect)
    browser.tabs()
    state = browser.run("get_state", {"include": "all"})
    assert state["_images"] == [b"png"] and browser._scales["c2"] == 2
    browser.run("navigate", {"url": "https://n.test/"})
    navigate = next(message for message in fake_firefox.sent if message["method"] == "browsingContext.navigate")
    assert navigate["params"] == {"context": "c2", "url": "https://n.test/", "wait": "complete"}
    browser.run("create_tab", {"url": "https://new.test/"})
    created = next(message for message in fake_firefox.sent if message["method"] == "browsingContext.create")
    assert created["params"] == {"type": "tab", "background": True}
    assert browser._tab() == "c3"
    browser.run("back", {})
    assert fake_firefox.sent[-1]["method"] != "browsingContext.traverseHistory"
    assert any(message["method"] == "browsingContext.traverseHistory" and message["params"]["delta"] == -1
               for message in fake_firefox.sent)


def test_firefox_keeps_the_console_and_requests_of_each_tab(fake_firefox):
    browser = firefox.FirefoxBrowser("s", connect=fake_firefox.connect)
    browser.tabs()
    fake_firefox.event("log.entryAdded", {"level": "error", "text": "boom", "source": {"context": "c2"}})
    fake_firefox.event("log.entryAdded", {"level": "info", "text": "fine", "source": {"context": "c2"}})
    fake_firefox.event("network.responseCompleted", {"context": "c2", "request": {"url": "https://b.test/x",
                                                                                   "method": "GET"},
                                                     "response": {"status": 404}})
    browser.tabs()  # a round trip, so the events above have been read
    assert browser.run("read_console_messages", {"only_errors": True})["result"] == "[error] boom"
    assert browser.run("read_network_requests", {"only_errors": True})["result"] == "GET https://b.test/x → 404"


def test_the_last_conversation_ends_the_session_and_leaves_firefox_open(fake_firefox):
    firefox.FirefoxBrowser("s", connect=fake_firefox.connect)
    assert firefox.connected("s")
    firefox.close_session("s")
    assert fake_firefox.methods()[-1] == "session.end"
    assert firefox._connection is None


# -- the window of a browser --------------------------------------------------------------------

def test_a_browser_name_finds_its_mac_window_application(monkeypatch):
    apps = [SimpleNamespace(isTerminated=lambda: False, processIdentifier=lambda: 41,
                            bundleIdentifier=lambda: "com.google.Chrome", localizedName=lambda: "Google Chrome",
                            bundleURL=lambda: None),
            SimpleNamespace(isTerminated=lambda: False, processIdentifier=lambda: 42,
                            bundleIdentifier=lambda: "com.apple.Safari", localizedName=lambda: "Safari",
                            bundleURL=lambda: None)]
    monkeypatch.setattr(macos, "_appkit", lambda: object())
    monkeypatch.setattr(macos, "running_applications", lambda appkit: apps)
    computer = macos.MacComputer()
    assert computer._running_match_once("safari") is apps[1]
    assert computer._running_match_once("SAFARI") is apps[1]
    assert computer._running_match_once("chrome") is apps[0]
    assert computer._running_match_once("brave") is None
