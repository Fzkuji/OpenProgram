"""The browser target reaches the user's Chrome or Edge with the built-in browser's actions."""
from types import SimpleNamespace

from openprogram.runtime.permissions.policy import _user_browser
from openprogram.runtime.permissions.rule import parse_command
from openprogram.programs.tools.computer_use.computer_use_agent import tool
from openprogram.programs.tools.computer_use.computer_use_agent.targets import browser
from openprogram.programs.applications.browser import extension as extension_browser


class FakeClient:
    def __init__(self):
        self.calls = []

    def is_alive(self):
        return True

    def call(self, name, arguments):
        self.calls.append((name, arguments))
        if arguments.get("target") == "bad":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="no such ref")], isError=True)
        if name == "browser_evaluate":
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="### Result\n\"Example Domain\"")],
                                   isError=False)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"ran {name}"),
                                        SimpleNamespace(type="image", data="aGk=")], isError=False)


def _acts(fake):
    """The calls that do something, without the check whether the tab is in front."""
    return [call for call in fake.calls if not call[1].get("function", "").endswith("visibilityState")]


def _install(monkeypatch, browser_name="chrome"):
    fake = FakeClient()
    monkeypatch.setattr(extension_browser, "installed", lambda name: True)
    monkeypatch.setattr(extension_browser, "_clients", {("s", browser_name): fake})
    return fake


def test_the_same_actions_reach_the_users_chrome(monkeypatch):
    fake = _install(monkeypatch)
    result = browser.run([("open", {"url": "https://example.com"}), ("click", {"ref": "e5"}),
                          ("type", {"ref": "e7", "text": "hi"}), ("press", {"key": "Enter"})],
                         {"browser": "chrome"}, "s")
    assert not result.is_error
    assert _acts(fake) == [
        ("browser_tabs", {"action": "new", "url": "https://example.com"}),
        ("browser_snapshot", {}),
        ("browser_click", {"target": "e5"}),
        ("browser_type", {"target": "e7", "text": "hi"}),
        ("browser_press_key", {"key": "Enter"}),
    ]


def test_edge_is_its_own_connection_and_screenshots_come_back(monkeypatch):
    fake = _install(monkeypatch, "edge")
    result = browser.run([("screenshot", {})], {"browser": "edge"}, "s")
    assert fake.calls == [("browser_take_screenshot", {"type": "png"})]
    assert result.images == [b"hi"]
    assert extension_browser._command("edge")[-6:-4] == ["--browser", "msedge"]


def test_a_failed_step_stops_the_rest(monkeypatch):
    fake = _install(monkeypatch)
    result = browser.run([("click", {"ref": "bad"}), ("click", {"ref": "e2"})], {"browser": "chrome"}, "s")
    assert result.is_error and len(_acts(fake)) == 1


def test_tabs_and_read_are_the_extensions_tab_list_and_snapshot(monkeypatch):
    fake = _install(monkeypatch)
    browser.run([("tabs", {}), ("read", {"tab": 1})], {"browser": "chrome"}, "s")
    assert fake.calls == [("browser_tabs", {"action": "list"}), ("browser_tabs", {"action": "select", "index": 1}),
                          ("browser_snapshot", {})]


def test_the_users_browser_is_one_grant_outside_the_page_grant(monkeypatch):
    monkeypatch.setattr(extension_browser, "installed", lambda name: True)
    call = {"target": "browser", "action": "click", "args": {"browser": "chrome", "ref": "e1"}}
    assert parse_command("computer_use_agent", call) == "browser:chrome"
    assert parse_command("computer_use_agent", {"target": "browser", "args": {"ref": "e1"}}) is None
    assert tool.requires_approval(**call) == "computer_use_agent controls your Chrome"
    assert tool.requires_approval(target="browser", action="click", args={"ref": "e1"}) is False
    assert _user_browser(call) == "chrome" and _user_browser({"target": "browser", "args": {}}) == ""


def test_the_extension_counts_as_installed_where_a_profile_holds_it(tmp_path, monkeypatch):
    monkeypatch.setattr(extension_browser, "_data_dir", lambda name: tmp_path / name)
    (tmp_path / "chrome" / "Profile 3" / "Extensions" / extension_browser.EXTENSION_ID).mkdir(parents=True)
    (tmp_path / "edge" / "Default" / "Extensions" / "someotherextension").mkdir(parents=True)
    assert extension_browser.installed_browsers() == ("chrome",)


def test_without_the_extension_the_guide_offers_only_the_built_in_browser(monkeypatch):
    monkeypatch.setattr(extension_browser, "installed", lambda name: False)
    text = browser.guide()
    assert 'args.browser' not in text
    assert "not reachable as web pages here" in text


def test_with_the_extension_the_guide_offers_that_browser_alone(monkeypatch):
    monkeypatch.setattr(extension_browser, "installed", lambda name: name == "edge")
    text = browser.guide()
    assert '- "edge": the user\'s own Edge' in text
    assert '"chrome"' not in text


def test_naming_a_browser_without_the_extension_starts_nothing(monkeypatch):
    monkeypatch.setattr(extension_browser, "installed", lambda name: False)
    monkeypatch.setattr(extension_browser, "_client", lambda *a: (_ for _ in ()).throw(AssertionError("started")))
    call = {"target": "browser", "action": "click", "args": {"browser": "chrome", "ref": "e1"}}
    assert tool.requires_approval(**call) is False
    result = tool.computer_use_agent(**call)
    assert result.is_error and "has no Playwright Extension" in result.text


class _HiddenTabClient:
    def __init__(self, visibility):
        self.visibility = visibility
        self.calls = []

    def call(self, name, arguments):
        from types import SimpleNamespace
        self.calls.append((name, arguments))
        text = f'### Result\n"{self.visibility}"' if arguments.get("function", "").endswith("visibilityState") else "done"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)


def _extension_browser(client):
    from openprogram.programs.applications.browser.extension import ExtensionBrowser
    browser = ExtensionBrowser.__new__(ExtensionBrowser)
    browser.browser, browser.client = "chrome", client
    return browser


def test_click_in_a_tab_behind_others_is_sent_in_the_page():
    client = _HiddenTabClient("hidden")
    assert _extension_browser(client).run("act", {"action": "click", "ref": "e5"})["ok"]
    assert [name for name, _ in client.calls] == ["browser_evaluate", "browser_evaluate"]
    assert client.calls[1][1]["target"] == "e5"


def test_click_in_the_front_tab_stays_a_real_click():
    client = _HiddenTabClient("visible")
    assert _extension_browser(client).run("act", {"action": "click", "ref": "e5"})["ok"]
    assert client.calls[-1][0] == "browser_click"


def test_connection_page_is_not_navigated_as_a_web_page():
    class Client(_HiddenTabClient):
        def call(self, name, arguments):
            from types import SimpleNamespace
            self.calls.append((name, arguments))
            text = "- 0: (current) [Welcome](chrome-extension://x/connect.html?a=1)" if name == "browser_tabs" else "ok"
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)

    client = Client("visible")
    _extension_browser(client).run("observe", {"url": "https://example.com"})
    assert [name for name, _ in client.calls] == ["browser_tabs", "browser_snapshot"]
    assert client.calls[0][1] == {"action": "new", "url": "https://example.com"}


def test_an_element_named_by_role_and_name_reaches_the_users_chrome(monkeypatch):
    fake = _install(monkeypatch)
    result = browser.run([("type", {"role": "textbox", "name": "Secret", "text": "s3"}),
                          ("click", {"role": "button", "name": 'Add "one"'}),
                          ("hover", {"role": "main"})], {"browser": "chrome"}, "s")
    assert not result.is_error
    assert _acts(fake) == [
        ("browser_type", {"target": 'getByRole("textbox", { name: "Secret", exact: true })', "text": "s3"}),
        ("browser_click", {"target": 'getByRole("button", { name: "Add \\"one\\"", exact: true })'}),
        ("browser_hover", {"target": 'getByRole("main")'}),
    ]


def test_an_action_on_no_element_is_turned_back_before_the_browser(monkeypatch):
    fake = _install(monkeypatch)
    for action, step in (("hover", {}), ("select", {"value": "a"}), ("click", {}), ("click", {"role": "a b"})):
        result = browser.run([(action, step)], {"browser": "chrome"}, "s")
        assert result.is_error and "role" in result.text
    assert fake.calls == []


def test_typing_without_an_element_goes_to_the_focused_one(monkeypatch):
    fake = _install(monkeypatch)
    assert not browser.run([("type", {"text": "hi", "submit": True})], {"browser": "chrome"}, "s").is_error
    (inserted, arguments), enter = fake.calls
    assert inserted == "browser_evaluate" and "target" not in arguments and '"hi"' in arguments["function"]
    assert enter == ("browser_press_key", {"key": "Enter"})


def test_a_script_on_an_element_receives_it(monkeypatch):
    fake = _install(monkeypatch)
    browser.run([("evaluate", {"ref": "e7", "script": "(el) => el.value"}), ("evaluate", {"script": "1 + 1"})],
                {"browser": "chrome"}, "s")
    assert [(name, arguments.get("target")) for name, arguments in fake.calls] == [
        ("browser_evaluate", "e7"), ("browser_evaluate", None)]
    assert "run(element)" in fake.calls[0][1]["function"]


def test_typing_at_the_caret_and_pressing_a_key_on_an_element(monkeypatch):
    fake = _install(monkeypatch)
    browser.run([("type", {"ref": "e7", "text": "hi", "clear": False}), ("press", {"ref": "e7", "key": "Enter"})],
                {"browser": "chrome"}, "s")
    assert [(name, arguments.get("target")) for name, arguments in fake.calls] == [
        ("browser_evaluate", "e7"), ("browser_evaluate", "e7"), ("browser_press_key", None)]


def test_a_role_and_name_that_match_several_or_none_say_so():
    class Client:
        def __init__(self, text):
            self.text = text

        def call(self, name, arguments):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.text)], isError=True)

    step = {"action": "click", "role": "button", "name": "Dup", "x": 1}
    several = _extension_browser(Client(
        "### Error\nError: strict mode violation: getByRole('button', { name: 'Dup', exact: true }) resolved to 2 elements:"))
    assert several.run("act", step)["result"] == '2 elements are button "Dup"; use a ref'
    none = _extension_browser(Client(
        '### Error\nError: "getByRole("button", { name: "Dup", exact: true })" does not match any elements.'))
    result = none.run("act", step)
    assert result["reason_code"] == "element_gone" and result["result"].startswith('button "Dup" is not on the page')


def test_wait_takes_seconds_and_a_text_as_the_built_in_browser_does():
    class Client(_HiddenTabClient):
        def call(self, name, arguments):
            self.calls.append((name, arguments))
            shown = "true" if "Saved" in arguments.get("function", "") else "false"
            return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"### Result\n{shown}")], isError=False)

    client = Client("visible")
    page = _extension_browser(client)
    assert page.run("act", {"action": "wait", "seconds": 2})["ok"]
    assert client.calls == [("browser_wait_for", {"time": 2.0})]
    assert page.run("act", {"action": "wait", "text": "Saved"})["ok"]
    missing = page.run("act", {"action": "wait", "text": "Gone", "seconds": 0.1})
    assert not missing["ok"] and missing["reason_code"] == "text_not_found"
    assert page.run("act", {"action": "wait", "seconds": "soon"})["reason_code"] == "invalid_seconds"


def test_several_options_and_a_triple_click(monkeypatch):
    fake = _install(monkeypatch)
    browser.run([("select", {"ref": "e3", "value": ["a", "b"]}), ("select", {"ref": "e3", "value": "c"}),
                 ("click", {"ref": "e4", "click_count": 3}), ("click", {"ref": "e4", "click_count": 2, "mouse_button": "right"})],
                {"browser": "chrome"}, "s")
    first, second, triple, double = _acts(fake)
    assert first == ("browser_select_option", {"target": "e3", "values": ["a", "b"]})
    assert second == ("browser_select_option", {"target": "e3", "values": ["c"]})
    assert triple[0] == "browser_evaluate" and triple[1]["target"] == "e4" and '"count": 3' in triple[1]["function"]
    assert double == ("browser_click", {"target": "e4", "doubleClick": True, "button": "right"})


def test_changed_browser_ref_refreshes_and_stops_batch_before_submit(monkeypatch):
    _check_browser_recovery(monkeypatch, "Error: Ref f1e194 not found in the current page snapshot. Try capturing new snapshot.", "not_dispatched")


def test_closed_connection_observes_without_replaying_uncertain_action(monkeypatch):
    _check_browser_recovery(monkeypatch, "Error: Target page, context or browser has been closed", "unknown")


def test_recovery_does_not_accept_a_different_page(monkeypatch):
    _check_browser_recovery(monkeypatch, "Error: Target page, context or browser has been closed", None,
                            snapshot_url="https://different.invalid/")


def test_recovery_snapshot_failure_remains_an_error(monkeypatch):
    _check_browser_recovery(monkeypatch, "Error: Ref f1e194 not found in the current page snapshot.", None,
                            snapshot_failed=True)


def _check_browser_recovery(monkeypatch, error, outcome, snapshot_url="https://example.com/form", snapshot_failed=False):
    client = _install(monkeypatch)
    def call(name, arguments):
        client.calls.append((name, arguments))
        failed = False
        if name == "browser_type" and arguments.get("target") == "second":
            text, failed = error, True
        elif name == "browser_snapshot":
            text, failed = "### Page\n- Page URL: " + snapshot_url, snapshot_failed
        else:
            text = "### Page\n- Page URL: https://example.com/form"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=failed)
    client.call = call
    result = browser.run([("type", {"ref": "first", "text": "name"}),
                          ("type", {"ref": "second", "text": "key"}),
                          ("click", {"ref": "submit"})], {"browser": "chrome"}, "s")
    assert result.is_error is (outcome is None)
    assert [name for name, _ in client.calls] == ["browser_type", "browser_type", "browser_snapshot"]
    if outcome:
        assert result.json_data[-1]["status"] == "observation_required"
        assert result.json_data[-1]["action_outcome"] == outcome
        assert "steps after it did not run" in result.text



def test_empty_tab_response_retries_observation_not_navigation(monkeypatch):
    client = FakeClient()
    calls = []
    def call(name, arguments):
        calls.append(name)
        if len(calls) == 1:
            text = "### Open tabs\nNo open tabs. Navigate to a URL to create one."
        else:
            text = "### Page\n- Page URL: https://example.com/"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)
    client.call = call
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    result = _extension_browser(client).run('navigate', {'url':'https://example.com/'})
    assert result['status'] == 'observation_required'
    assert result['action_outcome'] == 'unknown'
    assert result['ok']
    assert calls == ['browser_navigate', 'browser_snapshot']


def test_empty_tab_recovery_is_bounded_and_does_not_accept_another_page(monkeypatch):
    client = FakeClient()
    calls = []
    def call(name, arguments):
        calls.append(name)
        text = ("No open tabs. Navigate to a URL to create one." if len(calls)==1 else
                "- Page URL: https://unrelated.invalid/")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)
    client.call = call
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    b = _extension_browser(client)
    result = b.run('navigate', {'url':'https://example.com/'})
    assert result['status'] == 'observation_required'
    assert 'no longer attached' in result['result']
    assert calls == ['browser_navigate'] + ['browser_snapshot']*3



def test_empty_new_tab_interrupts_observe_before_unrelated_fourth_snapshot(monkeypatch):
    client = _install(monkeypatch)
    calls = []
    def call(name, arguments):
        calls.append(name)
        text = ("No open tabs. Navigate to a URL to create one." if len(calls)==1 else
                "- Page URL: https://different.invalid/")
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], isError=False)
    client.call = call
    monkeypatch.setattr(extension_browser.time, "sleep", lambda _: None)
    result = browser.run([('open', {'url':'https://example.com/'}), ('click', {'ref':'e1'})], {'browser':'chrome'}, 's')
    assert not result.is_error
    assert result.json_data[-1]['status'] == 'observation_required'
    assert calls == ['browser_tabs'] + ['browser_snapshot']*3


def test_empty_type_response_does_not_submit(monkeypatch):
    client = FakeClient()
    client.last_page_url = 'https://example.com/'
    calls = []
    def call(name, arguments):
        calls.append(name)
        text = ('No open tabs. Navigate to a URL to create one.' if len(calls)==1 else
                '- Page URL: https://example.com/')
        return SimpleNamespace(content=[SimpleNamespace(type='text', text=text)], isError=False)
    client.call = call
    monkeypatch.setattr(extension_browser.time, 'sleep', lambda _: None)
    result = _extension_browser(client).run('type', {'ref':'e1','text':'hello','submit':True})
    assert result['status'] == 'observation_required'
    assert calls == ['browser_type','browser_snapshot']
