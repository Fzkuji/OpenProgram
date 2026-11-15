"""computer_use_agent under several agents and long pages: lookups, reach, and what an error leaves behind."""
from __future__ import annotations

import pytest

from openprogram.programs._authoring import guides
from openprogram.programs.tools.computer_use.computer_use_agent import code as code_mode
from openprogram.programs.tools.computer_use.computer_use_agent import tool as entry
from openprogram.programs.tools.computer_use.computer_use_agent.desktop import macos
from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import NotAnswering, NotDispatched
from openprogram.programs.tools.computer_use.computer_use_agent.targets import app as app_target


def test_a_running_app_missed_by_one_scan_is_still_found(monkeypatch):
    # NSRunningApplication's lookup answers nil for a live app now and then.
    computer = macos.MacComputer()
    answers = iter([None, "chrome"])
    monkeypatch.setattr(computer, "_running_match_once", lambda query: next(answers))
    monkeypatch.setattr(macos.time, "sleep", lambda _: None)
    assert computer._running_match("Google Chrome") == "chrome"


def test_an_app_missed_by_every_scan_is_not_running(monkeypatch):
    computer = macos.MacComputer()
    scans = []
    monkeypatch.setattr(computer, "_running_match_once", lambda query: scans.append(query))
    monkeypatch.setattr(macos.time, "sleep", lambda _: None)
    assert computer._running_match("Ghost") is None
    assert len(scans) == macos._MATCH_ATTEMPTS


def _page(monkeypatch, frames, actions=("AXScrollToVisible",)):
    """A web page 800x600 at the origin; the element's frame is read from ``frames`` in turn."""
    computer = macos.MacComputer()
    state = macos.WindowState(pid=1, window_title="Form", origin=(0.0, 0.0), size=(800.0, 600.0))
    performed = []
    reads = iter(frames)
    last = [None]

    def frame(element):
        if element == "page":
            return (0.0, 100.0, 800.0, 500.0)    # the web area, below an 100 pt toolbar
        last[0] = next(reads, last[0])
        return last[0]

    monkeypatch.setattr(computer, "_frame", frame)
    monkeypatch.setattr(computer, "_attr", lambda element, name: (
        "page" if (element == "field" and name == "AXParent") else
        "AXWebArea" if (element == "page" and name == "AXRole") else None))
    monkeypatch.setattr(computer, "_actions", lambda element: list(actions))
    monkeypatch.setattr(computer, "_perform", lambda element, action: performed.append(action) or 0)
    monkeypatch.setattr(computer, "_follow_window", lambda state: None)
    monkeypatch.setattr(macos.time, "sleep", lambda _: None)
    return computer, state, performed


def test_an_element_in_view_is_reached_where_it_is(monkeypatch):
    computer, state, performed = _page(monkeypatch, [(100.0, 200.0, 50.0, 20.0)])
    assert computer._reachable_center(state, "field") == (125.0, 210.0)
    assert performed == []


def test_an_element_scrolled_under_the_toolbar_is_scrolled_into_view_first(monkeypatch):
    # Inside the window but above the page: a click there would hit the toolbar.
    below = (100.0, 300.0, 50.0, 20.0)
    computer, state, performed = _page(monkeypatch, [(100.0, 40.0, 50.0, 20.0), below, below])
    assert computer._reachable_center(state, "field") == (125.0, 310.0)
    assert performed == ["AXScrollToVisible"]


def test_a_row_of_a_native_list_is_scrolled_by_its_scroll_bar(monkeypatch):
    # A list 300 pt tall over 2000 pt of rows, without AXScrollToVisible: row 35 sits at 1760-1790.
    computer = macos.MacComputer()
    state = macos.WindowState(pid=1, window_title="Form", origin=(0.0, 0.0), size=(360.0, 300.0))
    rows = {"row": (10.0, 1760.0, 300.0, 30.0), "area": (0.0, 0.0, 360.0, 300.0), "contents": (0.0, 0.0, 340.0, 2000.0)}
    attrs = {("row", "AXParent"): "contents", ("contents", "AXParent"): "area", ("area", "AXRole"): "AXScrollArea",
             ("area", "AXContents"): ["contents"], ("area", "AXVerticalScrollBar"): "bar"}
    moved = []

    def set_value(element, name, value):
        moved.append((element, round(value, 3)))
        offset = value * (2000.0 - 300.0)
        rows["row"] = (10.0, 1760.0 - offset, 300.0, 30.0)
        return 0

    monkeypatch.setattr(computer, "_frame", lambda element: rows.get(element))
    monkeypatch.setattr(computer, "_attr", lambda element, name: attrs.get((element, name)))
    monkeypatch.setattr(computer, "_actions", lambda element: [])
    monkeypatch.setattr(computer, "_settable", lambda element, name: element == "bar")
    monkeypatch.setattr(computer, "_set", set_value)
    monkeypatch.setattr(computer, "_follow_window", lambda state: None)
    monkeypatch.setattr(macos.time, "sleep", lambda _: None)
    x, y = computer._reachable_center(state, "row")
    assert moved == [("bar", round((1775.0 - 150.0) / 1700.0, 3))]
    assert 0 <= y <= 300 and x == 160.0


def test_a_hidden_option_says_to_open_its_menu(monkeypatch):
    computer, state, _ = _page(monkeypatch, [(0.0, 0.0, 0.0, 0.0)])
    with pytest.raises(NotDispatched, match="Open what holds it"):
        computer._reachable_center(state, "field")


def test_a_step_that_never_reached_the_app_reports_nothing_dispatched(monkeypatch):
    class Computer:
        def act(self, session, action, step):
            raise NotDispatched("element_index 7 is not in the last state of this window.")

    monkeypatch.setattr("openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends.backend",
                        lambda: Computer())
    result = app_target.run([("click", {"element_index": 7})], {"app": "Google Chrome"}, "s")
    assert result.is_error
    assert result.json_data == {"action_dispatched": False}


def test_each_agent_of_a_conversation_reads_the_screen_under_its_own_key():
    from openprogram.runtime.execution import run_control
    from openprogram.runtime.jobs.runner import _current_job_id

    # Every turn of the conversation has an execution of its own; its turns still share what they read.
    keys = []
    for turn in ("exec_first", "exec_second"):
        token = run_control.set_current_execution_id(turn)
        try:
            keys.append(entry.operator("chat"))
        finally:
            run_control.reset_current_execution_id(token)
    assert keys == ["chat", "chat"]
    # An agent started in the conversation runs as a job beside them.
    token = _current_job_id.set("j_child")
    try:
        child = entry.operator("chat")
    finally:
        _current_job_id.reset(token)
    assert child != "chat" and child.startswith("chat")


def test_one_agents_code_keeps_its_interpreter_when_another_agent_runs_code(monkeypatch):
    closed = []

    class Runner:
        _closed = False

        def __init__(self, broker):
            self.name = broker

        def __enter__(self):
            return self

        def close(self):
            closed.append(self.name)

    class Broker:
        def __init__(self, **kwargs):
            self.execution = kwargs["execution_id"]

        def bind_invocation(self, invocation):
            pass

    import openprogram.programs.tools.computer_use.computer_use_agent.sandbox.broker as broker_module
    import openprogram.programs.tools.computer_use.computer_use_agent.sandbox.runner as runner_module
    import openprogram.runtime.execution.control.shared as shared
    monkeypatch.setattr(broker_module, "GuiBroker", Broker)
    monkeypatch.setattr(runner_module, "GuiPythonRunner", lambda broker: Runner(broker.execution))
    monkeypatch.setattr(shared, "default_control_service", lambda: type("S", (), {"effects": None})())
    monkeypatch.setattr(code_mode, "_runners", {})
    for execution in ("main", "j_child", "main"):
        monkeypatch.setattr(code_mode, "_identity", lambda e=execution: (e, "attempt", 1, "call"))
        code_mode._runner("chat")
    assert closed == []
    assert len(code_mode._runners) == 2


def test_a_guide_in_a_prompt_from_another_agent_counts_as_read():
    from openprogram.models.providers.types import TextContent, UserMessage
    from openprogram.runtime.permissions.authority import render_model_input
    import openprogram.programs.tools.computer_use.computer_use_agent.tool  # noqa: F401  registers the guides

    prompt = "Fill the form.\n\n" + entry.guide("app")
    wrapped = render_model_input(prompt, speaker_kind="runtime", speaker_id="runtime/agent%2Fmain",
                                 speaker_display="agent/main")
    token = guides.begin_loop()
    try:
        guides.observe_request([UserMessage(role="user", content=[TextContent(text=wrapped)], timestamp=0)])
        assert guides.missing_required(entry.NAME, {"target": "app", "code": "print(1)"}) == []
    finally:
        guides.end_loop(token)


# --- one agent per application at a time

class _Desk:
    """An application backend that records what reached it, in order."""

    def __init__(self, keys=None):
        self.log = []
        self.keys = keys or {}

    def app_key(self, app):
        return self.keys.get(app, "")

    def act(self, session, action, step):
        self.log.append((session, action, step.get("element_index")))
        return f"did {action}"

    def settle(self, session, app):
        self.log.append((session, "settle", None))

    def get_state(self, session, app, **kwargs):
        from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import Observation
        self.log.append((session, "state", None))
        return Observation(text="tree", image=None, app=app)


@pytest.fixture
def desk(monkeypatch):
    computer = _Desk()
    monkeypatch.setattr("openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends.backend",
                        lambda: computer)
    for name in ("_locks", "_acted", "_seen"):
        monkeypatch.setattr(app_target, name, {})
    return computer


def test_a_call_holds_its_application_from_its_first_step_to_the_state_it_returns(desk, monkeypatch):
    held = []
    act, state = desk.act, desk.get_state
    monkeypatch.setattr(desk, "act", lambda *args: held.append(app_target._locks["notes"].locked()) or act(*args))
    monkeypatch.setattr(desk, "get_state", lambda *args, **kwargs: (
        held.append(app_target._locks["notes"].locked()) or state(*args, **kwargs)))
    app_target.run([("click", {"element_index": 1}), ("click", {"element_index": 2})], {"app": "Notes"}, "a")
    assert held == [True, True, True]
    assert not app_target._locks["notes"].locked()
    # A failing step frees the application too.
    monkeypatch.setattr(desk, "act", lambda *args: (_ for _ in ()).throw(NotDispatched("nothing there")))
    assert app_target.run([("click", {"element_index": 1})], {"app": "Notes"}, "a").is_error
    assert not app_target._locks["notes"].locked()


def test_a_result_says_when_another_agent_acted_since_this_one_looked(desk):
    app_target.run([("get_app_state", {})], {"app": "Notes"}, "a")
    app_target.run([("click", {"element_index": 4})], {"app": "Notes"}, "b")
    app_target.run([("click", {"element_index": 5})], {"app": "Notes"}, "b")
    result = app_target.run([("click", {"element_index": 1})], {"app": "Notes"}, "a")
    assert result.text.startswith("note: another agent acted on Notes 2 time(s) since you last looked")
    # Having seen that result, the agent is told nothing more until someone acts again.
    assert not app_target.run([("click", {"element_index": 1})], {"app": "Notes"}, "a").text.startswith("note:")
    # Its own steps, and another agent's reads, are not news to it.
    app_target.run([("get_app_state", {})], {"app": "Notes"}, "b")
    assert not app_target.run([("get_app_state", {})], {"app": "Notes"}, "a").text.startswith("note:")


def test_the_note_waits_for_a_result_that_shows_the_application(desk, monkeypatch):
    app_target.run([("get_app_state", {})], {"app": "Notes"}, "a")
    app_target.run([("click", {"element_index": 4})], {"app": "Notes"}, "b")
    act = desk.act
    monkeypatch.setattr(desk, "act", lambda *args: (_ for _ in ()).throw(NotDispatched("element 9 is hidden")))
    failed = app_target.run([("click", {"element_index": 9})], {"app": "Notes"}, "a")
    # Nothing was sent and no state came back: the news is kept for the next result that has one.
    assert failed.is_error and "note:" not in failed.text
    monkeypatch.setattr(desk, "act", act)
    assert app_target.run([("click", {"element_index": 1})], {"app": "Notes"}, "a").text.startswith(
        "note: another agent acted on Notes 1 time(s)")
    # The other agent is told of the click that went through, not of the one that never reached the application.
    assert app_target.run([("get_app_state", {})], {"app": "Notes"}, "b").text.startswith(
        "note: another agent acted on Notes 1 time(s)")
    assert app_target.run([("get_app_state", {})], {"app": "Notes"}, "b").text == "tree"


def test_an_application_named_two_ways_is_one_application(desk):
    desk.keys = {"Google Chrome": "com.google.Chrome", "com.google.chrome": "com.google.Chrome"}
    app_target.run([("get_app_state", {})], {"app": "Google Chrome"}, "a")
    app_target.run([("click", {"element_index": 4})], {"app": "com.google.chrome"}, "b")
    assert app_target.run([("get_app_state", {})], {"app": "Google Chrome"}, "a").text.startswith("note:")
    # Another application is held and counted on its own.
    app_target.run([("click", {"element_index": 4})], {"app": "Notes"}, "b")
    assert not app_target.run([("get_app_state", {})], {"app": "Google Chrome"}, "a").text.startswith("note:")


def test_listing_applications_waits_for_no_one(desk, monkeypatch):
    desk.list_apps = lambda: "apps"
    with app_target._registry:
        lock = app_target._locks.setdefault("notes", __import__("threading").Lock())
    lock.acquire()
    try:
        assert app_target.run([("list_apps", {})], {}, "b").text == "apps"
    finally:
        lock.release()


# --- an element the app replaced since it was read

def _nodes(*rows):
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.tree import Node
    return [Node(role=role, depth=depth, label=label, value=value) for depth, role, label, value in rows]


def _read(rows, handles):
    """A window state as get_app_state leaves it: numbered, with the app's element handles."""
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop import tree
    nodes = _nodes(*rows)
    fresh = tree.number(nodes, None)
    return macos.WindowState(pid=1, window_title="Form", window_id=7,
                             elements={node.index: handle for node, handle in zip(nodes, handles)},
                             nodes=nodes, by_index={node.index: node for node in nodes},
                             snapshot=tree.snapshot(nodes, fresh))


_FORM = [(0, "AXWindow", "Form", ""), (1, "AXTextField", "Name", ""), (1, "AXTextField", "School", ""),
         (1, "AXButton", "Save", "")]


def test_a_replaced_element_is_found_again_under_its_index():
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop import tree
    state = _read(_FORM, ["w", "name", "school", "save"])
    seen = state.snapshot
    # The page drew itself again: a new line above, a value typed by another agent, new handles throughout.
    again = _nodes((0, "AXWindow", "Form", ""), (1, "AXStaticText", "Saved a moment ago", ""),
                   (1, "AXTextField", "Name", "Li"), (1, "AXTextField", "School", ""), (1, "AXButton", "Save", ""))
    assert tree.rebind(state, 2, again, ["w2", "note2", "name2", "school2", "save2"]) == "school2"
    assert state.elements[3] == "save2" and state.by_index[1].value == "Li"
    # What the model last saw is kept, so the state after its step reports the new line and the value.
    assert state.snapshot is seen
    assert "Saved a moment ago" in tree.diff(seen, again)[0]


def test_an_index_whose_line_now_reads_something_else_is_not_taken():
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop import tree
    state = _read(_FORM, ["w", "name", "school", "save"])
    before = dict(state.elements)
    again = _nodes((0, "AXWindow", "Form", ""), (1, "AXTextField", "Name", ""), (1, "AXTextField", "Company", ""),
                   (1, "AXButton", "Save", ""))
    assert tree.rebind(state, 2, again, ["w2", "name2", "company2", "save2"]) is None
    gone = _nodes((0, "AXWindow", "Form", ""), (1, "AXTextField", "Name", ""))
    assert tree.rebind(state, 3, gone, ["w2", "name2"]) is None
    assert state.elements == before


def test_an_unnamed_control_that_changed_is_left_to_a_fresh_read():
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop import tree
    rows = [(0, "AXWindow", "Form", ""), (1, "AXTextField", "", "a"), (1, "AXTextField", "", "b")]
    state = _read(rows, ["w", "first", "second"])
    # Read again exactly as it was, the field is the same one.
    assert tree.rebind(state, 2, _nodes(*rows), ["w2", "first2", "second2"]) == "second2"
    # With another value there is nothing to tell it from its neighbour by.
    changed = _nodes((0, "AXWindow", "Form", ""), (1, "AXTextField", "", "a"), (1, "AXTextField", "", "c"))
    assert tree.rebind(state, 2, changed, ["w3", "first3", "second3"]) is None


class _Ax:
    """Accessibility calls for elements named by strings; the ones in ``dead`` are gone."""

    def __init__(self, dead):
        self.dead = set(dead)

    def AXUIElementCopyAttributeValue(self, element, name, _):
        return (macos._INVALID_ELEMENT, None) if element in self.dead else (0, "AXButton")

    def AXUIElementCreateApplication(self, pid):
        return "root"

    def AXUIElementSetMessagingTimeout(self, element, seconds):
        return 0


def _mac(monkeypatch, dead, rows, handles, windows=("window",)):
    computer = macos.MacComputer()
    monkeypatch.setattr(macos, "_frameworks", lambda: (None, _Ax(dead), None, None))
    monkeypatch.setattr(computer, "_windows", lambda root: list(windows))
    monkeypatch.setattr(computer, "_ax_window_id", lambda window: 7)
    reads = []
    monkeypatch.setattr(computer, "_read_tree", lambda root, window: (
        reads.append(window) or _nodes(*rows), list(handles), True))
    return computer, reads


def test_a_step_on_a_replaced_element_reads_the_window_again_and_goes_on(monkeypatch):
    state = _read(_FORM, ["w", "name", "school", "save"])
    computer, reads = _mac(monkeypatch, {"save"}, _FORM, ["w2", "name2", "school2", "save2"])
    assert computer._element(state, 3) == "save2"
    assert reads == ["window"]
    # A live element is used as it is, without another read.
    assert computer._element(state, 1) == "name2" and reads == ["window"]


def test_a_step_on_an_element_that_is_really_gone_still_says_so(monkeypatch):
    state = _read(_FORM, ["w", "name", "school", "save"])
    computer, _ = _mac(monkeypatch, {"save"}, _FORM[:3], ["w2", "name2", "school2"])
    with pytest.raises(NotDispatched, match="element 3 no longer exists"):
        computer._element(state, 3)
    # So does a window that closed.
    computer, reads = _mac(monkeypatch, {"save"}, _FORM, ["w2", "name2", "school2", "save2"], windows=())
    with pytest.raises(NotDispatched, match="element 3 no longer exists"):
        computer._element(state, 3)
    assert reads == []


# --- an application busy with the step it was sent

class _Clock:
    """A clock the fake Accessibility layer moves: a request that waits takes that long."""

    def __init__(self):
        self.now = 100.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _BusyAx:
    """An Accessibility layer whose app answers a request after ``busy`` seconds, or not within the wait."""

    def __init__(self, clock, busy=0.0, refuses=None):
        self.clock, self.busy, self.refuses = clock, busy, refuses
        self.timeouts, self.requests = {}, []

    def AXUIElementCreateApplication(self, pid):
        return f"app {pid}"

    def AXUIElementSetMessagingTimeout(self, element, seconds):
        self.timeouts[element] = seconds
        return 0

    def _ask(self, element, what):
        self.requests.append((element, what))
        if self.refuses is not None:
            return self.refuses                     # could not be asked at all: no time passes
        wait = self.timeouts.get(element, 1.5)
        if self.busy > wait:
            self.clock.now += wait
            self.busy -= wait
            return macos._NOT_ANSWERED
        self.clock.now += self.busy
        self.busy = 0.0
        return 0

    def AXUIElementPerformAction(self, element, action):
        return self._ask(element, action)

    def AXUIElementSetAttributeValue(self, element, name, value):
        return self._ask(element, name)

    def AXUIElementCopyAttributeValue(self, element, name, _):
        return self._ask(element, name), "AXApplication"


def _busy(monkeypatch, busy=0.0, refuses=None):
    clock = _Clock()
    ax = _BusyAx(clock, busy, refuses)
    monkeypatch.setattr(macos, "_frameworks_cached", lambda: (None, ax, None, None))
    monkeypatch.setattr(macos.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(macos.time, "sleep", clock.sleep)
    monkeypatch.setattr(macos, "_check_cancelled", lambda: None)
    return macos.MacComputer(), ax, clock


class _App:
    def processIdentifier(self):
        return 7

    def localizedName(self):
        return "Notes"


def test_a_press_the_app_is_still_carrying_out_counts_as_taken(monkeypatch):
    # A button that saves for 4 s: macOS gives up waiting for the answer, the app got the press once.
    computer, ax, _ = _busy(monkeypatch, busy=4.0)
    assert computer._perform("save", "AXPress") == 0
    assert ax.timeouts["save"] == macos.ACTION_TIMEOUT and ax.requests == [("save", "AXPress")]
    # A value the app is slow to take is the same.
    computer, ax, _ = _busy(monkeypatch, busy=4.0)
    assert computer._set("field", "AXValue", "Ada") == 0
    # The same code at once is a request that could not be made, and other codes are refusals.
    for code in (macos._NOT_ANSWERED, -25205):
        computer, _, _ = _busy(monkeypatch, refuses=code)
        assert computer._perform("save", "AXPress") == code
        assert computer._set("field", "AXValue", "Ada") == code


def test_a_slow_button_is_pressed_once_and_never_clicked_as_well(monkeypatch):
    computer, ax, _ = _busy(monkeypatch, busy=2.0)
    state = macos.WindowState(pid=7, window_title="Form", origin=(0.0, 0.0), size=(800.0, 600.0))
    monkeypatch.setattr(computer, "_element", lambda state, index: "save")
    monkeypatch.setattr(computer, "_node_at", lambda state, index: type("Node", (), {
        "role": "AXButton", "body": lambda self: 'button "Save"'})())
    monkeypatch.setattr(computer, "_actions", lambda element: ["AXPress"])
    monkeypatch.setattr(computer, "_background_click", lambda *args: pytest.fail("the press already reached the app"))
    assert computer._do_click(_App(), 7, state, {"element_index": 3}) == 'pressed button "Save"'
    assert [what for _, what in ax.requests] == ["AXPress"]


def _message_cell(monkeypatch, computer, *, press, selected_after_press=False):
    """Outlook's message list: a cell that lists AXPress, inside a row that takes AXSelected."""
    attrs = {("cell", "AXParent"): "row", ("row", "AXRole"): "AXRow", ("row", "AXSelected"): False}
    calls = []

    def perform(element, action):
        calls.append((element, action))
        if selected_after_press:
            attrs[("row", "AXSelected")] = True
        return press

    def set_(element, name, value):
        calls.append((element, name, value))
        attrs[(element, name)] = value
        return 0
    monkeypatch.setattr(computer, "_element", lambda state, index: "cell")
    monkeypatch.setattr(computer, "_node_at", lambda state, index: type("Node", (), {
        "role": "AXCell", "body": lambda self: 'cell "Sender: ICDM"'})())
    monkeypatch.setattr(computer, "_actions", lambda element: ["AXPress"])
    monkeypatch.setattr(computer, "_attr", lambda element, name: attrs.get((element, name)))
    monkeypatch.setattr(computer, "_settable", lambda element, name: (element, name) == ("row", "AXSelected"))
    monkeypatch.setattr(computer, "_perform", perform)
    monkeypatch.setattr(computer, "_set", set_)
    monkeypatch.setattr(computer, "_background_click", lambda *args: pytest.fail("a row is selected without the mouse"))
    return calls


def test_a_message_in_a_list_is_selected_through_its_row_and_never_with_the_mouse(monkeypatch):
    """A mouse-down lets the app bring its window over the user's; selecting the row does not."""
    state = macos.WindowState(pid=7, window_title="Inbox", origin=(0.0, 0.0), size=(800.0, 600.0))
    # The press is refused and changes nothing: the row is selected instead.
    computer = macos.MacComputer.__new__(macos.MacComputer)
    calls = _message_cell(monkeypatch, computer, press=-25205)
    assert computer._do_click(_App(), 7, state, {"element_index": 3}) == 'selected cell "Sender: ICDM"'
    assert calls == [("cell", "AXPress"), ("row", "AXSelected", True)]
    # The press answers with an error and selects the row all the same: nothing more is sent.
    computer = macos.MacComputer.__new__(macos.MacComputer)
    calls = _message_cell(monkeypatch, computer, press=-25205, selected_after_press=True)
    assert computer._do_click(_App(), 7, state, {"element_index": 3}) == 'selected cell "Sender: ICDM"'
    assert calls == [("cell", "AXPress")]


def test_only_a_request_left_unanswered_means_the_app_is_busy(monkeypatch):
    computer, ax, _ = _busy(monkeypatch, busy=3.0)
    assert computer._answers(7) is False and ax.timeouts["app 7"] == macos.ANSWER_TIMEOUT
    computer, _, _ = _busy(monkeypatch)
    assert computer._answers(7) is True
    # A process that cannot be asked (no Accessibility in it, access not granted) is not a busy one.
    for code in (macos._NOT_ANSWERED, -25211):
        computer, _, _ = _busy(monkeypatch, refuses=code)
        assert computer._answers(7) is True


def test_a_busy_app_is_waited_for_and_one_that_stays_silent_is_reported(monkeypatch):
    computer, _, clock = _busy(monkeypatch, busy=3.2)
    computer._await_answer(_App())
    assert 3.2 <= clock.now - 100.0 < 3.2 + macos.ANSWER_TIMEOUT
    computer, _, clock = _busy(monkeypatch, busy=600.0)
    with pytest.raises(NotAnswering, match=r"Notes has not answered for 10 s.*Nothing was sent") as failure:
        computer._await_answer(_App())
    assert isinstance(failure.value, NotDispatched)
    assert macos.UNANSWERED_MAX <= clock.now - 100.0 < macos.UNANSWERED_MAX + macos.ANSWER_TIMEOUT


def test_reads_and_steps_wait_for_the_app_before_asking_it_anything(monkeypatch):
    computer, _, clock = _busy(monkeypatch, busy=600.0)
    monkeypatch.setattr(macos, "_frameworks", lambda: macos._frameworks_cached())
    monkeypatch.setattr(computer, "resolve_app", lambda query, launch=True: _App())
    monkeypatch.setattr(computer, "_windows", lambda root: pytest.fail("a busy app's windows read as closed"))
    monkeypatch.setattr(computer, "_do_click", lambda *args: pytest.fail("a step sent now runs whenever it comes back"))
    # A step on a window the model never read fails at once, without waiting for the app.
    with pytest.raises(NotDispatched, match="Call get_app_state"):
        computer.act("s", "click", {"app": "Notes", "element_index": 1})
    assert clock.now == 100.0
    computer._states[("s", 7)] = macos.WindowState(pid=7, window_title="Form")
    for call in (lambda: computer.get_state("s", "Notes"),
                 lambda: computer.list_windows("Notes"),
                 lambda: computer.act("s", "click", {"app": "Notes", "element_index": 1}),
                 lambda: computer.settle("s", "Notes")):
        with pytest.raises(NotAnswering):
            call()
    # Waiting is how the model lets the app finish, so it is never refused.
    assert computer.act("s", "wait", {"app": "Notes", "duration": 2}) == "waited 2s"


def test_a_step_the_app_is_still_carrying_out_is_done_and_has_no_state_yet(desk, monkeypatch):
    settle, state = desk.settle, desk.get_state
    monkeypatch.setattr(desk, "settle", lambda session, app: (_ for _ in ()).throw(NotAnswering("Notes", 10.2)))
    monkeypatch.setattr(desk, "get_state", lambda *args, **kwargs: pytest.fail("the app is not answering"))
    result = app_target.run([("click", {"element_index": 3})], {"app": "Notes"}, "a")
    assert not result.is_error and result.json_data is None
    assert result.text.startswith("Done: did click.\n\nNotes has not answered since (10 s)")
    assert "Do not repeat the step" in result.text
    # The step still counts as acting on the application for the agents that share it.
    monkeypatch.setattr(desk, "settle", settle)
    monkeypatch.setattr(desk, "get_state", state)
    assert app_target.run([("get_app_state", {})], {"app": "Notes"}, "b").text.startswith(
        "note: another agent acted on Notes 1 time(s)")


def test_a_step_refused_because_the_app_is_busy_sent_nothing(desk, monkeypatch):
    monkeypatch.setattr(desk, "act", lambda *args: (_ for _ in ()).throw(NotAnswering("Notes", 10.0)))
    result = app_target.run([("click", {"element_index": 3})], {"app": "Notes"}, "a")
    assert result.is_error and result.json_data == {"action_dispatched": False}
    assert result.text.startswith("Error: Notes has not answered for 10 s")
    # Among several steps, the ones before it went out; the app is not waited for a second time.
    sent = []

    def act(session, action, step):
        if sent:
            raise NotAnswering("Notes", 10.0)
        sent.append(action)
        return "did click"

    monkeypatch.setattr(desk, "act", act)
    monkeypatch.setattr(desk, "settle", lambda session, app: pytest.fail("the app was already waited for"))
    result = app_target.run([("click", {"element_index": 1}), ("click", {"element_index": 2})], {"app": "Notes"}, "a")
    assert result.is_error and result.json_data is None
    assert result.text == ("Done:\n1. did click\nError: step 2 (click) failed: Notes has not answered for 10 s: it is "
                           "busy (still carrying out its last step, or not responding). Nothing was sent, and its "
                           "windows are still open. Wait (action wait), then try again. The steps after it did not run.")


def test_a_step_that_was_sent_stays_done_when_its_result_cannot_be_read(desk, monkeypatch):
    # A click on Quit: the step went out, and the app it left is gone when the state is read.
    monkeypatch.setattr(desk, "settle", lambda session, app: (_ for _ in ()).throw(NotDispatched("'Notes' is not running")))
    result = app_target.run([("click", {"element_index": 3})], {"app": "Notes"}, "a")
    assert not result.is_error and result.json_data is None
    assert result.text == "Done: did click.\n\nIts state could not be read afterwards: 'Notes' is not running"


@pytest.mark.parametrize("prior", [False, True])
def test_stale_control_returns_fresh_state_without_replaying_steps(prior):
    from types import SimpleNamespace
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import StaleElement, Observation
    calls = []
    def act(session, action, args):
        calls.append(action)
        if action == "click":
            raise StaleElement("old control gone")
        return "typed"
    def state(session, app, **kw):
        assert kw == {"full": True, "as_diff": False}
        return Observation("fresh controls", None, app)
    computer = SimpleNamespace(act=act, get_state=state)
    turn = SimpleNamespace(acted=False, state=lambda text: text)
    steps = ([("type", {"text": "hello"})] if prior else []) + [("click", {"element_index": 120}), ("click", {"element_index": 121})]
    result = app_target._act(computer, steps, "Chrome", "s", True, turn)
    assert not result.is_error
    assert "fresh controls" in result.text and "did not run" in result.text
    assert result.json_data["status"] == "observation_required"
    assert result.json_data["action_dispatched"] is prior
    assert calls == (["type", "click"] if prior else ["click"])
