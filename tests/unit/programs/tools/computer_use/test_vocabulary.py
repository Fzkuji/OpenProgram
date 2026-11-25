"""One spelling per meaning: the actions, families, carried-over names and gating of the design's tables."""
from __future__ import annotations

import pytest

from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.actions import vocabulary
from openprogram.programs.tools.computer_use.computer_use.actions.vocabulary import Surface, check, check_step

TREE = frozenset({"pixels", "tree", "pointer", "hover", "keyboard"})
PIXELS = frozenset({"pixels", "pointer", "hover", "keyboard"})

APP = Surface("window", "window", TREE, tuple(vocabulary.FAMILIES["window"].extras))
BROWSER = Surface("browser", "browser", TREE, tuple(vocabulary.FAMILIES["browser"].extras))
SCREEN = Surface("lab screen", "screen", PIXELS)


# -- the tables -------------------------------------------------------------------

def test_the_core_actions_and_their_arguments_are_the_designs():
    assert vocabulary.CORE == {
        "get_state": ("include", "full"),
        "click": ("element", "x", "y", "mouse_button", "click_count", "modifiers", "seconds"),
        "type_text": ("text", "element", "clear", "submit"),
        "press_key": ("key", "element", "seconds"),
        "scroll": ("element", "x", "y", "direction", "pages", "pixels"),
        "drag": ("element", "x", "y", "to_element", "to_x", "to_y", "seconds"),
        "hover": ("element", "x", "y", "seconds"),
        "wait": ("seconds", "text"),
        "find": ("text", "role"),
        "set_value": ("element", "value"),
    }


@pytest.mark.parametrize("family, shared, extras", [
    ("window", ("app", "window", "window_id"),
     {"list_apps", "list_windows", "zoom", "focus", "show_window", "menu", "paste", "copy", "select_text",
      "perform_secondary_action", "open_file", "start_audio_recording", "stop_audio_recording"}),
    ("screen", ("display",), {"list_displays", "zoom", "paste"}),
    ("browser", ("tab",),
     {"list_tabs", "create_tab", "close_tab", "navigate", "back", "forward", "reload", "paste", "upload_file",
      "evaluate", "read_console_messages", "read_network_requests", "list_panes", "start_video_recording",
      "stop_video_recording"}),
])
def test_each_family_has_its_shared_arguments_and_extra_actions(family, shared, extras):
    table = vocabulary.FAMILIES[family]
    assert table.shared == shared
    assert set(table.extras) == extras


def test_a_target_offers_only_the_extras_its_provider_names():
    chrome = Surface("chrome", "browser", TREE, ("list_tabs", "navigate"))
    assert "list_panes" not in chrome.actions() and "list_tabs" in chrome.actions()
    with pytest.raises(errors.NotSupported, match="list_panes is not available on chrome"):
        check_step(chrome, "list_panes", {})


# -- carried-over names ----------------------------------------------------------

@pytest.mark.parametrize("written, ours, implied", [
    ("screenshot", "get_state", {"include": "screenshot"}),
    ("get_app_state", "get_state", {}),
    ("read", "get_state", {}),
    ("snapshot", "get_state", {}),
    ("read_page", "get_state", {}),
    ("left_click", "click", {}),
    ("tap", "click", {}),
    ("double_click", "click", {"click_count": 2}),
    ("right_click", "click", {"mouse_button": "right"}),
    ("long_press", "click", {"seconds": 1}),
    ("type", "type_text", {}),
    ("write", "type_text", {}),
    ("input_text", "type_text", {}),
    ("press", "press_key", {}),
    ("key", "press_key", {}),
    ("keypress", "press_key", {}),
    ("hotkey", "press_key", {}),
    ("scroll_at", "scroll", {}),
    ("left_click_drag", "drag", {}),
    ("drag_and_drop", "drag", {}),
    ("move", "hover", {}),
    ("mouse_move", "hover", {}),
    ("sleep", "wait", {}),
    ("fill", "set_value", {}),
    ("select", "set_value", {}),
    ("select_option", "set_value", {}),
])
def test_every_carried_over_action_is_rewritten_to_the_one_spelling(written, ours, implied):
    for family in ("window", "browser"):
        assert vocabulary.canonical(family, written) == (ours, implied)
        assert written in vocabulary.carried(family)


@pytest.mark.parametrize("written, step, expected", [
    ("double_click", {"element": 3}, ("click", {"element": 3, "click_count": 2})),
    ("right_click", {"x": 1, "y": 2}, ("click", {"x": 1, "y": 2, "mouse_button": "right"})),
    ("left_click", {"coordinate": [5, 6]}, ("click", {"x": 5, "y": 6})),
    ("click", {"element_index": 3}, ("click", {"element": 3})),
    ("click", {"ref": "e12"}, ("click", {"element": "e12"})),
    ("click", {"button": "middle", "element": 1}, ("click", {"element": 1, "mouse_button": "middle"})),
    ("hover", {"element": 1, "duration": 2}, ("hover", {"element": 1, "seconds": 2})),
    ("left_click_drag", {"start_coordinate": [1, 2], "coordinate": [3, 4]},
     ("drag", {"x": 1, "y": 2, "to_x": 3, "to_y": 4})),
    ("drag_and_drop", {"ref": "e1", "to_ref": "e2"}, ("drag", {"element": "e1", "to_element": "e2"})),
    ("hotkey", {"keys": ["Control", "C"]}, ("press_key", {"key": "ctrl+c"})),
    ("press", {"key": "Enter"}, ("press_key", {"key": "Return"})),
    ("type", {"value": "hi"}, ("type_text", {"text": "hi"})),
    ("scroll", {"amount": 300, "scroll_direction": "down", "x": 1, "y": 1},
     ("scroll", {"pixels": 300, "direction": "down", "x": 1, "y": 1})),
    ("get_app_state", {"changes_only": True}, ("get_state", {"full": False})),
    ("sleep", {"duration": 1}, ("wait", {"seconds": 1})),
    # Claude's computer tool: modifiers as text, wheel clicks, a zoom region by its corners.
    ("left_click", {"coordinate": [1, 2], "text": "shift"}, ("click", {"x": 1, "y": 2, "modifiers": "shift"})),
    ("scroll", {"coordinate": [1, 2], "scroll_direction": "down", "scroll_amount": 3},
     ("scroll", {"x": 1, "y": 2, "direction": "down", "pixels": 300.0})),
    ("zoom", {"region": [10, 20, 110, 70]}, ("zoom", {"x": 10, "y": 20, "width": 100, "height": 50})),
])
def test_carried_over_arguments_are_rewritten_and_never_refused(written, step, expected):
    assert check_step(APP, written, step) == expected


def test_a_screen_zoom_takes_a_region_too():
    screen = Surface("screen", "screen", PIXELS, ("zoom",))
    assert check_step(screen, "zoom", {"region": [0, 0, 50, 40]}) == \
        ("zoom", {"x": 0, "y": 0, "width": 50, "height": 40})


@pytest.mark.parametrize("region", [["a", "b", "c", "d"], [0, 0, None, 10], [0, 0, True, 10], [0, 0, "nan", 10],
                                    [0, 0, float("inf"), 10]])
def test_a_region_that_is_not_four_numbers_is_a_bad_argument(region):
    with pytest.raises(errors.BadArgument, match=r"region is \[x0, y0, x1, y1\]"):
        check_step(APP, "zoom", {"region": region})
    assert check_step(APP, "zoom", {"region": ["10", "20", "110", "70"]}) == \
        ("zoom", {"x": 10.0, "y": 20.0, "width": 100.0, "height": 50.0})


def test_click_text_is_read_as_held_keys_only_when_it_names_modifiers():
    assert check_step(APP, "left_click", {"element": 1, "text": "cmd+shift"}) == \
        ("click", {"element": 1, "modifiers": "shift+super"})
    with pytest.raises(errors.BadArgument, match=r"click does not take text; it is click\("):
        check_step(APP, "click", {"x": 1, "y": 2, "text": "Sign in"})
    with pytest.raises(errors.BadArgument, match="click does not take key"):
        check_step(APP, "click", {"x": 1, "y": 2, "key": "ctrl+a"})
    # Other tools send an empty text when no key is held.
    assert check_step(APP, "left_click", {"x": 1, "y": 2, "text": ""}) == ("click", {"x": 1, "y": 2, "modifiers": ""})


def test_app_opens_an_application_by_reading_it():
    assert check_step(APP, "open", {"app": "Safari"}) == ("get_state", {"app": "Safari"})
    # A generic word names the operation of the family it was written for, never another family's.
    assert vocabulary.counterpart("browser", "open") == "open"
    assert vocabulary.counterpart("browser", "goto") == "navigate"


def test_a_page_action_carries_its_arguments_over():
    assert check_step(BROWSER, "select_option", {"ref": "e3", "values": ["F1"]}) == \
        ("set_value", {"element": "e3", "value": "F1"})
    assert check_step(BROWSER, "evaluate", {"expression": "1 + 1"}) == ("evaluate", {"script": "1 + 1"})
    assert check_step(BROWSER, "goto", {"url": "https://example.com"}) == \
        ("navigate", {"url": "https://example.com"})
    assert check_step(BROWSER, "tabs", {}) == ("list_tabs", {})
    assert check_step(BROWSER, "new_tab", {"url": "about:blank"}) == ("create_tab", {"url": "about:blank"})
    assert check_step(BROWSER, "upload", {"ref": "e1", "path": "/tmp/a"}) == \
        ("upload_file", {"element": "e1", "path": "/tmp/a"})
    assert check_step(BROWSER, "click", {"role": "button", "name": "Go"}) == \
        ("click", {"role": "button", "name": "Go"})


def test_application_openers_are_get_state_with_the_application():
    steps, subject = check(APP, [("open_app", {})], {"app": "Calculator"})
    assert steps == [("get_state", {})] and subject == {"app": "Calculator"}
    assert vocabulary.canonical("window", "launch_app") == ("get_state", {})


def test_a_rewrite_never_drops_items():
    # A list of keys is one chord, not its first key.
    assert check_step(APP, "press_key", {"key": ["ctrl", "shift", "Tab"]}) == ("press_key", {"key": "ctrl+shift+Tab"})
    assert check_step(APP, "click", {"element": 1, "modifiers": ["cmd", "shift"]}) == \
        ("click", {"element": 1, "modifiers": "shift+super"})


def test_our_own_argument_wins_over_a_carried_one():
    # Both written: ours is kept and the other is reported, never silently merged.
    with pytest.raises(errors.BadArgument, match="does not take ref"):
        check_step(APP, "click", {"element": 2, "ref": "e1"})


# -- refusals ---------------------------------------------------------------------

def test_an_unknown_action_names_the_targets_actions():
    with pytest.raises(errors.UnknownAction) as caught:
        check_step(APP, "teleport", {})
    text = caught.value.text()
    assert text.startswith("UNKNOWN_ACTION: window has no action 'teleport'.")
    assert "Actions here: get_state, click, type_text" in text


def test_an_unknown_action_offers_the_nearest_of_the_family():
    with pytest.raises(errors.UnknownAction, match=r"Did you mean click\("):
        check_step(APP, "clik", {})


def test_an_action_of_another_family_names_the_targets_of_the_same_machine_that_have_it():
    app = Surface("window", "window", TREE, (), lambda action: ["browser"] if action == "navigate" else [])
    with pytest.raises(errors.UnknownAction) as caught:
        check_step(app, "navigate", {"url": "https://example.com"})
    assert "it is an action of browser" in caught.value.text()


def test_a_missing_open_file_names_the_step_that_works():
    app = Surface("window", "window", TREE, ("list_apps", "menu"))
    with pytest.raises(errors.NotSupported) as caught:
        check_step(app, "open_file", {"path": "/tmp/a.txt"})
    assert ("window does not have open_file; open it from the application: get_state(app), then menu File > Open, "
            "or press_key ctrl+o.") in caught.value.text()


def test_an_action_the_family_has_and_the_target_lacks_is_not_supported_with_the_target_that_has_it():
    chrome = Surface("chrome", "browser", TREE, ("list_tabs",),
                     lambda action: ["browser"] if action == "list_panes" else [])
    with pytest.raises(errors.NotSupported) as caught:
        check_step(chrome, "list_panes", {})
    text = caught.value.text()
    assert text.startswith("NOT_SUPPORTED: list_panes is not available on chrome")
    assert "browser has it" in text


def test_a_bad_argument_answers_with_the_actions_signature():
    with pytest.raises(errors.BadArgument) as caught:
        check_step(BROWSER, "click", {"selector": "#go"})
    assert caught.value.text() == (
        "BAD_ARGUMENT: click does not take selector; it is "
        "click(element, x, y, mouse_button, click_count, modifiers, seconds, role, name, tab).")


def test_a_signature_names_the_familys_shared_arguments_too():
    with pytest.raises(errors.BadArgument) as caught:
        check(APP, [("open_app", {})], {"name": "Calculator"})
    assert caught.value.text() == ("BAD_ARGUMENT: get_state does not take name; it is "
                                   "get_state(include, full, app, window, window_id).")


def test_a_step_of_a_batch_is_named_in_its_refusal():
    with pytest.raises(errors.BadArgument, match="^step 2: "):
        check(APP, [("click", {"element": 1}), ("click", {"selector": "x"})], {"app": "Notes"})


# -- gating -----------------------------------------------------------------------

def test_a_target_without_a_tree_refuses_element_arguments_and_tree_actions():
    for action, step in (("click", {"element": 3}), ("click", {"element_index": 3}),
                         ("drag", {"element": 1, "to_x": 2, "to_y": 3})):
        with pytest.raises(errors.NotSupported, match="pixels only"):
            check_step(SCREEN, action, step)
    for action in ("find", "set_value"):
        with pytest.raises(errors.NotSupported, match=f"{action} is not available on lab screen"):
            check_step(SCREEN, action, {})
    with pytest.raises(errors.NotSupported):
        check_step(SCREEN, "get_state", {"include": "tree"})
    with pytest.raises(errors.NotSupported, match="wait cannot look for text"):
        check_step(SCREEN, "wait", {"text": "Saved"})
    assert check_step(SCREEN, "click", {"x": 1, "y": 2}) == ("click", {"x": 1, "y": 2})
    assert "element" not in SCREEN.signature("click")


@pytest.mark.parametrize("missing, refused, works", [
    ("hover", "hover", "click"),
    ("pointer", "click", "type_text"),
    ("keyboard", "type_text", "click"),
    ("keyboard", "press_key", "click"),
])
def test_a_core_action_needs_the_input_capability_its_target_declares(missing, refused, works):
    surface = Surface("t", "window", TREE - {missing})
    assert refused not in surface.actions() and works in surface.actions()
    with pytest.raises(errors.NotSupported) as caught:
        check_step(surface, refused, {"element": 1})
    assert caught.value.text().startswith(f"NOT_SUPPORTED: {refused} is not available on t: ")


# -- shared arguments --------------------------------------------------------------

def test_shared_arguments_address_the_subject_per_family():
    steps, subject = check(APP, [("click", {"element": 1}), ("type_text", {"text": "x"})], {"app": "Notes"})
    assert subject == {"app": "Notes"} and steps == [("click", {"element": 1}), ("type_text", {"text": "x"})]
    steps, subject = check(BROWSER, [("click", {"element": "e1", "tab": "t2"})], {})
    assert subject == {"tab": "t2"} and steps == [("click", {"element": "e1"})]
    steps, subject = check(Surface("screen", "screen", PIXELS, ("list_displays",)), [("list_displays", {})], {"display": 2})
    assert subject == {"display": 2}


def test_a_carried_over_subject_name_is_rewritten():
    steps, subject = check(BROWSER, [("get_state", {})], {"tab_id": "t1"})
    assert subject == {"tab": "t1"}
    steps, subject = check(BROWSER, [("click", {"element": "e1"}), ("reload", {})], {"page": "t3"})
    assert subject == {"tab": "t3"}


def test_args_shared_by_several_steps_hold_only_the_subject():
    with pytest.raises(errors.BadArgument, match="move element into the steps"):
        check(APP, [("click", {}), ("click", {})], {"app": "Notes", "element": 1})
    with pytest.raises(errors.BadArgument, match="args without an action"):
        check(APP, [], {"element": 1})


def test_args_beside_a_list_of_one_step_hold_only_the_subject_too():
    with pytest.raises(errors.BadArgument, match="move x, y into the steps"):
        check(APP, [("click", {"element": 3})], {"app": "X", "x": 5, "y": 6}, listed=True)
    steps, subject = check(APP, [("click", {"element": 3})], {"app": "X"}, listed=True)
    assert steps == [("click", {"element": 3})] and subject == {"app": "X"}
    # action + args: the args are the step.
    steps, subject = check(APP, [("click", {"x": 5, "y": 6})], {"app": "X", "x": 5, "y": 6})
    assert steps == [("click", {"x": 5, "y": 6})] and subject == {"app": "X"}


def test_another_familys_subject_is_a_bad_argument():
    with pytest.raises(errors.BadArgument, match="does not take tab"):
        check(APP, [("get_state", {})], {"tab": "t1"})


# -- the family guides speak the same vocabulary -----------------------------------------

def _guide_steps(family: str):
    """Each ``- action(arguments)`` line of a family guide, as (action, argument names)."""
    import re
    from pathlib import Path

    path = Path(vocabulary.__file__).parents[1] / "guides" / f"{family}.md"
    if not path.exists():
        return
    for match in re.finditer(r"(?m)^- ([a-z_]+)\(([^)]*)\)", path.read_text(encoding="utf-8")):
        names = set()
        for item in match.group(2).split(","):
            # "a | b" offers alternatives; after "=" come values, not names.
            for part in ([item] if "=" in item else item.split("|")):
                name = part.split("=")[0].strip().strip("?").strip()
                if re.fullmatch(r"[a-z_]+", name):
                    names.add(name)
        yield match.group(1), names


@pytest.mark.parametrize("family", sorted(vocabulary.FAMILIES))
def test_a_family_guide_names_only_its_actions_and_their_arguments(family):
    table = vocabulary.FAMILIES[family]
    for action, names in _guide_steps(family):
        assert action in vocabulary.CORE or action in table.extras, f"{family}.md: {action}"
        unknown = names - set(table.arguments(action)) - set(table.shared)
        assert not unknown, f"{family}.md: {action} names {sorted(unknown)}"
