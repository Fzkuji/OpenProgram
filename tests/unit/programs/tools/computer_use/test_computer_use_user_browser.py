"""Targets chrome and edge: the user's own browser, with the browser family's actions and stable tab ids."""
import itertools
import json
from types import SimpleNamespace

import pytest

from openprogram.host.machines import local_machine
from openprogram.programs.applications.browser import extension as extension_browser
from openprogram.programs.tools.computer_use.computer_use.routing import dispatch, locks
from openprogram.programs.tools.computer_use.computer_use.contract import Approval, Call
from openprogram.programs.tools.computer_use.computer_use.providers.browser import (built_in as built_in_browser,
    user as user_browser)
from tests.support.screen_targets import user_browsers
from openprogram.programs.tools.computer_use.computer_use.providers.browser import tabs as browser_tabs


def _reply(text, error=False, image=None):
    content = [SimpleNamespace(type="text", text=text)]
    if image:
        content.append(SimpleNamespace(type="image", data=image))
    return SimpleNamespace(content=content, isError=error)


class FakeExtension:
    """Playwright MCP in extension mode: the attached tabs, the selected one, and every call it got."""

    def __init__(self, *urls):
        self._keys = itertools.count(1)
        self.pages = [self._page(url) for url in urls or ("https://a.test/",)]
        self.selected = 0
        self.calls = []
        self.failing = {}

    def _page(self, url):
        return {"key": f"k{next(self._keys)}", "url": url, "title": url.split("//")[-1].strip("/")}

    def reconnect(self):
        """The extension connected again: the same tabs, on new page objects."""
        for page in self.pages:
            page["key"] = f"k{next(self._keys)}"

    def is_alive(self):
        return True

    def _tabs(self):
        return "\n".join(f"- {index}:{' (current)' if index == self.selected else ''} [{page['title']}]({page['url']})"
                         for index, page in enumerate(self.pages))

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name in self.failing:
            failure = self.failing[name]
            if isinstance(failure, Exception):
                raise failure
            return _reply(failure, error=True)
        if name == "browser_run_code_unsafe":
            rows = [{**page, "current": index == self.selected} for index, page in enumerate(self.pages)]
            return _reply("### Result\n" + json.dumps(rows))
        if name == "browser_tabs":
            if arguments["action"] == "new":
                self.pages.append(self._page(arguments["url"]))
                self.selected = len(self.pages) - 1
            elif arguments["action"] == "select":
                self.selected = arguments["index"]
            elif arguments["action"] == "close":
                self.pages.pop(arguments["index"])
                self.selected = min(self.selected, len(self.pages) - 1)
            return _reply("### Open tabs\n" + self._tabs())
        if name == "browser_evaluate":
            function = arguments.get("function", "")
            if function.endswith("visibilityState"):
                return _reply('### Result\n"visible"')
            if "HTMLSelectElement" in function:
                return _reply('### Result\n"select"')
            return _reply("### Result\ntrue")
        if name == "browser_take_screenshot":
            return _reply("### Result\nscreenshot", image="aGk=")
        page = self.pages[self.selected]
        return _reply(f"### Page\n- Page URL: {page['url']}\n- Page Title: {page['title']}\nran {name}")

    def acts(self):
        """The calls that act on a page: without listing tabs and the check whether a tab is in front."""
        return [(name, arguments) for name, arguments in self.calls
                if name != "browser_run_code_unsafe" and not arguments.get("function", "").endswith("visibilityState")]


@pytest.fixture
def connect(monkeypatch):
    monkeypatch.setattr(browser_tabs, "_tables", {})
    monkeypatch.setattr(browser_tabs, "_current", {})
    monkeypatch.setattr(browser_tabs, "_users", {})
    monkeypatch.setattr(browser_tabs, "_turns", {})
    # Ending a connection runs on a thread of its own; here it runs in place.
    monkeypatch.setattr(user_browser, "_in_background", lambda target, *args: target(*args))
    monkeypatch.setattr(extension_browser.shutil, "which", lambda name: "/usr/local/bin/npx")
    clients = {}
    monkeypatch.setattr(extension_browser, "_clients", clients)

    def connect(name="chrome", *urls):
        fake = FakeExtension(*urls)
        clients[("s", name)] = fake
        return user_browser.UserBrowser(SimpleNamespace(extension=extension_browser), name), fake
    return connect


def _run(provider, steps, subject=None, agent="a1", images=True):
    return dispatch.run_steps(provider, subject or {}, steps, Call("s", agent, images=images))


def test_a_browser_is_listed_where_its_extension_is_installed_without_connecting(monkeypatch):
    service = SimpleNamespace(extension=user_browsers("edge"))
    monkeypatch.setattr(built_in_browser, "service", lambda: service)
    found = built_in_browser._open(local_machine(), "s")
    assert [provider.target for provider in found] == ["browser", "browser edge"]
    assert found[1].address() == 'type="browser", app="edge"'
    assert found[1].describe().line.startswith("the user's own Microsoft Edge with their sign-ins")


def test_the_extension_counts_as_installed_where_a_profile_holds_it(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser import catalog, firefox, safari
    monkeypatch.setattr(extension_browser, "_data_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(safari, "installed", lambda: False)
    monkeypatch.setattr(firefox, "installed", lambda: False)
    # Brave is found by its executable; Opera's is missing, so its extension alone does not list it.
    monkeypatch.setattr(catalog, "executable", lambda name: "/opt/brave/brave" if name == "brave" else "")
    (tmp_path / "chrome" / "Profile 3" / "Extensions" / extension_browser.EXTENSION_ID).mkdir(parents=True)
    (tmp_path / "edge" / "Default" / "Extensions" / "someotherextension").mkdir(parents=True)
    (tmp_path / "brave" / "Default" / "Extensions" / extension_browser.EXTENSION_ID).mkdir(parents=True)
    # Opera keeps its one profile in the user-data folder itself.
    (tmp_path / "opera" / "Extensions" / extension_browser.EXTENSION_ID).mkdir(parents=True)
    assert extension_browser.installed_browsers() == ("chrome", "brave")
    assert extension_browser._command("edge")[-6:-4] == ["--browser", "msedge"]
    assert extension_browser._command("chrome-canary")[-6:-4] == ["--browser", "chrome-canary"]


def test_a_chromium_browser_without_a_channel_is_started_from_its_executable_and_profile_folder(tmp_path, monkeypatch):
    from openprogram.programs.applications.browser import catalog
    monkeypatch.setattr(catalog, "executable", lambda name: f"/opt/{name}/{name}")
    monkeypatch.setattr(extension_browser, "_data_dir", lambda name: tmp_path / name)
    monkeypatch.setattr(extension_browser, "token", lambda: "")
    command = extension_browser._command("vivaldi")
    assert command[command.index("--extension") + 1:command.index("--caps")] == [
        "--browser", "chrome", "--executable-path", "/opt/vivaldi/vivaldi"]
    # Playwright MCP 0.0.79 finds the profile holding the extension only in this folder.
    assert extension_browser._environment("vivaldi")["PWTEST_EXTENSION_USER_DATA_DIR"] == str(tmp_path / "vivaldi")
    assert "PWTEST_EXTENSION_USER_DATA_DIR" not in extension_browser._environment("edge")


def test_every_browser_app_is_in_the_catalog_with_a_label_and_an_engine():
    from openprogram.programs.applications.browser import catalog
    from openprogram.programs.tools.computer_use.computer_use.routing import registry
    assert registry.BROWSER_APPS == catalog.NAMES
    for name in catalog.NAMES:
        browser = catalog.get(name)
        assert browser.label and browser.engine in ("chromium", "safari", "firefox") and browser.bundles
        assert browser.engine != "chromium" or browser.channel or browser.executables
        assert name == name.lower()
    assert extension_browser.label("brave") == "Brave" and extension_browser.engine("safari") == "safari"


def test_the_family_actions_reach_the_users_chrome_and_typing_inserts(connect):
    chrome, fake = connect()
    result = _run(chrome, [("create_tab", {"url": "https://example.com"}), ("click", {"element": "e5"}),
                           ("type_text", {"element": "e7", "text": "hi"}), ("press_key", {"key": "ctrl+Return"})])
    assert not result.is_error, result.text
    names = [name for name, _ in fake.acts()]
    assert names == ["browser_tabs", "browser_click", "browser_evaluate", "browser_press_key"]
    assert fake.acts()[1] == ("browser_click", {"target": "e5"})
    assert fake.acts()[2][1]["target"] == "e7" and '"hi"' in fake.acts()[2][1]["function"]
    assert fake.acts()[3] == ("browser_press_key", {"key": "Control+Enter"})
    assert "4 of 4 steps done." in result.text


def test_clear_replaces_a_fields_text(connect):
    chrome, fake = connect()
    assert not _run(chrome, [("type_text", {"element": "e7", "text": "hi", "clear": True, "submit": True})]).is_error
    assert fake.acts() == [("browser_type", {"target": "e7", "text": "hi"}), ("browser_press_key", {"key": "Enter"})]


def test_tab_ids_stay_the_same_when_another_tab_closes_and_when_the_extension_reconnects(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/", "https://c.test/")
    listed = _run(chrome, [("list_tabs", {})]).json_data["tabs"]
    first, second, third = (row["tab"] for row in listed)
    assert len({first, second, third}) == 3 and all(tab.startswith("t") for tab in (first, second, third))
    assert not _run(chrome, [("close_tab", {})], {"tab": first}).is_error
    assert fake.calls[-1] == ("browser_tabs", {"action": "close", "index": 0})
    assert [row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"]] == [second, third]
    fake.reconnect()
    result = _run(chrome, [("get_state", {})], {"tab": third})
    assert result.text.startswith(f"tab {third} · c.test · https://c.test/")
    assert ("browser_tabs", {"action": "select", "index": 1}) in fake.calls


def test_each_agent_keeps_its_own_current_tab(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    first, second = (row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"])
    _run(chrome, [("click", {"element": "e1"})], {"tab": second}, agent="a1")
    _run(chrome, [("click", {"element": "e1"})], {"tab": first}, agent="a2")
    result = _run(chrome, [("click", {"element": "e2"})], agent="a1")
    assert result.text.startswith(f"tab {second} ·")
    assert fake.selected == 1
    # An agent that never chose acts on the tab the extension has selected.
    assert _run(chrome, [("get_state", {})], agent="a3").text.startswith(f"tab {second} ·")


def test_a_step_without_tab_never_moves_to_another_tab_when_the_agents_tab_lost_its_id(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://a.test/", "https://b.test/")
    first, _, third = (row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"])
    _run(chrome, [("click", {"element": "e1"})], {"tab": first})
    fake.selected = 2
    # Two tabs at one address cannot be told apart after reconnecting: both get new ids.
    fake.reconnect()
    acted = len(fake.acts())
    result = _run(chrome, [("type_text", {"element": "e1", "text": "x"})])
    assert result.text.startswith(f"BAD_ARGUMENT: tab {first}, the one you used last here, is no longer open")
    assert f"{third} (b.test)" in result.text and len(fake.acts()) == acted
    assert _run(chrome, [("click", {"element": "e1"})], {"tab": third}).text.startswith(f"tab {third} ·")
    assert _run(chrome, [("click", {"element": "e2"})]).text.startswith(f"tab {third} ·")


def test_an_agent_whose_tab_it_closed_goes_back_to_the_selected_tab(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    first, second = (row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"])
    _run(chrome, [("click", {"element": "e1"})], {"tab": second})
    assert not _run(chrome, [("close_tab", {})], {"tab": second}).is_error
    assert _run(chrome, [("get_state", {})]).text.startswith(f"tab {first} ·")


def test_another_agent_closing_the_tab_an_agent_used_leaves_it_bad_argument(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    first, second = (row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"])
    _run(chrome, [("click", {"element": "e1"})], {"tab": second}, agent="a2")
    assert not _run(chrome, [("close_tab", {})], {"tab": second}, agent="a1").is_error
    acted = len(fake.acts())
    result = _run(chrome, [("click", {"element": "e1"})], agent="a2")
    assert result.text.startswith(f"BAD_ARGUMENT: tab {second}, the one you used last here, is no longer open")
    assert len(fake.acts()) == acted


def test_listing_after_the_agents_tab_is_gone_marks_no_tab_current(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    first, second = (row["tab"] for row in _run(chrome, [("list_tabs", {})]).json_data["tabs"])
    _run(chrome, [("click", {"element": "e1"})], {"tab": second})
    fake.pages.pop(1)
    fake.selected = 0
    result = _run(chrome, [("list_tabs", {})])
    assert f"(tab {second}, the one you used last, is gone; pass title on every call)" in result.text
    assert "(current)" not in result.text and not any(row.get("current") for row in result.json_data["tabs"])
    # Before the agent chose, text and data both mark the tab a step without tab would use.
    fresh = _run(chrome, [("list_tabs", {})], agent="a2")
    assert f"- {first} (current):" in fresh.text and fresh.json_data["tabs"][0]["current"] is True


def test_a_connection_left_with_no_attached_tab_sends_nothing(connect, monkeypatch):
    chrome, fake = connect()
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    original = fake.call

    def call(name, arguments):
        if name == "browser_run_code_unsafe":
            fake.calls.append((name, arguments))
            return _reply("### Result\nNo open tabs. Navigate to a URL to create one.")
        return original(name, arguments)
    fake.call = call
    for steps in ([("click", {"element": "e1"})], [("list_tabs", {})]):
        result = _run(chrome, steps)
        assert result.text.startswith("MACHINE_UNREACHABLE: ") and "lost its attached tab" in result.text
        assert result.json_data == {"action_dispatched": False}
    assert "browser_click" not in [name for name, _ in fake.acts()]


def test_a_tab_lost_while_selecting_or_after_create_tab_is_coded(connect, monkeypatch):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    second = _run(chrome, [("list_tabs", {})]).json_data["tabs"][1]["tab"]
    original = fake.call
    lost = {"browser_tabs"}

    def call(name, arguments):
        if name in lost:
            fake.calls.append((name, arguments))
            return _reply("### Result\nNo open tabs. Navigate to a URL to create one.")
        if name == "browser_run_code_unsafe" and "after" in lost:
            fake.calls.append((name, arguments))
            return _reply("### Result\nNo open tabs. Navigate to a URL to create one.")
        return original(name, arguments)
    fake.call = call
    result = _run(chrome, [("click", {"element": "e1"})], {"tab": second})
    assert result.text.startswith("MACHINE_UNREACHABLE: ") and "while selecting tab 1" in result.text
    assert "browser_click" not in [name for name, _ in fake.acts()]
    lost.clear()
    lost.add("after")
    result = _run(chrome, [("create_tab", {"url": "https://c.test/"})])
    assert result.text.startswith("OUTCOME_UNKNOWN: ") and "lost its attached tab while listing tabs" in result.text


def test_a_call_waiting_for_another_agents_turn_answers_screen_busy(connect, monkeypatch):
    chrome, fake = connect()
    monkeypatch.setattr(user_browser.locks, "WAIT_SECONDS", 0.0)
    turn = browser_tabs._turn("s", "chrome")
    turn.acquire()
    try:
        result = _run(chrome, [("click", {"element": "e1"})], agent="a2")
    finally:
        turn.release()
    assert result.text.startswith("SCREEN_BUSY: another agent has been operating type=\"browser\", app=\"chrome\"")
    assert result.json_data == {"action_dispatched": False} and fake.calls == []


def test_a_number_or_an_unknown_id_answers_with_the_open_tabs(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    for named in ("1", "tzzzz"):
        result = _run(chrome, [("click", {"element": "e1"})], {"tab": named})
        assert result.is_error and result.text.startswith("BAD_ARGUMENT: ") and "Open tabs: t" in result.text
    assert [name for name, _ in fake.acts()] == []


def test_close_tab_closes_only_a_tab_the_call_names(connect):
    chrome, fake = connect()
    result = _run(chrome, [("close_tab", {})])
    assert result.is_error and result.text.startswith("BAD_ARGUMENT: close_tab closes only a tab the call names")
    assert result.json_data == {"action_dispatched": False}
    assert fake.acts() == []


def test_a_browser_that_does_not_connect_answers_with_its_code_and_sends_nothing(connect, monkeypatch):
    chrome, _ = connect()

    def no_connection(session, browser):
        raise RuntimeError("timed out waiting for the extension")
    monkeypatch.setattr(extension_browser, "_clients", {})
    monkeypatch.setattr(extension_browser, "_client", no_connection)
    result = _run(chrome, [("click", {"element": "e1"})])
    assert result.text.startswith("MACHINE_UNREACHABLE: your Google Chrome did not connect")
    assert result.json_data == {"action_dispatched": False}
    monkeypatch.setattr(extension_browser.shutil, "which", lambda name: None)
    result = _run(chrome, [("click", {"element": "e1"})])
    assert result.text.startswith("DEPENDENCY_MISSING: ") and "Install Node.js" in result.text


def test_a_connection_lost_during_a_step_says_its_outcome_is_unknown(connect):
    chrome, fake = connect()
    fake.failing["browser_click"] = RuntimeError("socket closed")
    result = _run(chrome, [("click", {"element": "e1"})])
    assert result.text.startswith("OUTCOME_UNKNOWN: ") and "do not repeat it" in result.text
    fake.failing = {"browser_run_code_unsafe": RuntimeError("socket closed")}
    result = _run(chrome, [("click", {"element": "e1"})])
    assert result.text.startswith("MACHINE_UNREACHABLE: ") and result.json_data == {"action_dispatched": False}


def test_a_stale_ref_stops_the_list_with_the_page_as_it_is(connect):
    chrome, fake = connect("chrome", "https://a.test/form")
    original = fake.call

    def call(name, arguments):
        if name == "browser_type" and arguments.get("target") == "second":
            fake.calls.append((name, arguments))
            return _reply("Error: Ref second not found in the current page snapshot.", error=True)
        return original(name, arguments)
    fake.call = call
    result = _run(chrome, [("type_text", {"element": "first", "text": "name", "clear": True}),
                           ("type_text", {"element": "second", "text": "key", "clear": True}),
                           ("click", {"element": "submit"})])
    assert result.text.startswith("ELEMENT_GONE: step 2 (type_text): The element is no longer on the page")
    assert "Steps done: 1. type_text." in result.text
    assert [name for name, _ in fake.acts()] == ["browser_type", "browser_type", "browser_snapshot"]


def test_the_users_browser_is_one_grant_under_its_own_rule(connect):
    chrome, _ = connect()
    assert chrome.approval({}, [("click", {})]) == Approval("computer_use controls your Google Chrome", "browser chrome")
    assert chrome.approval({}, [("upload_file", {"path": "/a"})]).rule == "browser chrome"
    assert chrome.authority([("get_state", {})], False) == "process.exec"
    assert chrome.runs_page_script([("evaluate", {"script": "1"})])
    # One key for the whole browser: a tab has another id in each conversation, and a step without tab names none.
    assert chrome.lock({"tab": "tab1"}, [("click", {})]) == locks.Locks(exclusive=("tab/local/browser chrome/*",))
    assert chrome.lock({}, [("click", {})]) == locks.Locks(exclusive=("tab/local/browser chrome/*",))
    assert "list_panes" not in chrome.surface().actions() and "create_tab" in chrome.surface().actions()


def test_a_chrome_window_is_claimed_only_for_a_tab_this_conversation_attached(connect):
    chrome, _ = connect("chrome", "https://a.test/")
    window = {"bundle_id": "com.google.Chrome", "url": "https://a.test/"}
    # Nothing attached yet: the page stays with the window target.
    assert chrome.claims(window, "s") == ""
    _run(chrome, [("list_tabs", {})])
    assert "the user's Google Chrome" in chrome.claims(window, "s")
    assert chrome.claims(window, "other") == ""
    assert chrome.claims({**window, "url": "https://never-shared.test/"}, "s") == ""
    assert chrome.claims({"bundle_id": "com.google.Chrome"}, "s") == ""
    assert chrome.claims({**window, "bundle_id": "com.apple.Safari"}, "s") == ""


def test_a_window_at_the_address_of_a_tab_that_is_gone_is_not_claimed(connect):
    chrome, fake = connect("chrome", "https://a.test/", "https://b.test/")
    window = {"bundle_id": "com.google.Chrome", "url": "https://b.test/"}
    _run(chrome, [("list_tabs", {})])
    assert "the user's Google Chrome" in chrome.claims(window, "s")
    fake.pages.pop(1)
    fake.selected = 0
    _run(chrome, [("list_tabs", {})])
    assert chrome.claims(window, "s") == ""
    assert "the user's Google Chrome" in chrome.claims({**window, "url": "https://a.test/"}, "s")


def test_the_last_agent_ending_closes_the_connection(connect, monkeypatch):
    chrome, _ = connect()
    closed = []
    monkeypatch.setattr(extension_browser, "close_session", lambda session, browser="": closed.append((session, browser)))
    _run(chrome, [("get_state", {})], agent="a1")
    _run(chrome, [("get_state", {})], agent="a2")
    chrome.close(Call("s", "a1"))
    assert closed == []
    chrome.close(Call("s", "a2"))
    assert closed == [("s", "chrome")]
    assert ("s", "chrome") not in browser_tabs._tables and ("s", "chrome") not in browser_tabs._users


class _TurnTaken:
    """A turn some other agent holds: entering it means the caller waited."""

    def __enter__(self):
        raise AssertionError("close waited for the turn")


def test_ending_an_agent_never_waits_for_another_agents_step(connect, monkeypatch):
    chrome, _ = connect()
    closed, queued = [], []
    monkeypatch.setattr(extension_browser, "close_session", lambda session, browser="": closed.append((session, browser)))
    monkeypatch.setattr(user_browser, "_in_background", lambda target, *args: queued.append((target, args)))
    _run(chrome, [("get_state", {})], agent="a1")
    _run(chrome, [("get_state", {})], agent="a2")
    turn = browser_tabs._turns[("s", "chrome")]
    browser_tabs._turns[("s", "chrome")] = _TurnTaken()
    chrome.close(Call("s", "a1"))
    chrome.close(Call("s", "a2"))
    assert len(queued) == 1 and closed == []
    browser_tabs._turns[("s", "chrome")] = turn
    # An agent that starts using the connection before the turn comes keeps it.
    _run(chrome, [("get_state", {})], agent="a3")
    target, args = queued.pop()
    target(*args)
    assert closed == []
    chrome.close(Call("s", "a3"))
    target, args = queued.pop()
    target(*args)
    assert closed == [("s", "chrome")]


def test_an_agent_that_only_listed_tabs_keeps_the_connection_when_another_ends(connect, monkeypatch):
    chrome, _ = connect()
    closed = []
    monkeypatch.setattr(extension_browser, "close_session", lambda session, browser="": closed.append((session, browser)))
    listed = _run(chrome, [("list_tabs", {})], agent="main").json_data["tabs"]
    _run(chrome, [("create_tab", {"url": "https://b.test/"})], agent="s\njob")
    chrome.close(Call("s", "s\njob"))
    assert closed == []
    assert [row["tab"] for row in _run(chrome, [("list_tabs", {})], agent="main").json_data["tabs"]][:1] == \
        [listed[0]["tab"]]


def test_get_state_returns_the_outline_and_the_screenshot_by_default(connect):
    chrome, fake = connect()
    result = _run(chrome, [("get_state", {})])
    assert result.images == [b"hi"] and "browser_take_screenshot" in [name for name, _ in fake.calls]
    fake.calls.clear()
    assert _run(chrome, [("get_state", {})], images=False).images == []
    assert "browser_take_screenshot" not in [name for name, _ in fake.calls]


def test_console_and_network_filters_the_extension_cannot_apply_are_refused(connect):
    chrome, fake = connect()
    result = _run(chrome, [("read_console_messages", {"pattern": "TypeError", "limit": 5})])
    assert result.text.startswith("NOT_SUPPORTED: read_console_messages takes only only_errors")
    result = _run(chrome, [("read_network_requests", {"only_errors": True})])
    assert result.text.startswith("NOT_SUPPORTED: read_network_requests takes only pattern")
    assert not [name for name, _ in fake.acts() if name in ("browser_console_messages", "browser_network_requests")]
    assert not _run(chrome, [("read_console_messages", {"only_errors": True})]).is_error
    assert ("browser_console_messages", {"level": "error"}) in fake.calls


def test_a_text_only_caller_gets_no_screenshot(connect):
    chrome, _ = connect("edge")
    assert _run(chrome, [("get_state", {"include": "all"})], images=False).images == []
    assert _run(chrome, [("get_state", {"include": "screenshot"})]).images == [b"hi"]


def test_find_lists_the_matching_lines_of_the_page(connect):
    chrome, fake = connect()
    original = fake.call

    def call(name, arguments):
        if name == "browser_snapshot":
            fake.calls.append((name, arguments))
            return _reply('- Page URL: https://a.test/\n- button "Save" [ref=e3]\n- link "Help" [ref=e4]')
        return original(name, arguments)
    fake.call = call
    result = _run(chrome, [("find", {"role": "button"})])
    assert result.text.endswith('found:\n- button "Save" [ref=e3]')


# -- the extension adapter -------------------------------------------------------------

class _HiddenTabClient:
    def __init__(self, visibility):
        self.visibility = visibility
        self.calls = []

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        text = f'### Result\n"{self.visibility}"' if arguments.get("function", "").endswith("visibilityState") else "done"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)


def _extension_browser(client):
    page = extension_browser.ExtensionBrowser.__new__(extension_browser.ExtensionBrowser)
    page.browser, page.client = "chrome", client
    return page


def test_click_in_a_tab_behind_others_is_sent_in_the_page():
    client = _HiddenTabClient("hidden")
    assert _extension_browser(client).run("click", {"element": "e5"})["ok"]
    assert [name for name, _ in client.calls] == ["browser_evaluate", "browser_evaluate"]
    assert client.calls[1][1]["target"] == "e5"


def test_click_in_the_front_tab_stays_a_real_click():
    client = _HiddenTabClient("visible")
    assert _extension_browser(client).run("click", {"element": "e5"})["ok"]
    assert client.calls[-1][0] == "browser_click"


def test_a_new_tab_is_opened_beside_the_connection_page():
    client = _HiddenTabClient("visible")
    _extension_browser(client).run("create_tab", {"url": "https://example.com"})
    assert client.calls == [("browser_tabs", {"action": "new", "url": "https://example.com"})]


def test_an_element_named_by_role_and_name_reaches_the_users_chrome():
    client = _HiddenTabClient("visible")
    page = _extension_browser(client)
    page.run("type_text", {"role": "textbox", "name": "Secret", "text": "s3", "clear": True})
    page.run("click", {"role": "button", "name": 'Add "one"'})
    page.run("hover", {"role": "main", "seconds": 0})
    assert [call for call in client.calls if not call[1].get("function", "").endswith("visibilityState")] == [
        ("browser_type", {"target": 'getByRole("textbox", { name: "Secret", exact: true })', "text": "s3"}),
        ("browser_click", {"target": 'getByRole("button", { name: "Add \\"one\\"", exact: true })'}),
        ("browser_hover", {"target": 'getByRole("main")'}),
    ]


def test_an_action_on_no_element_is_turned_back_before_the_browser():
    client = _HiddenTabClient("visible")
    page = _extension_browser(client)
    for action, step in (("hover", {}), ("set_value", {"value": "a"}), ("click", {}), ("click", {"role": "a b"})):
        result = page.run(action, step)
        assert result["ok"] is False and "role" in str(result.get("message")), (action, result)
    assert client.calls == []


def test_pointing_by_x_y_moves_the_mouse_there():
    client = _HiddenTabClient("visible")
    page = _extension_browser(client)
    page.run("hover", {"x": 10, "y": 20, "seconds": 0})
    page.run("scroll", {"x": 10, "y": 20, "direction": "down", "pixels": 300})
    assert _without_visibility(client.calls) == [("browser_mouse_move_xy", {"x": 10, "y": 20}), ("browser_mouse_move_xy", {"x": 10, "y": 20}),
                            ("browser_mouse_wheel", {"deltaX": 0, "deltaY": 300})]


def _without_visibility(calls):
    return [call for call in calls if not call[1].get("function", "").endswith("visibilityState")]


def test_pointer_steps_in_a_tab_behind_others_are_sent_in_the_page():
    """Chrome draws a tab behind others no frames; a real pointer move, wheel or Playwright drag waits for them."""
    class Client(_HiddenTabClient):
        def call(self, name, arguments):
            self.calls.append((name, arguments))
            function = arguments.get("function", "")
            text = ('### Result\n"hidden"' if function.endswith("visibilityState") else
                    '### Result\n"check"' if "HTMLSelectElement" in function else
                    "### Result\n[230, 330]" if "getBoundingClientRect(); return [" in function else "done")
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)
    client = Client("hidden")
    page = _extension_browser(client)
    steps = (("hover", {"x": 10, "y": 20, "seconds": 0}), ("scroll", {"x": 10, "y": 20, "direction": "down", "pixels": 300}),
             ("scroll", {"direction": "down", "pixels": 300}), ("click", {"x": 10, "y": 20, "seconds": 0.01}),
             ("set_value", {"element": "e3", "value": True}), ("drag", {"element": "e1", "to_element": "e2"}),
             ("drag", {"x": 1, "y": 2, "to_x": 30, "to_y": 40}))
    for action, step in steps:
        assert page.run(action, step)["ok"], (action, step)
    names = {name for name, _ in client.calls}
    assert names == {"browser_evaluate"}, names
    drags = [args for _, args in client.calls if "dragstart" in args.get("function", "")]
    assert drags[0]["target"] == "e1" and '"to": {"x": 230.0, "y": 330.0}' in drags[0]["function"]
    assert '"from": {"x": 1.0, "y": 2.0}' in drags[1]["function"] and "target" not in drags[1]


def test_set_value_chooses_options_of_a_select_box():
    class Client(_HiddenTabClient):
        def call(self, name, arguments):
            self.calls.append((name, arguments))
            text = '### Result\n"select"' if "HTMLSelectElement" in arguments.get("function", "") else "done"
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)
    client = Client("visible")
    _extension_browser(client).run("set_value", {"element": "e3", "value": ["a", "b"]})
    assert client.calls[-1] == ("browser_select_option", {"target": "e3", "values": ["a", "b"]})


def test_a_role_and_name_that_match_several_or_none_say_so():
    class Client:
        def __init__(self, text):
            self.text = text

        def call(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], isError=True)

    step = {"role": "button", "name": "Dup", "x": 1}
    several = _extension_browser(Client(
        "### Error\nError: strict mode violation: getByRole('button', { name: 'Dup', exact: true }) resolved to 2 elements:"))
    assert several.run("click", step)["result"] == '2 elements are button "Dup"; use a ref'
    none = _extension_browser(Client(
        '### Error\nError: "getByRole("button", { name: "Dup", exact: true })" does not match any elements.'))
    result = none.run("click", step)
    assert result["reason_code"] == "element_gone" and result["result"].startswith('button "Dup" is not on the page')


def test_wait_takes_seconds_and_a_text_as_the_built_in_browser_does():
    class Client(_HiddenTabClient):
        def call(self, name, arguments):
            self.calls.append((name, arguments))
            shown = "true" if "Saved" in arguments.get("function", "") else "false"
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"### Result\n{shown}")], isError=False)

    client = Client("visible")
    page = _extension_browser(client)
    assert page.run("wait", {"seconds": 2})["ok"]
    assert client.calls == [("browser_wait_for", {"time": 2.0})]
    assert page.run("wait", {"text": "Saved"})["ok"]
    missing = page.run("wait", {"text": "Gone", "seconds": 0.1})
    assert not missing["ok"] and missing["reason_code"] == "text_not_found"
    assert page.run("wait", {"seconds": "soon"})["reason_code"] == "invalid_seconds"


def test_a_triple_click_is_sent_in_the_page():
    client = _HiddenTabClient("visible")
    page = _extension_browser(client)
    page.run("click", {"element": "e4", "click_count": 3})
    page.run("click", {"element": "e4", "click_count": 2, "mouse_button": "right"})
    triple, double = [call for call in client.calls if not call[1].get("function", "").endswith("visibilityState")]
    assert triple[0] == "browser_evaluate" and triple[1]["target"] == "e4" and '"count": 3' in triple[1]["function"]
    assert double == ("browser_click", {"target": "e4", "doubleClick": True, "button": "right"})


def test_holding_a_key_is_not_supported_here():
    result = _extension_browser(_HiddenTabClient("visible")).run("press_key", {"key": "shift", "seconds": 2})
    assert result["reason_code"] == "unsupported_option"


def _empty_tab_client(monkeypatch, texts):
    calls = []

    class Client:
        last_page_url = "https://example.com/"

        def call(self, name, arguments):
            calls.append(name)
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=texts(len(calls)))], isError=False)
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    return _extension_browser(Client()), calls


def test_empty_tab_response_retries_observation_not_navigation(monkeypatch):
    page, calls = _empty_tab_client(monkeypatch, lambda n: "### Open tabs\nNo open tabs. Navigate to a URL to create one."
                                    if n == 1 else "### Page\n- Page URL: https://example.com/")
    result = page.run("navigate", {"url": "https://example.com/"})
    assert result["status"] == "observation_required" and result["action_outcome"] == "unknown" and result["ok"]
    assert calls == ["browser_navigate", "browser_snapshot"]


def test_empty_tab_recovery_is_bounded_and_does_not_accept_another_page(monkeypatch):
    page, calls = _empty_tab_client(monkeypatch, lambda n: "No open tabs. Navigate to a URL to create one."
                                    if n == 1 else "- Page URL: https://unrelated.invalid/")
    result = page.run("navigate", {"url": "https://example.com/"})
    assert result["status"] == "observation_required" and "no longer attached" in result["result"]
    assert calls == ["browser_navigate"] + ["browser_snapshot"] * 3


def test_empty_type_response_does_not_submit(monkeypatch):
    page, calls = _empty_tab_client(monkeypatch, lambda n: "No open tabs. Navigate to a URL to create one."
                                    if n == 1 else "- Page URL: https://example.com/")
    result = page.run("type_text", {"element": "e1", "text": "hello", "clear": True, "submit": True})
    assert result["status"] == "observation_required"
    assert calls == ["browser_type", "browser_snapshot"]


_CLOSED = "Error: Target page, context or browser has been closed"


def _recovering(error, snapshot_url="https://a.test/form", snapshot_failed=False):
    calls = []

    class Client:
        last_page_url = "https://a.test/form"

        def call(self, name, arguments):
            calls.append(name)
            if name == "browser_snapshot":
                return _reply("- Page URL: " + snapshot_url, error=snapshot_failed)
            return _reply(error, error=True)
    return _extension_browser(Client()).run("type_text", {"element": "e1", "text": "x", "clear": True}), calls


def test_a_closed_connection_observes_and_says_the_outcome_is_unknown():
    result, calls = _recovering(_CLOSED)
    assert result["status"] == "observation_required" and result["action_outcome"] == "unknown"
    assert calls == ["browser_type", "browser_snapshot"]


def test_recovery_does_not_accept_a_different_page():
    result, calls = _recovering(_CLOSED, snapshot_url="https://other.invalid/")
    assert result["ok"] is False and "status" not in result and result["result"] == _CLOSED
    assert calls == ["browser_type", "browser_snapshot"]


def test_a_failed_recovery_snapshot_leaves_the_error():
    result, _ = _recovering("Error: Ref e1 not found in the current page snapshot.", snapshot_failed=True)
    assert result["ok"] is False and "status" not in result


def test_a_closed_connection_during_a_list_is_outcome_unknown_and_stops_it(connect):
    chrome, fake = connect("chrome", "https://a.test/form")
    fake.last_page_url = "https://a.test/form"
    original = fake.call

    def call(name, arguments):
        if name == "browser_type":
            fake.calls.append((name, arguments))
            return _reply(_CLOSED, error=True)
        if name == "browser_snapshot":
            fake.calls.append((name, arguments))
            return _reply("- Page URL: https://a.test/form")
        return original(name, arguments)
    fake.call = call
    result = _run(chrome, [("type_text", {"element": "e1", "text": "x", "clear": True}), ("click", {"element": "e2"})])
    assert result.text.startswith("OUTCOME_UNKNOWN: step 1 (type_text)")
    assert "browser_click" not in [name for name, _ in fake.acts()]
