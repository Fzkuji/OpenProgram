"""The local ``screen`` target: one whole display, the real pointer and keyboard.

Every test drives a fake driver: no screenshot is taken and no event is
posted. The fake records what would have been sent, so a refused step can
be shown to have sent nothing.
"""
from __future__ import annotations

from pathlib import Path
import re

import pytest

from openprogram.host.machines import Machine
from openprogram.programs.tools.computer_use.computer_use import errors, locks, vocabulary
from openprogram.programs.tools.computer_use.computer_use.contract import Call
from openprogram.programs.tools.computer_use.computer_use.desktop import base
from openprogram.programs.tools.computer_use.computer_use.desktop.screen_macos import (
    Display, Owner, hit_row, scroll_ticks)
from openprogram.programs.tools.computer_use.computer_use.dispatch import run_steps
from openprogram.programs.tools.computer_use.computer_use.providers import local_screen
from openprogram.programs.tools.computer_use.computer_use.providers.local_screen import LocalScreen, Policy
from openprogram.runtime.permissions.refusal import Refused

MAIN = Display(1, (0.0, 0.0), (1512.0, 982.0), backing=2.0, main=True)
SIDE = Display(7, (1512.0, -200.0), (2560.0, 1440.0), backing=1.0, main=False)
FINDER = Owner(301, "com.apple.finder", "Finder")
APP = Owner(9001, "ai.openprogram.desktop", "OpenProgram")
DEV_APP = Owner(9002, "ai.openprogram.desktop.dev", "OpenProgram Dev")
SAFE = Owner(400, "com.example.vault", "Vault")
PNG = b"\x89PNG\r\n\x1a\nfake"


class FakeDriver:
    system = "macOS"
    select_all = "super+a"
    submit = "Return"

    def __init__(self) -> None:
        self.granted = {"screen_recording": True, "accessibility": True}
        self.is_locked = False
        self.shown = [MAIN, SIDE]
        self.under: dict[tuple[float, float], Owner] = {}
        self.default_owner = FINDER
        self.front_owner = FINDER
        self.secure = False
        self.password_focus = False
        self.sent: list[tuple] = []
        self.captures: list[dict] = []
        self.where = (10.0, 10.0)

    def grants(self):
        return dict(self.granted)

    def locked(self):
        return self.is_locked

    def version(self):
        return "15.1"

    def displays(self):
        return list(self.shown)

    def capture(self, display, scale, *, region=None, hidden=lambda owner: False):
        self.captures.append({"display": display.id, "scale": scale, "region": region,
                              "hidden": [owner.label for owner in (FINDER, APP, DEV_APP, SAFE) if hidden(owner)]})
        return PNG

    def front(self):
        return self.front_owner

    def owner_at(self, point):
        return self.under.get((round(point[0], 3), round(point[1], 3)), self.default_owner)

    def secure_input(self):
        return self.secure

    def focus_is_password(self):
        return self.password_focus

    def pointer(self):
        return self.where

    def move(self, point):
        self.sent.append(("move", point))

    def click(self, point, button, count, modifiers="", hold=0.0):
        self.sent.append(("click", point, button, count, modifiers, hold))

    def drag(self, start, end, seconds):
        self.sent.append(("drag", start, end, seconds))

    def scroll(self, point, direction, pages, pixels):
        self.sent.append(("scroll", point, direction, pages, pixels))

    def press(self, chord, hold=0.0):
        self.sent.append(("press", chord, hold))

    def type(self, text):
        self.sent.append(("type", text))

    def paste(self, text):
        self.sent.append(("paste", text))


@pytest.fixture(autouse=True)
def _quick(monkeypatch):
    monkeypatch.setattr(local_screen, "SETTLE_SECONDS", 0)
    monkeypatch.setattr(local_screen.time, "sleep", lambda seconds: None)
    local_screen._shots.clear()
    yield
    local_screen._shots.clear()


@pytest.fixture
def driver():
    return FakeDriver()


@pytest.fixture
def policy():
    return {"value": Policy(mode="default")}


@pytest.fixture
def screen(driver, policy):
    return LocalScreen(Machine("local", "This computer"), "conv", driver=lambda: driver,
                       policy=lambda: policy["value"])


def _call(images: bool = True, check=None) -> Call:
    return Call(conversation="conv", agent="agent-1", check_cancelled=check, images=images)


def _run(screen, steps, shared=None, call=None):
    checked, subject = vocabulary.check(screen.surface(), steps, dict(shared or {}))
    return run_steps(screen, subject, checked, call or _call())


def _look(screen, display=None):
    return _run(screen, [("get_state", {})], {"display": display} if display is not None else {})


# -- the target -------------------------------------------------------------------

def test_off_until_the_owner_turns_it_on(monkeypatch):
    from openprogram.host import machines
    from openprogram.programs.tools.computer_use.computer_use import registry
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: FakeDriver)
    monkeypatch.setattr("openprogram.programs.tools.computer_use.computer_use.providers.desktop.supported",
                        lambda: False)
    config: dict = {}
    monkeypatch.setattr(machines.user_config, "read_config_value", lambda key: config.get(key))
    monkeypatch.setattr(machines.user_config, "read_config", lambda: config)
    assert "screen" not in registry.providers("conv")
    config["computer_use"] = {"whole_screen": "yes"}
    assert "screen" not in registry.providers("conv")
    config["computer_use"] = {"whole_screen": True}
    assert "screen" in registry.providers("conv")


def test_providers_only_where_a_driver_exists(monkeypatch):
    machine = Machine("local", "This computer")
    monkeypatch.setattr(local_screen.platform, "system", lambda: "Linux")
    assert local_screen.providers(machine, "conv") == []
    monkeypatch.setattr(local_screen.platform, "system", lambda: "Windows")
    assert local_screen.providers(machine, "conv") == []
    monkeypatch.setattr(local_screen, "_driver_for", lambda system: FakeDriver)
    found = local_screen.providers(machine, "conv")
    assert [provider.target for provider in found] == ["screen"]


def test_it_is_a_pixels_only_screen_with_its_extras(screen):
    assert (screen.target, screen.family, screen.machine) == ("screen", "screen", "local")
    assert screen.capabilities == {"pixels", "pointer", "hover", "keyboard", "displays"}
    actions = screen.surface().actions()
    assert {"get_state", "click", "type_text", "press_key", "scroll", "drag", "hover", "wait",
            "list_displays", "zoom", "paste"} == set(actions)
    with pytest.raises(errors.NotSupported):
        vocabulary.check_step(screen.surface(), "set_value", {"element": 3, "value": "x"})
    with pytest.raises(errors.NotSupported):
        vocabulary.check_step(screen.surface(), "click", {"element": 3})


def test_describe_names_a_missing_grant(screen, driver):
    assert screen.describe().missing == ""
    assert "moves the user's pointer" in screen.describe().line
    driver.granted["screen_recording"] = False
    missing = screen.describe().missing
    assert "Screen Recording" in missing and "Accessibility" not in missing


def test_note_says_this_computer_and_the_real_pointer(screen):
    note = screen.note()
    assert "this computer (macOS 15.1)" in note
    assert "the user sees the pointer move" in note
    assert "super is the Command key" in note


# -- permission answers -----------------------------------------------------------

def test_permission_answers(screen):
    steps = [("click", {"x": 1, "y": 1})]
    asked = screen.approval({}, steps)
    assert asked.rule == "screen" and asked.shown == "computer_use controls the whole screen of this computer"
    assert screen.approval({}, [("list_displays", {})]) is None
    assert screen.authority(steps, False) == "process.exec"
    assert screen.system_access({}, steps, False) == "computer_use:desktop"
    assert screen.system_access({}, [("get_state", {})], False) == "computer_use:desktop"
    assert screen.system_access({}, [("list_displays", {})], False) is None
    assert screen.lock({}, steps) == locks.Locks(exclusive=(locks.machine_key("local"),))
    assert not screen.lock({}, [("list_displays", {})])
    row = screen.resource({"display": 7})
    assert (row.kind, row.title, row.detail) == ("desktop", "Screen", "display 7")
    assert screen.resource({}).detail == "main display"


def test_a_missing_grant_answers_permission_missing(screen, driver):
    driver.granted["accessibility"] = False
    result = _run(screen, [("click", {"x": 5, "y": 5})])
    assert result.is_error and result.text.startswith("PERMISSION_MISSING:")
    assert "Accessibility" in result.text
    assert driver.sent == []


def test_a_locked_screen_sends_nothing(screen, driver):
    driver.is_locked = True
    result = _look(screen)
    assert result.is_error and "locked" in result.text
    assert result.json_data == {"action_dispatched": False}
    assert driver.captures == []


# -- reading ----------------------------------------------------------------------

def test_list_displays_names_ids_sizes_and_the_main_one(screen):
    result = _run(screen, [("list_displays", {})])
    assert "- 1 (main): 1512x982 points at 2x" in result.text
    assert "- 7: 2560x1440 points at 1x, top left at 1512,-200" in result.text


def test_get_state_is_one_display_at_a_capped_scale(screen, driver):
    result = _look(screen)
    assert result.images == [PNG]
    assert driver.captures[0]["display"] == 1
    assert driver.captures[0]["scale"] == pytest.approx(1568 / 1512)
    assert "the screenshot is 1568x1018 pixels" in result.text
    assert "Front application: Finder." in result.text


def test_an_unknown_display_lists_the_displays(screen, driver):
    result = _look(screen, display=3)
    assert result.text.startswith("BAD_ARGUMENT:") and "- 7: 2560x1440" in result.text
    assert driver.captures == []


def test_screenshots_leave_out_refused_and_own_applications(screen, driver, policy):
    policy["value"] = Policy(refused=frozenset({"vault"}), mode="default")
    _look(screen)
    assert driver.captures[-1]["hidden"] == ["OpenProgram", "OpenProgram Dev", "Vault"]
    policy["value"] = Policy(refused=frozenset({"vault"}), bypass=True, mode="bypass")
    _look(screen)
    assert driver.captures[-1]["hidden"] == ["Vault"]


def test_text_only_reads_take_no_screenshot(screen, driver):
    result = _run(screen, [("get_state", {})], call=_call(images=False))
    assert result.images == [] and driver.captures == []
    assert "no image comes back" in result.text
    _run(screen, [("click", {"x": 1568 / 2, "y": 10})], call=_call(images=False))
    assert driver.sent[0][1] == pytest.approx((756.0, 982 * 10 / 1018))


def test_zoom_is_a_region_at_full_resolution(screen, driver):
    _look(screen)
    result = _run(screen, [("zoom", {"x": 100, "y": 200, "width": 300, "height": 150})])
    ratio = 1568 / 1512
    zoomed = driver.captures[-1]
    assert zoomed["scale"] == 2.0
    assert zoomed["region"] == pytest.approx((100 / ratio, 200 / ratio, 300 / ratio, 150 / ratio))
    assert result.images == [PNG] and driver.sent == []


# -- pointer steps ----------------------------------------------------------------

def test_x_y_needs_a_screenshot_of_that_display(screen, driver):
    result = _run(screen, [("click", {"x": 5, "y": 5})])
    assert result.text.startswith("BAD_ARGUMENT:") and "get_state" in result.text
    assert driver.sent == []


def test_pixels_map_to_points_through_scale_and_origin(screen, driver):
    _look(screen, display=7)
    result = _run(screen, [("click", {"x": 784, "y": 441, "modifiers": "cmd"})], {"display": 7})
    assert not result.is_error
    kind, point, button, count, modifiers, hold = driver.sent[0]
    assert (kind, button, count, modifiers) == ("click", "left", 1, "super")
    assert point == pytest.approx((1512 + 1280, -200 + 720))
    assert result.images == [PNG] and "Done: clicked." in result.text


def test_a_point_outside_the_screenshot_is_refused(screen, driver):
    _look(screen)
    result = _run(screen, [("click", {"x": 1568, "y": 5})])
    assert result.text.startswith("BAD_ARGUMENT:") and driver.sent == []


def test_carried_over_names_reach_the_driver(screen, driver):
    _look(screen)
    _run(screen, [("double_click", {"coordinate": [10, 10]})])
    _run(screen, [("left_click_drag", {"start_coordinate": [10, 10], "coordinate": [20, 30]})])
    assert driver.sent[0][0] == "click" and driver.sent[0][3] == 2
    assert driver.sent[1][0] == "drag"


def test_a_point_on_openprogram_is_refused_outside_bypass(screen, driver, policy):
    _look(screen)
    shot = local_screen._shots[("agent-1", 1)]
    for app in (APP, DEV_APP):
        driver.under[tuple(round(v, 3) for v in shot.point(100, 100))] = app
        with pytest.raises(Refused) as refused:
            _run(screen, [("click", {"x": 100, "y": 100})])
        assert refused.value.refusal.code == "COMPUTER_USE_OWN_WINDOW"
    assert driver.sent == []
    policy["value"] = Policy(bypass=True, mode="bypass")
    assert not _run(screen, [("click", {"x": 100, "y": 100})]).is_error
    assert driver.sent[0][0] == "click"


def test_a_point_on_a_refused_application_is_refused(screen, driver, policy):
    policy["value"] = Policy(refused=frozenset({"com.example.vault"}), bypass=True, mode="bypass")
    _look(screen)
    driver.default_owner = SAFE
    for step in (("click", {"x": 1, "y": 1}), ("hover", {"x": 1, "y": 1}), ("scroll", {"x": 1, "y": 1})):
        with pytest.raises(Refused) as refused:
            _run(screen, [step])
        assert refused.value.refusal.code == "COMPUTER_USE_REFUSED_APP"
    assert driver.sent == []


def test_a_drag_onto_openprogram_is_refused(screen, driver):
    _look(screen)
    shot = local_screen._shots[("agent-1", 1)]
    driver.under[tuple(round(v, 3) for v in shot.point(500, 500))] = APP
    with pytest.raises(Refused):
        _run(screen, [("drag", {"x": 10, "y": 10, "to_x": 500, "to_y": 500})])
    assert driver.sent == []


def test_a_scroll_without_a_point_is_judged_where_the_pointer_is(screen, driver):
    driver.under[(10.0, 10.0)] = APP
    with pytest.raises(Refused):
        _run(screen, [("scroll", {"direction": "down"})])
    driver.under.clear()
    _run(screen, [("scroll", {"direction": "up", "pages": 2})])
    assert driver.sent == [("scroll", None, "up", 2.0, 0.0)]


# -- key and text steps -----------------------------------------------------------

def test_keys_are_refused_while_openprogram_is_in_front(screen, driver):
    driver.front_owner = DEV_APP
    with pytest.raises(Refused) as refused:
        _run(screen, [("press_key", {"key": "Return"})])
    assert refused.value.refusal.code == "COMPUTER_USE_OWN_WINDOW"
    assert driver.sent == []


def test_secure_input_refuses_keys_whatever_the_setting(screen, driver, policy):
    driver.secure = True
    policy["value"] = Policy(bypass=True, mode="bypass", refuse_password_fields=False)
    for step in (("type_text", {"text": "hunter2"}), ("press_key", {"key": "a"}), ("paste", {"text": "x"})):
        result = _run(screen, [step])
        assert result.is_error and "Secure keyboard entry" in result.text
        assert result.json_data == {"action_dispatched": False}
    assert driver.sent == []


def test_password_fields_follow_the_setting(screen, driver, policy):
    driver.password_focus = True
    assert not _run(screen, [("type_text", {"text": "abc"})]).is_error
    policy["value"] = Policy(mode="default", refuse_password_fields=True)
    with pytest.raises(Refused) as refused:
        _run(screen, [("type_text", {"text": "abc"})])
    assert refused.value.refusal.code == "COMPUTER_USE_PASSWORD_FIELD"
    assert driver.sent == [("type", "abc")]


def test_type_text_clears_and_submits(screen, driver):
    _run(screen, [("type", {"text": "Ada", "clear": True, "submit": True})])
    assert driver.sent == [("press", "super+a", 0.0), ("press", "BackSpace", 0.0), ("type", "Ada"),
                           ("press", "Return", 0.0)]


def test_keys_arrive_in_one_spelling(screen, driver):
    _run(screen, [("hotkey", {"keys": ["Control", "Shift", "Tab"]})])
    _run(screen, [("press_key", {"key": "cmd+c"})])
    assert [sent[1] for sent in driver.sent] == ["ctrl+shift+Tab", "super+c"]


# -- batches ----------------------------------------------------------------------

def test_a_batch_stops_at_a_refusal_and_says_what_was_done(screen, driver):
    _look(screen)
    driver.front_owner = APP
    result = _run(screen, [("click", {"x": 10, "y": 10}), ("type_text", {"text": "hi"}),
                           ("press_key", {"key": "Return"})])
    assert result.is_error
    assert "1. click: clicked" in result.text
    assert "step 2 (type_text) failed: [blocked by mode]" in result.text
    assert "1 of 3 steps done." in result.text
    assert [sent[0] for sent in driver.sent] == ["click"]
    assert result.images == [PNG]


def test_a_batch_that_times_out_names_the_steps_that_took_effect(screen, driver):
    _look(screen)
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] > 1:
            raise errors.Timeout("late")

    result = _run(screen, [("click", {"x": 10, "y": 10}), ("click", {"x": 20, "y": 20})], call=_call(check=check))
    assert result.text.startswith("TIMEOUT:") and "1. click" in result.text
    assert len(driver.sent) == 1


def test_a_timeout_after_input_says_input_was_sent(screen, driver):
    _look(screen)
    calls = {"n": 0}

    def check():
        calls["n"] += 1
        if calls["n"] > 1:
            raise errors.Timeout("late")

    result = _run(screen, [("click", {"x": 10, "y": 10}), ("click", {"x": 20, "y": 20})], call=_call(check=check))
    assert result.text.startswith("TIMEOUT:") and result.json_data is None
    calls["n"] = 0
    result = _run(screen, [("list_displays", {}), ("click", {"x": 20, "y": 20})], call=_call(check=check))
    assert result.text.startswith("TIMEOUT:") and result.json_data == {"action_dispatched": False}


def test_a_get_state_among_the_steps_counts_as_a_step(screen, driver):
    _look(screen)
    steps = [("click", {"x": 10, "y": 10}), ("get_state", {}), ("type_text", {"text": "hi"})]
    result = _run(screen, steps)
    assert not result.is_error and "3 of 3 steps done." in result.text
    driver.front_owner = APP
    result = _run(screen, steps)
    assert "step 3 (type_text) failed: [blocked by mode]" in result.text
    assert "2 of 3 steps done." in result.text


def test_an_unknown_key_name_is_a_bad_argument(screen, driver, monkeypatch):
    from openprogram.programs.tools.computer_use.computer_use.desktop import screen_macos
    monkeypatch.setattr(screen_macos, "_frameworks", lambda: (None, None, None, None))
    with pytest.raises(base.BadArgument, match="Hyper_L"):
        object.__new__(screen_macos.MacScreen).press("Hyper_L")

    def press(chord, hold=0.0):
        raise base.BadArgument(f"unknown key {chord!r}")
    monkeypatch.setattr(driver, "press", press)
    result = _run(screen, [("press_key", {"key": "Hyper_L"})])
    assert result.text.startswith("BAD_ARGUMENT: unknown key") and result.json_data == {"action_dispatched": False}
    _look(screen)
    result = _run(screen, [("click", {"x": 10, "y": 10}), ("press_key", {"key": "Hyper_L"})])
    assert "step 2 (press_key) failed: BAD_ARGUMENT: unknown key" in result.text


def test_wait_sends_nothing_and_returns_a_screenshot(screen, driver):
    result = _run(screen, [("sleep", {"duration": 0.05})])
    assert driver.sent == [] and result.images == [PNG] and "waited 0.05 s" in result.text


def test_close_forgets_the_agents_screenshots(screen, driver):
    _look(screen)
    screen.close(_call())
    assert _run(screen, [("click", {"x": 5, "y": 5})]).text.startswith("BAD_ARGUMENT:")


# -- the driver's pure parts ------------------------------------------------------

def _row(pid, x, y, width, height, layer=0, alpha=1, name=""):
    return {"kCGWindowOwnerPID": pid, "kCGWindowLayer": layer, "kCGWindowAlpha": alpha, "kCGWindowName": name,
            "kCGWindowBounds": {"X": x, "Y": y, "Width": width, "Height": height}}


def test_the_hit_test_passes_over_windows_that_let_events_through():
    rows = [
        _row(9001, 0, 0, 2000, 2000, layer=2147483630, name="OpenProgram agent overlay"),
        _row(4706, 0, 0, 2000, 2000, layer=2147483632),
        _row(10, 0, 0, 2000, 37, layer=24, name="Menubar"),
        _row(20, 0, 0, 2000, 2000, layer=20, name="Dock"),
        _row(77, 0, 0, 500, 500, alpha=0),
        _row(9001, 600, 600, 100, 100, layer=101),
        _row(55, 100, 100, 50, 50),
        _row(66, 0, 0, 400, 400),
    ]
    own = lambda pid: pid == 9001  # noqa: E731
    assert hit_row(rows, (120, 120), own)["kCGWindowOwnerPID"] == 55
    assert hit_row(rows, (10, 100), own)["kCGWindowOwnerPID"] == 66
    assert hit_row(rows, (10, 10), own)["kCGWindowOwnerPID"] == 10
    # A menu of OpenProgram's own, above the Dock's level, still counts.
    assert hit_row(rows, (650, 650), own)["kCGWindowOwnerPID"] == 9001
    assert hit_row(rows, (900, 900), own) is None


def test_both_apps_and_this_process_count_as_openprogram():
    import os
    assert APP.own and DEV_APP.own and Owner(1, "ai.openprogram.runtime").own
    assert Owner(os.getpid()).own
    assert not FINDER.own and not Owner(2, "com.openprogram.other").own


def test_scroll_ticks_reveal_content_in_the_direction_asked():
    assert scroll_ticks("down", 1, 0)[1] < 0 and scroll_ticks("up", 1, 0)[1] > 0
    ticks, dy, dx, in_pixels = scroll_ticks("right", 1, 200)
    assert in_pixels and dx < 0 and dy == 0 and ticks * abs(dx) == 200


# -- the guide --------------------------------------------------------------------

def test_the_guide_has_one_line_per_action_in_the_spec_names():
    guide = (Path(base.__file__).parent.parent / "guides" / "screen.md").read_text(encoding="utf-8")
    lines = re.findall(r"^- (\w+)(?:\(|:)", guide, flags=re.MULTILINE)
    family = vocabulary.FAMILIES["screen"]
    wanted = [name for name in vocabulary.CORE if name not in ("find", "set_value")] + list(family.extras)
    assert sorted(lines) == sorted(wanted)
    for name in vocabulary.carried("screen"):
        assert f"- {name}(" not in guide
    # Shortcuts differ by system; they are in the target note.
    assert "super+" not in guide
