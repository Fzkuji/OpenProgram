"""Target browser: OpenProgram's own browser through the tab driver, with the browser family's actions."""
import re
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


from openprogram.host.machines import Connection, Machine, local_machine
from openprogram.programs.tools.computer_use.computer_use.routing import dispatch, locks
from openprogram.programs.tools.computer_use.computer_use.actions import vocabulary
from openprogram.programs.tools.computer_use.computer_use.contract import Approval, Call, Row
from openprogram.programs.tools.computer_use.computer_use.providers.browser import built_in as built_in_browser
from tests.support.screen_targets import user_browsers

GUIDE = Path(built_in_browser.__file__).parents[2] / "guides" / "browser.md"


class FakeDriver:
    """The tab driver: answers each action from a table and records what it was asked."""

    def __init__(self, **answers):
        self.answers = answers
        self.runs = []
        self.batches = 0
        self.forgotten = []

    def run(self, action, args, session, agent=""):
        self.runs.append((action, args, session, agent))
        answer = self.answers.get(action, {"ok": True, "done": f"{action} done", "tab": "tab1",
                                           "url": "https://a.test/", "title": "A"})
        return dict(answer() if callable(answer) else answer)

    def known(self, tab):
        return SimpleNamespace(title="A", origin="https://a.test") if tab == "tab1" else None


def _browser(driver, rows=()):
    @contextmanager
    def batch():
        driver.batches += 1
        yield
    tabs = SimpleNamespace(driver=lambda: driver, batch=batch, forget=driver.forgotten.append)
    pages = SimpleNamespace(project_conversation_resources=lambda conversation: (list(rows), None, None))
    return built_in_browser.BuiltInBrowser(SimpleNamespace(tabs=tabs, pages=pages))


def _run(provider, steps, subject=None, **call):
    return dispatch.run_steps(provider, subject or {}, steps, Call("s", "a1", **call))


def test_this_computer_lists_the_built_in_browser_and_each_browser_with_the_extension(monkeypatch):
    service = SimpleNamespace(extension=user_browsers("chrome", "edge"))
    monkeypatch.setattr(built_in_browser, "service", lambda: service)
    found = built_in_browser._open(local_machine(), "s")
    assert [provider.target for provider in found] == ["browser", "browser chrome", "browser edge"]
    assert [provider.address() for provider in found] == [
        'type="browser"', 'type="browser", app="chrome"', 'type="browser", app="edge"']
    assert built_in_browser._open(Machine("lab", "lab", Connection("osworld")), "s") == []


def test_actions_reach_the_tab_driver_under_the_agents_name():
    driver = FakeDriver()
    result = _run(_browser(driver), [("click", {"element": "e5"})], {"tab": "tab1"})
    assert not result.is_error
    assert driver.runs == [("click", {"tab": "tab1", "element": "e5"}, "s", "a1")]
    assert result.text == "click done\ntab tab1 · A · https://a.test/"


def test_list_tabs_and_get_state_take_their_own_paths():
    driver = FakeDriver(list_tabs={"ok": True, "tabs": [{"tab": "tab1", "title": "A", "url": "https://a.test/",
                                                        "current": True, "shown": "background"}]})
    provider = _browser(driver)
    listed = _run(provider, [("list_tabs", {})])
    assert listed.text.startswith("Tabs (the current one is used when tab is omitted):\n- tab1 (current): A")
    _run(provider, [("get_state", {"include": "tree"})])
    assert [run[0] for run in driver.runs] == ["list_tabs", "get_state"]


def test_driver_failures_answer_with_their_codes_and_say_what_was_sent():
    cases = {
        "element_gone": ("ELEMENT_GONE", False),
        "tab_busy": ("SCREEN_BUSY", False),
        "action_failed": ("OUTCOME_UNKNOWN", True),
        "tab_not_found": ("BAD_ARGUMENT", False),
        "app_unavailable": ("MACHINE_UNREACHABLE", False),
        "browser_unavailable": ("DEPENDENCY_MISSING", False),
    }
    for reason, (code, sent) in cases.items():
        driver = FakeDriver(click={"ok": False, "reason_code": reason, "error": f"it failed: {reason}"})
        result = _run(_browser(driver), [("click", {"element": "e1"})])
        assert result.is_error and result.text.startswith(f"{code}: it failed: {reason}"), result.text
        assert result.json_data == (None if sent else {"action_dispatched": False}), reason


def test_a_failure_without_a_code_keeps_its_own_words():
    driver = FakeDriver(click={"ok": False, "reason_code": "something_else", "error": "the page said no"})
    result = _run(_browser(driver), [("click", {"element": "e1"})])
    assert result.is_error and result.text == "the page said no"


def test_a_stale_ref_stops_the_list_and_counts_the_steps_done():
    driver = FakeDriver(set_value={"ok": False, "reason_code": "element_gone", "error": "e9 is gone",
                                   "page": "- button \"Go\" [ref=e2]"})
    result = _run(_browser(driver), [("type_text", {"element": "e1", "text": "a"}), ("set_value", {"element": "e9",
                                     "value": "b"}), ("click", {"element": "e2"})])
    assert result.text.startswith("ELEMENT_GONE: step 2 (set_value): e9 is gone")
    assert result.text.endswith("Steps done: 1. type_text.")
    assert result.json_data is None  # the first step was sent
    assert [run[0] for run in driver.runs] == ["type_text", "set_value"] and driver.batches == 1


def test_a_list_runs_its_steps_in_one_batch_and_counts_them():
    driver = FakeDriver()
    result = _run(_browser(driver), [("click", {"element": "e1"}), ("press_key", {"key": "Return"})])
    assert result.text.endswith("2 of 2 steps done.") and driver.batches == 1


def test_a_call_out_of_time_stops_between_steps():
    driver = FakeDriver()
    ticks = iter([False, True])

    def check():
        if next(ticks):
            raise built_in_browser.errors.Timeout("late")
    call = Call("s", "a1")
    call.check = check
    result = dispatch.run_steps(_browser(driver), {}, [("click", {"element": "e1"}), ("click", {"element": "e2"})],
                                call)
    assert result.text.startswith("TIMEOUT: the call ran out of time; steps that took effect: 1. click.")
    assert len(driver.runs) == 1
    late = _run(_browser(FakeDriver()), [("click", {"element": "e1"})], deadline=time.monotonic() - 1)
    assert late.text.startswith("TIMEOUT: ") and late.json_data == {"action_dispatched": False}


def test_a_text_only_caller_gets_no_screenshot():
    driver = FakeDriver(get_state={"ok": True, "tab": "tab1", "url": "u", "page": "- main", "_images": [b"png"]})
    provider = _browser(driver)
    assert _run(provider, [("get_state", {"include": "all"})], images=False).images == []
    assert _run(provider, [("get_state", {"include": "all"})]).images == [b"png"]


def test_close_tab_closes_only_a_tab_the_call_names():
    driver = FakeDriver()
    result = _run(_browser(driver), [("close_tab", {})])
    assert result.text.startswith("BAD_ARGUMENT: close_tab closes only a tab the call names")
    assert driver.runs == []
    assert not _run(_browser(driver), [("close_tab", {})], {"tab": "tab1"}).is_error


def test_permission_authority_and_locks():
    provider = _browser(FakeDriver())
    assert provider.approval({}, [("navigate", {"url": "https://a.test"}), ("evaluate", {"script": "1"})]) is None
    assert provider.approval({}, [("upload_file", {"path": "/a"})]).rule is None
    assert isinstance(provider.approval({}, [("upload_file", {"path": "/a"})]), Approval)
    assert provider.authority([("click", {})], False) == "browser.control"
    assert provider.authority([("click", {})], True) == "process.exec"
    assert provider.runs_page_script([("evaluate", {"script": "1"})])
    assert not provider.runs_page_script([("click", {})])
    held = provider.lock({"tab": "tab1"}, [("click", {}), ("click", {"tab": "tab2"})])
    # Pages are driven in the background: the machine is not held, so a screen call never waits for them.
    assert held == locks.Locks(exclusive=("tab/local/browser/tab1", "tab/local/browser/tab2"))


def test_resources_and_windows():
    provider = _browser(FakeDriver(), rows=[{"id": "p1", "status": "open"}, {"id": "p2", "status": "closed"}])
    assert provider.resource({"tab": "tab1"}) == Row("web", "A", "https://a.test")
    assert provider.resource({"tab": "gone"}) is None and provider.resource({}) is None
    assert provider.created("s") == [{"id": "p1", "status": "open"}]
    assert "a Page of OpenProgram's browser" in provider.claims({"app": "OpenProgram Dev"}, "s")
    assert provider.claims({"app": "Safari"}, "s") == ""


def test_get_state_returns_the_outline_and_the_screenshot_unless_the_caller_reads_text_only():
    driver = FakeDriver()
    provider = _browser(driver)
    _run(provider, [("get_state", {})])
    _run(provider, [("get_state", {})], images=False)
    _run(provider, [("get_state", {"include": "screenshot"})], images=False)
    assert [run[1]["include"] for run in driver.runs] == ["all", "tree", "screenshot"]


def test_an_agent_that_ended_is_forgotten_by_the_tab_driver():
    driver = FakeDriver()
    provider = _browser(driver)
    provider.close(Call("s", "s\njob-1"))
    provider.close(Call("s", ""))
    assert driver.forgotten == ["s\njob-1"]


def test_close_tab_names_the_tab_it_closed():
    driver = FakeDriver(close_tab={"ok": True, "tab": "tab1", "done": "closed the tab"})
    result = _run(_browser(driver), [("close_tab", {})], {"tab": "tab1"})
    assert result.text == "closed the tab\ntab tab1"


def test_in_a_conversation_on_another_machine_localhost_is_still_this_computers():
    provider = _browser(FakeDriver())
    assert provider.environment_note("devbox", set()) == " Its http://localhost is this computer's."
    assert provider.environment_note("devbox", {"devbox browser"}) == (
        " Its http://localhost is this computer's; for devbox's use device=\"devbox\", type=\"browser\".")


def test_the_provider_offers_every_extra_of_the_family():
    actions = set(_browser(FakeDriver()).surface().actions())
    assert set(vocabulary.FAMILIES["browser"].extras) <= actions and set(vocabulary.CORE) <= actions


def test_the_guide_has_one_line_per_action_in_the_vocabulary():
    family = vocabulary.FAMILIES["browser"]
    named = re.findall(r"^- (\w+)[(:]", GUIDE.read_text(), flags=re.M)
    assert sorted(named) == sorted({*vocabulary.CORE, *family.extras})
    for line in re.findall(r"^- (\w+\([^)]*\))", GUIDE.read_text(), flags=re.M):
        action, arguments = line[:-1].split("(", 1)
        known = set(family.arguments(action)) | {"element", "x", "y", "to_x", "to_y", "to_element"}
        for argument in re.split(r",\s*|\s\|\s", re.sub(r"=[^,\s]*|\?", "", arguments)):
            assert argument in known, (action, argument)


def test_render_names_the_new_tab_a_step_opened_and_what_find_found():
    text = built_in_browser.render({"ok": True, "done": "clicked", "opened": [{"tab": "tab2", "url": "https://b.test"}],
                                    "found": ['- button "Save" [ref=e3]']})
    assert 'pass title="tab2" to use it' in text and text.endswith('found:\n- button "Save" [ref=e3]')
