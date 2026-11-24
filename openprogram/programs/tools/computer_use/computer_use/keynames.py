"""Key names in one spelling: xdotool's, with ``super`` for Command on macOS and the Windows key elsewhere.

A model writes ``Return``, ``Escape``, ``ctrl+shift+Tab``. Spellings carried
over from other tools (``Enter``, ``Control+A``, ``cmd+c``, ``ArrowDown``)
are normalized to that one before a provider sees them. A provider whose
backend has its own names translates from ours: ``playwright`` for the
browsers, ``pyautogui`` for an OSWorld machine.
"""
from __future__ import annotations

from typing import Any

_MODIFIERS = {
    "ctrl": "ctrl", "control": "ctrl", "ctl": "ctrl",
    "alt": "alt", "option": "alt", "opt": "alt",
    "shift": "shift",
    "super": "super", "cmd": "super", "command": "super", "meta": "super", "win": "super", "windows": "super",
    "fn": "fn",
}
_ORDER = ("ctrl", "alt", "shift", "super", "fn")

# Other spellings of a key, lower-cased -> xdotool's name.
_KEYS = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape", "tab": "Tab",
    "space": "space", "spacebar": "space", " ": "space",
    "backspace": "BackSpace", "delete": "Delete", "del": "Delete", "forwarddelete": "Delete",
    "insert": "Insert", "home": "Home", "end": "End",
    "pageup": "Page_Up", "page_up": "Page_Up", "prior": "Page_Up", "pgup": "Page_Up",
    "pagedown": "Page_Down", "page_down": "Page_Down", "next": "Page_Down", "pgdn": "Page_Down",
    "up": "Up", "arrowup": "Up", "down": "Down", "arrowdown": "Down",
    "left": "Left", "arrowleft": "Left", "right": "Right", "arrowright": "Right",
    "capslock": "Caps_Lock", "caps_lock": "Caps_Lock",
    "plus": "plus", "minus": "minus",
}

# xdotool name -> Playwright's.
_PLAYWRIGHT = {
    "Return": "Enter", "BackSpace": "Backspace", "space": "Space", "Page_Up": "PageUp", "Page_Down": "PageDown",
    "Up": "ArrowUp", "Down": "ArrowDown", "Left": "ArrowLeft", "Right": "ArrowRight", "Caps_Lock": "CapsLock",
    "plus": "+", "minus": "-",
    "ctrl": "Control", "alt": "Alt", "shift": "Shift", "super": "Meta",
}

# xdotool name -> pyautogui's.
_PYAUTOGUI = {
    "Return": "enter", "Escape": "esc", "BackSpace": "backspace", "Delete": "delete", "Tab": "tab",
    "Page_Up": "pageup", "Page_Down": "pagedown", "Up": "up", "Down": "down", "Left": "left", "Right": "right",
    "Caps_Lock": "capslock", "Insert": "insert", "Home": "home", "End": "end", "plus": "+", "minus": "-",
}


def chord_of(value: Any) -> str:
    """Several keys written as a list are one chord: ``["ctrl", "c"]`` is ``ctrl+c``."""
    if isinstance(value, (list, tuple)):
        return "+".join(str(part).strip() for part in value if str(part).strip())
    return str(value or "").strip()


def _parts(chord: str) -> list[str]:
    raw = chord.strip()
    # "+" alone, or a chord ending in "++" ("ctrl++"), names the plus key.
    if raw == "+":
        return ["plus"]
    if raw.endswith("++"):
        return [part.strip() for part in raw[:-2].split("+")] + ["plus"]
    return [part.strip() for part in raw.split("+")]


def _key(name: str, chorded: bool) -> str:
    known = _KEYS.get(name.lower())
    if known:
        return known
    if len(name) == 1:
        # With a modifier a letter is the key itself: Control+A is ctrl+a.
        return name.lower() if chorded else name
    lower = name.lower()
    if lower[:1] == "f" and lower[1:].isdigit():
        return "F" + lower[1:]
    if lower.startswith("kp_"):
        return "KP_" + name[3:].capitalize()
    return name


def normalize_key(chord: str, modifiers_only: bool = False) -> str:
    """``chord`` in xdotool spelling: modifiers first, then the key. Unknown names are left as written."""
    parts = [part for part in _parts(chord.replace(",", "+") if modifiers_only else chord) if part]
    if not parts:
        return ""
    held, key = [], ""
    for position, part in enumerate(parts):
        modifier = _MODIFIERS.get(part.lower())
        last = position == len(parts) - 1
        if modifier:
            # A modifier is spelled as one wherever it stands, also last in a chord of modifiers only.
            held.append(modifier)
        elif last:
            key = part
        else:
            # Not a modifier this table knows: kept in place, for the backend to judge.
            held.append(part)
    ordered = [name for name in _ORDER if name in held] + [name for name in held if name not in _ORDER]
    ordered = list(dict.fromkeys(ordered))
    if not key:
        return "+".join(ordered)
    return "+".join([*ordered, _key(key, bool(ordered))])


def playwright(chord: str) -> str:
    """Our spelling as Playwright's: ``ctrl+shift+Tab`` is ``Control+Shift+Tab``."""
    parts = _parts(chord)
    names = [_PLAYWRIGHT.get(part, part) for part in parts]
    if len(names) > 1 and len(names[-1]) == 1:
        names[-1] = names[-1].upper() if names[-1].isalpha() else names[-1]
    return "+".join(names)


def pyautogui(chord: str, system: str = "") -> list[str]:
    """Our spelling as the key list pyautogui's ``hotkey`` takes."""
    command = "command" if system.lower() in ("darwin", "macos") else "win"
    names = []
    for part in _parts(chord):
        if part == "super":
            names.append(command)
        else:
            names.append(_PYAUTOGUI.get(part, part.lower() if len(part) > 1 else part))
    return names


__all__ = ["chord_of", "normalize_key", "playwright", "pyautogui"]
