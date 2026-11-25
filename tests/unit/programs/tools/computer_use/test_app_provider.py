"""The ``window`` target: this computer's application windows behind the provider contract.

The platform backend is faked: nothing here reads or operates a real window.
"""
from __future__ import annotations

import pytest

from openprogram.host import machines
from openprogram.host.machines import kinds
from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.routing import locks, registry
from openprogram.programs.tools.computer_use.computer_use import tool as entry
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.desktop import backends
from openprogram.programs.tools.computer_use.computer_use.desktop.common import base, tree
from openprogram.programs.tools.computer_use.computer_use.providers.window import provider as desktop
from openprogram.programs.tools.computer_use.computer_use.providers.window import agents as window_agents
from openprogram.programs.tools.computer_use.computer_use.providers.window import platforms as window_platforms


class _Computer:
    """A backend that records what it is asked and answers in the backend's own words."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.fail: dict[str, Exception] = {}
        self.state_text = 'App: Notes\nWindow: "Main"\n\nAccessibility tree:\n0 button "7"'
        self.sparse = False
        self._states = {("agent", 1): object(), ("other", 1): object()}

    def app_key(self, app: str) -> str:
        return f"com.example.{app.lower()}"

    def list_apps(self) -> str:
        return "Running: Notes"

    def list_windows(self, app: str) -> str:
        return f"windows of {app}"

    def get_state(self, session, app, **options):
        self.calls.append(("get_state", session, app, options))
        failure = self.fail.pop("get_state", None)
        if failure is not None:
            raise failure
        return base.Observation(self.state_text, b"png", app, sparse=self.sparse)

    def act(self, session, action, args):
        self.calls.append((action, session, dict(args)))
        failure = self.fail.get(action)
        if failure is not None:
            raise failure
        return f"did {action}"

    def settle(self, session, app):
        return 0.0

    def find(self, session, app, text, role):
        return f"found {text}"

    def zoom(self, session, app, x, y, width, height):
        return base.Observation("zoomed", b"zoom", app)


@pytest.fixture
def computer(monkeypatch):
    fake = _Computer()
    monkeypatch.setattr(backends, "support", lambda: {"available": True, "reason": ""})
    monkeypatch.setattr(backends, "backend", lambda: fake)
    monkeypatch.setattr(window_platforms, "_missing_grants", lambda system: [])
    for name in ("_acted", "_seen"):
        monkeypatch.setattr(window_agents, name, {})
    monkeypatch.setattr(desktop, "_rule_names", {})
    return fake


def _app(system: str = "Darwin") -> desktop.AppProvider:
    return desktop.AppProvider(machines.local_machine({}), "conv", system=system)


def _call(agent: str = "agent", images: bool = True) -> Call:
    return Call(conversation="conv", agent=agent, images=images)


# ----------------------------------------------------------------- the opener

def test_this_computer_yields_its_window_target(computer):
    kind = kinds.get("local")
    assert kind is not None and kind.local_only and machines.local_machine({}).kind is kind
    assert desktop.providers in registry._openers["local"]
    found = desktop.providers(machines.local_machine({}), "conv")
    assert [provider.target for provider in found] == ["window"]
    assert found[0].family == "window" and found[0].machine == "local" and found[0].address() == 'type="window"'


def test_without_a_backend_there_is_no_window_target(monkeypatch):
    monkeypatch.setattr(backends, "support", lambda: {"available": False, "reason": "no X11"})
    assert desktop.providers(machines.local_machine({}), "conv") == []


def test_open_file_is_an_action_of_macos_only(computer):
    assert "open_file" in _app("Darwin").extra_actions()
    for system in ("Linux", "Windows"):
        assert "open_file" not in _app(system).extra_actions()
    assert "open_file" in _app("Darwin").surface().actions()


def test_a_missing_grant_keeps_the_target_listed_with_where_to_grant_it(computer, monkeypatch):
    monkeypatch.setattr(window_platforms, "_missing_grants", lambda system: ["accessibility"])
    said = _app().describe()
    assert "app names the application" in said.line
    assert said.missing == ("OpenProgram needs Accessibility (System Settings > Privacy & Security > "
                            "Accessibility).")


def test_the_note_names_the_system_and_its_keys(computer):
    note = _app("Darwin").note()
    assert "macOS" in note and "super is Command" in note and "open_file" in note


# ----------------------------------------------------------------- permission

def test_every_step_but_list_apps_asks_once_per_application(computer):
    app = _app()
    assert app.approval({}, [("list_apps", {})]) is None
    asked = app.approval({"app": "  Google   Chrome "}, [("click", {"element": 3})])
    assert asked.shown == "computer_use controls google chrome" and asked.rule == "window google chrome"
    unnamed = app.approval({}, [("get_state", {})])
    assert unnamed.shown == "computer_use controls an application" and unnamed.rule is None
    assert app.approval({}, [("click", {"app": "Notes"}), ("click", {})]).rule == "window notes"


def test_a_rule_names_the_application_however_the_model_named_it(computer, monkeypatch):
    def app_name(query):
        known = {"com.apple.terminal": "Terminal", "pid:123": "Terminal",
                 "/system/applications/utilities/terminal.app": "Terminal", "terminal": "Terminal"}
        return known.get(query.lower(), "")
    computer.app_name = app_name
    for named in ("com.apple.Terminal", "pid:123", "/System/Applications/Utilities/Terminal.app", "Terminal"):
        asked = _app().approval({"app": named}, [("click", {"element": 1})])
        assert asked.rule == "window terminal" and asked.shown == "computer_use controls terminal"
    assert _app().approval({"app": "Nowhere"}, [("click", {"element": 1})]).rule == "window nowhere"


def test_one_decision_resolves_the_rule_name_once_and_never_through_app_info(computer, monkeypatch):
    asked = []
    computer.app_name = lambda query: asked.append(query) or "Terminal"
    computer.app_info = lambda query: pytest.fail("app_info sleeps, scans every bundle and draws an icon")
    for _ in range(3):
        assert _app().approval({"app": "com.apple.Terminal"}, [("click", {"element": 1})]).rule == "window terminal"
    assert asked == ["com.apple.Terminal"]
    clock = iter([100.0, 100.0 + desktop._RULE_SECONDS + 1])
    monkeypatch.setattr(desktop.time, "monotonic", lambda: next(clock))
    desktop._rule_names.clear()
    for _ in range(2):
        _app().approval({"app": "com.apple.Terminal"}, [("click", {"element": 1})])
    assert len(asked) == 3


def test_a_backend_that_cannot_tell_keeps_the_name_given(computer):
    computer.app_name = lambda query: (_ for _ in ()).throw(RuntimeError("no bus"))
    assert _app().approval({"app": " Some  App "}, [("click", {"element": 1})]).rule == "window some app"


def test_the_tool_reads_approval_and_rule_from_the_provider(computer, monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.providers.screen import local as local_screen
    monkeypatch.setattr(local_screen, "providers", lambda machine, conversation: [])
    call = {"type": "window", "app": "Calculator", "action": "left_click", "args": {"element_index": 3}}
    assert entry.requires_approval(**call) == "computer_use controls calculator"
    assert entry.rule_key(call) == "window calculator"
    # The application may still be named inside args.
    assert entry.rule_key({"type": "window", "action": "click", "args": {"app": "Calculator", "element": 3}}) \
        == "window calculator"
    assert entry.requires_approval(type="window") is False
    assert entry.system_access({"type": "window"}) is None
    assert entry.system_access(call) == "computer_use:desktop"
    assert entry.authority(call) == "process.exec"


def test_desktop_access_is_needed_for_everything_but_listing_apps(computer):
    app = _app()
    assert app.system_access({}, [("list_apps", {})], False) is None
    assert app.system_access({}, [("list_apps", {})], True) == "computer_use:desktop"
    assert app.system_access({"app": "Notes"}, [("list_windows", {})], False) == "computer_use:desktop"


def test_background_steps_share_the_machine_and_hold_the_application(computer):
    machine, notes = locks.machine_key("local"), locks.application_key("local", "com.example.notes")
    held = _app("Darwin").lock({"app": "Notes"}, [("click", {"x": 3, "y": 4}), ("press_key", {"key": "super+s"})])
    assert held == locks.Locks(shared=(machine,), exclusive=(notes,))
    assert _app("Darwin").lock({}, [("list_apps", {})]) == locks.Locks()


@pytest.mark.parametrize(("system", "step", "whole"), [
    # Elements fall back to XTEST in the foreground when AT-SPI cannot carry the step out.
    ("Linux", ("click", {"element": 3}), True),
    ("Linux", ("click", {"x": 3, "y": 4}), True),
    ("Linux", ("scroll", {"element": 3, "direction": "down"}), True),
    ("Linux", ("type_text", {"text": "a"}), True),
    ("Linux", ("press_key", {"key": "Return"}), True),
    ("Linux", ("hover", {"element": 3}), True),
    ("Linux", ("set_value", {"element": 3, "value": "a"}), False),
    ("Linux", ("paste", {"text": "a"}), False),
    # Windows that take only real input get clicks, keys, text and ctrl+V through SendInput.
    ("Windows", ("press_key", {"key": "Return"}), True),
    ("Windows", ("press_key", {"key": "ctrl+s"}), True),
    ("Windows", ("click", {"x": 3, "y": 4}), True),
    ("Windows", ("click", {"element": 3}), True),
    ("Windows", ("type_text", {"text": "a"}), True),
    ("Windows", ("paste", {"text": "a"}), True),
    # UI Automation's SetFocus can bring the window to the front before the front is given back.
    ("Windows", ("focus", {"element": 3}), True),
    ("Windows", ("hover", {"element": 3}), False),
    ("Windows", ("scroll", {"x": 3, "y": 4, "direction": "down"}), False),
    ("Windows", ("set_value", {"element": 3, "value": "a"}), False),
])
def test_steps_that_need_the_real_pointer_or_the_foreground_hold_the_whole_machine(computer, system, step, whole):
    held = _app(system).lock({"app": "Notes"}, [step])
    machine = locks.machine_key("local")
    if whole:
        assert held == locks.Locks(exclusive=(machine,))
    else:
        assert held == locks.Locks(shared=(machine,),
                                   exclusive=(locks.application_key("local", "com.example.notes"),))


def test_resources_show_the_application_and_window(computer):
    row = _app().resource({"app": "Notes", "window": "Main"})
    assert (row.kind, row.title, row.detail) == ("desktop", "Notes", "Main")
    assert _app().resource({}) is None


# ----------------------------------------------------------------- reading

def test_get_state_reads_the_named_application_for_the_agent(computer):
    result = _app().get_state({"app": "Notes", "window_id": 7}, {"full": True}, _call())
    kind, session, app, options = computer.calls[-1]
    assert (kind, session, app) == ("get_state", "agent", "Notes")
    assert options["window_id"] == 7 and options["full"] is True and options["include"] == "all"
    assert callable(options["claim"])
    assert result.images == [b"png"] and "Accessibility tree" in result.text


def test_a_caller_reading_text_only_gets_the_tree_without_a_screenshot(computer):
    result = _app().get_state({"app": "Notes"}, {}, _call(images=False))
    assert computer.calls[-1][3]["include"] == "tree" and result.images == []


def test_get_state_without_an_application_is_a_bad_argument(computer):
    with pytest.raises(errors.BadArgument, match="list_apps"):
        _app().get_state({}, {}, _call())


def test_a_missing_grant_answers_permission_missing(computer):
    computer.fail["get_state"] = base.PermissionMissing("Grant Accessibility in System Settings.")
    with pytest.raises(errors.PermissionMissing, match="System Settings"):
        _app().get_state({"app": "Notes"}, {}, _call())


def test_lists_need_no_state(computer):
    assert _app().list("apps", {}, {}, _call()).text == "Running: Notes"
    assert _app().list("windows", {"app": "Notes"}, {}, _call()).text == "windows of Notes"
    with pytest.raises(errors.BadArgument):
        _app().list("windows", {}, {}, _call())


def test_the_content_another_target_operates_is_claimed_through_the_registry(computer, monkeypatch):
    asked = []
    monkeypatch.setattr(registry, "claim", lambda window, conversation: asked.append((window, conversation))
                        or ('type="browser", app="chrome"', "the page https://a.test/"))
    _app().get_state({"app": "Google Chrome"}, {}, _call())
    claim = computer.calls[-1][3]["claim"]
    assert claim({"app": "Google Chrome", "title": "A"}) == ('type="browser", app="chrome"', "the page https://a.test/")
    assert asked == [({"app": "Google Chrome", "title": "A"}, "conv")]


def test_claimed_content_is_left_out_and_the_window_keeps_its_controls():
    nodes = [tree.Node("AXWindow", 0), tree.Node("AXToolbar", 1), tree.Node("AXWebArea", 1),
             tree.Node("AXLink", 2), tree.Node("AXGroup", 3), tree.Node("AXButton", 1)]
    kept, elements, dropped = tree.leave_out(nodes, list("abcdef"), frozenset({"AXWebArea"}))
    assert [node.role for node in kept] == ["AXWindow", "AXToolbar", "AXButton"]
    assert elements == ["a", "b", "f"] and dropped == 3
    line = base.claim_line(('type="browser", app="chrome"', "a page of Google Chrome"))
    assert 'computer_use(type="browser", app="chrome")' in line and "own controls" in line
    assert base.claimed(None, {}) is None
    assert base.claimed(lambda window: (_ for _ in ()).throw(RuntimeError()), {}) is None


# ----------------------------------------------------------------- acting

def test_steps_reach_the_backend_in_our_names_and_the_state_follows(computer):
    result = _app().act({"app": "Notes"}, [("click", {"element": 3, "modifiers": "shift"})], _call())
    assert computer.calls[0] == ("click", "agent", {"element": 3, "modifiers": "shift", "app": "Notes"})
    assert result.text.startswith("Done: did click.\n\n") and result.images == [b"png"] and not result.is_error


def test_the_tool_rewrites_carried_over_names_before_the_provider_sees_them(computer, monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.providers.screen import local as local_screen
    monkeypatch.setattr(local_screen, "providers", lambda machine, conversation: [])
    result = entry.computer_use(type="window", app="Notes", action="double_click",
                                args={"element_index": 4, "duration": 1})
    assert computer.calls[0][0] == "click"
    assert computer.calls[0][2] == {"element": 4, "seconds": 1, "click_count": 2, "app": "Notes"}
    assert result.text.startswith("Done: did click.")


def test_a_batch_states_how_many_steps_are_done_and_where_it_stopped(computer):
    computer.fail["click"] = base.ComputerUseError("the app refused the click")
    result = _app().act({"app": "Notes"}, [("type_text", {"text": "3"}), ("click", {"element": 9}),
                                           ("press_key", {"key": "Return"})], _call())
    assert result.is_error
    assert result.text.startswith("Done:\n1. did type_text\nError: step 2 (click): the app refused the click "
                                  "The steps after it did not run.\n1 of 3 steps done.")
    assert [call[0] for call in computer.calls] == ["type_text", "click", "get_state"]


def test_a_stale_element_answers_element_gone_with_the_current_state(computer):
    computer.fail["click"] = base.StaleElement("element 9 no longer exists.")
    result = _app().act({"app": "Notes"}, [("type_text", {"text": "3"}), ("click", {"element": 9})], _call())
    assert result.is_error and "ELEMENT_GONE: element 9 no longer exists." in result.text
    assert "Accessibility tree" in result.text
    assert result.json_data == {"status": "observation_required", "action_dispatched": True, "next_step": 2}
    assert computer.calls[-1][3]["full"] is True


def test_a_step_that_sent_nothing_says_so(computer):
    computer.fail["click"] = base.BadArgument("x and y must be numbers")
    result = _app().act({"app": "Notes"}, [("click", {"x": "a", "y": 1})], _call())
    assert result.text == "BAD_ARGUMENT: x and y must be numbers"
    assert result.is_error and result.json_data == {"action_dispatched": False}
    computer.fail["click"] = base.NotDispatched("Call get_state for 'Notes' first")
    result = _app().act({"app": "Notes"}, [("click", {"element": 1})], _call())
    assert result.text.startswith("Error: Call get_state") and result.json_data == {"action_dispatched": False}


def test_an_application_that_stops_answering_after_a_step_leaves_the_outcome_unknown(computer):
    computer.fail["get_state"] = base.NotAnswering("Notes", 10)
    result = _app().act({"app": "Notes"}, [("click", {"element": 1})], _call())
    assert result.is_error and "Done: did click.\n\nOUTCOME_UNKNOWN: Notes has not answered" in result.text
    assert "Do not repeat the step" in result.text


def test_a_call_out_of_time_stops_between_steps(computer):
    call = _call()
    call.deadline = 0.0
    with pytest.raises(errors.Timeout, match="Nothing was sent") as caught:
        _app().act({"app": "Notes"}, [("click", {"element": 1})], call)
    assert computer.calls == [] and not caught.value.sent


def test_a_wait_for_text_out_of_time_names_the_steps_done(computer, monkeypatch):
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)
    checks = iter([None, None, errors.Timeout("the call ran out of time")])

    class _Late(Call):
        def check(self):
            failure = next(checks)
            if failure is not None:
                raise failure

    with pytest.raises(errors.Timeout) as caught:
        _app().act({"app": "Notes"}, [("click", {"element": 1}), ("wait", {"text": "Done", "seconds": 5})],
                   _Late(conversation="conv", agent="agent"))
    assert str(caught.value).startswith("Steps done: 1. did click. The call ran out of time during step 2 (wait)")
    assert caught.value.sent


def test_steps_may_not_name_another_application(computer):
    with pytest.raises(errors.BadArgument, match="names another app"):
        _app().act({"app": "Notes"}, [("click", {"element": 1}), ("click", {"app": "Mail", "element": 2})],
                   _call())
    with pytest.raises(errors.BadArgument, match="cannot run among several steps"):
        _app().act({"app": "Notes"}, [("find", {"text": "a"}), ("click", {"element": 2})], _call())
    with pytest.raises(errors.BadArgument, match="click needs app"):
        _app().act({}, [("click", {"element": 2})], _call())


def test_type_text_clears_and_submits_with_the_backends_own_steps(computer):
    result = _app().act({"app": "Notes"}, [("type_text", {"text": "hi", "element": 5, "clear": True,
                                                          "submit": True})], _call())
    assert [(call[0], call[2]) for call in computer.calls[:3]] == [
        ("set_value", {"app": "Notes", "element": 5, "value": ""}),
        ("type_text", {"app": "Notes", "text": "hi", "element": 5}),
        ("press_key", {"app": "Notes", "key": "Return"}),
    ]
    assert "cleared the field; did type_text; pressed Return" in result.text


def test_text_typed_before_a_later_key_fails_is_reported_done_and_never_typed_again(computer):
    computer.fail["press_key"] = base.ComputerUseError("The window is on another Space; keys cannot reach it.")
    result = _app().act({"app": "Notes"}, [("type_text", {"text": "hi", "submit": True})], _call())
    assert result.is_error and result.text.startswith(
        "Done: did type_text.\nError: the rest of the step failed: The window is on another Space")
    assert "Accessibility tree" in result.text
    assert [call[0] for call in computer.calls] == ["type_text", "press_key", "get_state"]
    computer.calls.clear()
    computer.fail["press_key"] = base.NotAnswering("Notes", 10)
    result = _app().act({"app": "Notes"}, [("type_text", {"text": "hi", "submit": True})], _call())
    # An application that stopped answering has no state to read.
    assert result.is_error and result.text.startswith("Done: did type_text.")
    assert [call[0] for call in computer.calls] == ["type_text", "press_key"]


def test_a_field_without_a_value_is_cleared_with_the_keyboard(computer):
    computer.fail["set_value"] = base.ComputerUseError("element 5 has no settable value")
    _app("Windows").act({"app": "Notes"}, [("type_text", {"text": "hi", "element": 5, "clear": True})], _call())
    assert [(call[0], call[2].get("key")) for call in computer.calls[:4]] == [
        ("set_value", None), ("press_key", "ctrl+a"), ("press_key", "BackSpace"), ("type_text", None)]


def test_wait_for_text_reads_until_the_window_shows_it(computer, monkeypatch):
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)
    texts = iter(["Loading", "Loading", "Saved to disk"])
    original = computer.get_state

    def reads(session, app, **options):
        computer.state_text = next(texts, "Saved to disk")
        return original(session, app, **options)
    computer.get_state = reads
    result = _app().act({"app": "Notes"}, [("wait", {"text": "saved", "seconds": 5})], _call())
    assert "waited until 'saved' showed" in result.text
    final = computer.calls[-1]
    assert final[0] == "get_state" and final[3]["full"] is True


def test_wait_for_text_reads_the_text_of_the_screenshot_of_a_window_with_little_tree(computer, monkeypatch):
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)
    original = computer.get_state

    computer.sparse = True

    def reads(session, app, **options):
        computer.state_text = ("Text on the screenshot (OCR):\nConnected" if options["include"] == "screenshot"
                               else "Accessibility tree:\n0 window")
        return original(session, app, **options)
    computer.get_state = reads
    result = _app().act({"app": "Notes"}, [("wait", {"text": "connected", "seconds": 5})], _call())
    assert "waited until 'connected' showed" in result.text
    assert [call[3]["include"] for call in computer.calls[:2]] == ["tree", "screenshot"]


def test_wait_for_text_takes_no_screenshot_of_a_window_whose_tree_is_full(computer, monkeypatch):
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)
    clock = iter(range(0, 1000))
    monkeypatch.setattr(desktop.time, "monotonic", lambda: float(next(clock)))
    result = _app().act({"app": "Notes"}, [("wait", {"text": "never", "seconds": 5})], _call())
    assert "'never' did not show" in result.text
    reads = [call[3].get("include") for call in computer.calls if call[0] == "get_state"]
    assert reads.count("tree") > 1 and "screenshot" not in reads


def test_another_agents_steps_are_named_in_the_next_state(computer):
    _app().act({"app": "Notes"}, [("click", {"element": 1})], _call("other"))
    _app().act({"app": "Notes"}, [("click", {"element": 1})], _call("other"))
    seen = _app().get_state({"app": "Notes"}, {}, _call("agent"))
    assert seen.text.startswith("note: another agent acted on Notes 2 time(s) since you last looked")
    again = _app().get_state({"app": "Notes"}, {}, _call("agent"))
    assert not again.text.startswith("note:")


def test_reading_steps_return_their_own_answer(computer):
    assert _app().act({"app": "Notes"}, [("find", {"text": "Save"})], _call()).text == "found Save"
    # With a last get_state it is two steps, and find runs alone.
    with pytest.raises(errors.BadArgument, match="find cannot run among several steps"):
        _app().act({"app": "Notes"}, [("find", {"text": "Save"}), ("get_state", {})], _call())
    zoomed = _app().act({"app": "Notes"}, [("zoom", {"x": 1, "y": 2, "width": 3, "height": 4})], _call(images=False))
    assert zoomed.text == "zoomed" and zoomed.images == []


def test_the_end_of_an_agent_drops_its_window_states(computer):
    import threading
    computer._lock = threading.RLock()
    taken = []

    class _Watched(dict):
        def __iter__(self):
            taken.append(computer._lock._is_owned())
            return super().__iter__()
    computer._states = _Watched(computer._states)
    _app().close(_call("agent"))
    # Other agents' reads write the states under the driver's lock.
    assert taken and all(taken)
    assert list(computer._states) == [("other", 1)]


def test_the_guide_names_only_the_families_arguments():
    import re
    from openprogram.programs.tools.computer_use.computer_use.actions import vocabulary
    from pathlib import Path
    guide = (Path(desktop.__file__).parents[2] / "guides" / "window.md").read_text(encoding="utf-8")
    family = vocabulary.FAMILIES["window"]
    for action in (*vocabulary.CORE, *family.extras):
        for found in re.finditer(rf"(?<![\w.]){action}\(([^)]*)\)", guide):
            names = set(re.findall(r"[a-z_]+", found.group(1)))
            allowed = set(family.arguments(action)) | set(family.shared) | {"x", "y", "true", "false", "all", "tree",
                                                                            "screenshot", "left", "right", "middle",
                                                                            "up", "down", "text", "md", "html",
                                                                            "cursor_before", "cursor_after"}
            assert names <= allowed, (action, names - allowed)
    for old in ("element_index", "get_app_state", "(duration", " duration=", "key=held", "\"vm\""):
        assert old not in guide
