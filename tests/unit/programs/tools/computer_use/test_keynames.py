"""Keys in one spelling, xdotool's, and the translations the backends need."""
from __future__ import annotations

import pytest

from openprogram.programs.tools.computer_use.computer_use.keynames import (
    chord_of, normalize_key, playwright, pyautogui,
)


@pytest.mark.parametrize("written, ours", [
    ("Enter", "Return"),
    ("Return", "Return"),
    ("return", "Return"),
    ("Esc", "Escape"),
    ("Escape", "Escape"),
    ("Control+A", "ctrl+a"),
    ("ctrl+shift+Tab", "ctrl+shift+Tab"),
    ("Shift+Control+tab", "ctrl+shift+Tab"),
    ("cmd+c", "super+c"),
    ("Command+V", "super+v"),
    ("Meta+a", "super+a"),
    ("win+e", "super+e"),
    ("super+space", "super+space"),
    ("option+Left", "alt+Left"),
    ("ArrowUp", "Up"),
    ("ArrowDown", "Down"),
    ("arrowleft", "Left"),
    ("ArrowRight", "Right"),
    ("PageDown", "Page_Down"),
    ("Backspace", "BackSpace"),
    ("f5", "F5"),
    ("A", "A"),
    ("ctrl++", "ctrl+plus"),
])
def test_other_spellings_become_ours(written, ours):
    assert normalize_key(written) == ours


@pytest.mark.parametrize("written, ours", [
    ("ctrl", "ctrl"),
    ("Shift", "shift"),
    ("cmd+shift", "shift+super"),
    ("Control+Alt", "ctrl+alt"),
])
def test_a_chord_of_modifiers_only_keeps_them_all(written, ours):
    assert normalize_key(written) == ours


def test_modifiers_of_a_click_are_modifiers_only():
    assert normalize_key("cmd,shift", modifiers_only=True) == "shift+super"
    assert normalize_key("Control", modifiers_only=True) == "ctrl"


def test_a_list_of_keys_is_one_chord():
    assert chord_of(["ctrl", "c"]) == "ctrl+c"
    assert chord_of(("Control", " ", "Shift", "T")) == "Control+Shift+T"
    assert chord_of("Return") == "Return"


@pytest.mark.parametrize("ours, theirs", [
    ("Return", "Enter"),
    ("ctrl+shift+Tab", "Control+Shift+Tab"),
    ("ctrl+a", "Control+A"),
    ("super+c", "Meta+C"),
    ("Up", "ArrowUp"),
    ("Page_Down", "PageDown"),
    ("BackSpace", "Backspace"),
    ("space", "Space"),
    ("Escape", "Escape"),
    ("ctrl+plus", "Control++"),
])
def test_browsers_get_playwrights_names(ours, theirs):
    assert playwright(ours) == theirs


@pytest.mark.parametrize("ours, system, theirs", [
    ("Return", "", ["enter"]),
    ("ctrl+c", "", ["ctrl", "c"]),
    ("super+c", "Darwin", ["command", "c"]),
    ("super+e", "Windows", ["win", "e"]),
    ("ctrl+shift+Tab", "", ["ctrl", "shift", "tab"]),
    ("Escape", "", ["esc"]),
    ("Page_Up", "", ["pageup"]),
    ("Left", "", ["left"]),
    ("A", "", ["A"]),
])
def test_an_osworld_machine_gets_pyautoguis_names(ours, system, theirs):
    assert pyautogui(ours, system) == theirs


def test_normalized_keys_translate_end_to_end():
    assert playwright(normalize_key("Control+Enter")) == "Control+Enter"
    assert pyautogui(normalize_key("cmd+ArrowDown"), "darwin") == ["command", "down"]
