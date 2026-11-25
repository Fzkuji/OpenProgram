"""DOM ``KeyboardEvent.code`` values mapped to macOS and Windows virtual key codes.

``code`` names the physical key, as the virtual key code does, so a
shortcut typed in the tab reaches the app as the same key whatever the
keyboard layout. Modifier names come from the page as ``shift``, ``ctrl``,
``alt`` and ``meta``.
"""
from __future__ import annotations

from ..common.keys import COMMAND, CONTROL, OPTION, SHIFT

_LETTERS = {
    "A": 0, "S": 1, "D": 2, "F": 3, "H": 4, "G": 5, "Z": 6, "X": 7, "C": 8, "V": 9,
    "B": 11, "Q": 12, "W": 13, "E": 14, "R": 15, "Y": 16, "T": 17, "O": 31, "U": 32,
    "I": 34, "P": 35, "L": 37, "J": 38, "K": 40, "N": 45, "M": 46,
}
_DIGITS = {"1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23, "9": 25, "7": 26, "8": 28, "0": 29}

MAC_KEYCODES: dict[str, int] = {
    **{f"Key{letter}": code for letter, code in _LETTERS.items()},
    **{f"Digit{digit}": code for digit, code in _DIGITS.items()},
    "Equal": 24, "Minus": 27, "BracketRight": 30, "BracketLeft": 33, "Quote": 39,
    "Semicolon": 41, "Backslash": 42, "Comma": 43, "Slash": 44, "Period": 47,
    "Backquote": 50, "IntlBackslash": 10,
    "Enter": 36, "Tab": 48, "Space": 49, "Backspace": 51, "Escape": 53,
    "MetaLeft": 55, "MetaRight": 54, "ShiftLeft": 56, "ShiftRight": 60,
    "CapsLock": 57, "AltLeft": 58, "AltRight": 61, "ControlLeft": 59, "ControlRight": 62,
    "Fn": 63,
    "NumpadDecimal": 65, "NumpadMultiply": 67, "NumpadAdd": 69, "NumLock": 71,
    "NumpadDivide": 75, "NumpadEnter": 76, "NumpadSubtract": 78, "NumpadEqual": 81,
    "Numpad0": 82, "Numpad1": 83, "Numpad2": 84, "Numpad3": 85, "Numpad4": 86,
    "Numpad5": 87, "Numpad6": 88, "Numpad7": 89, "Numpad8": 91, "Numpad9": 92,
    "F1": 122, "F2": 120, "F3": 99, "F4": 118, "F5": 96, "F6": 97, "F7": 98, "F8": 100,
    "F9": 101, "F10": 109, "F11": 103, "F12": 111, "F13": 105, "F14": 107, "F15": 113,
    "F16": 106, "F17": 64, "F18": 79, "F19": 80, "F20": 90,
    "Insert": 114, "Help": 114, "Home": 115, "PageUp": 116, "Delete": 117, "End": 119,
    "PageDown": 121, "ArrowLeft": 123, "ArrowRight": 124, "ArrowDown": 125, "ArrowUp": 126,
    "IntlYen": 93, "IntlRo": 94, "Lang1": 104, "Lang2": 102,
}

_PUNCTUATION_VK = {
    "Semicolon": 0xBA, "Equal": 0xBB, "Comma": 0xBC, "Minus": 0xBD, "Period": 0xBE, "Slash": 0xBF,
    "Backquote": 0xC0, "BracketLeft": 0xDB, "Backslash": 0xDC, "BracketRight": 0xDD, "Quote": 0xDE,
    "IntlBackslash": 0xE2, "IntlRo": 0xC1, "IntlYen": 0xDC,
}

# Windows virtual-key codes, by the key's place on a US layout.
WINDOWS_VK: dict[str, int] = {
    **{f"Key{chr(c)}": c for c in range(ord("A"), ord("Z") + 1)},
    **{f"Digit{n}": 0x30 + n for n in range(10)},
    **{f"Numpad{n}": 0x60 + n for n in range(10)},
    **{f"F{n}": 0x6F + n for n in range(1, 25)},
    **_PUNCTUATION_VK,
    "Enter": 0x0D, "Tab": 0x09, "Space": 0x20, "Backspace": 0x08, "Escape": 0x1B,
    "ShiftLeft": 0x10, "ShiftRight": 0x10, "ControlLeft": 0x11, "ControlRight": 0x11,
    "AltLeft": 0x12, "AltRight": 0x12, "MetaLeft": 0x5B, "MetaRight": 0x5C, "ContextMenu": 0x5D,
    "CapsLock": 0x14, "NumLock": 0x90, "ScrollLock": 0x91, "Pause": 0x13, "PrintScreen": 0x2C,
    "NumpadMultiply": 0x6A, "NumpadAdd": 0x6B, "NumpadSubtract": 0x6D, "NumpadDecimal": 0x6E,
    "NumpadDivide": 0x6F, "NumpadEnter": 0x0D,
    "Insert": 0x2D, "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Lang1": 0x15, "Lang2": 0x19, "Convert": 0x1C, "NonConvert": 0x1D,
}
# Keys whose scan code carries the extended (E0) prefix.
WINDOWS_EXTENDED = frozenset({
    "ControlRight", "AltRight", "MetaLeft", "MetaRight", "ContextMenu", "NumpadEnter", "NumpadDivide",
    "Insert", "Delete", "Home", "End", "PageUp", "PageDown", "ArrowLeft", "ArrowUp", "ArrowRight",
    "ArrowDown", "PrintScreen", "NumLock",
})
# Scan codes of keys that share a virtual key with their twin on the other side.
WINDOWS_SCAN = {"ShiftLeft": 0x2A, "ShiftRight": 0x36, "ControlLeft": 0x1D, "ControlRight": 0x1D,
                "AltLeft": 0x38, "AltRight": 0x38}


def windows_key(code: str) -> tuple[int, bool] | None:
    """(virtual-key code, extended) for a DOM ``code``; None for a key Windows has no code for."""
    vk = WINDOWS_VK.get(code)
    return None if vk is None else (vk, code in WINDOWS_EXTENDED)


MODIFIER_FLAGS = {"shift": SHIFT, "ctrl": CONTROL, "alt": OPTION, "meta": COMMAND}


def mac_keycode(code: str) -> int | None:
    """The virtual key code for a DOM ``code``; None for a key macOS has no code for."""
    return MAC_KEYCODES.get(code)


def modifier_flags(names: object) -> int:
    """CGEvent flags for the modifier names the page reports as held."""
    flags = 0
    if isinstance(names, (list, tuple)):
        for name in names:
            flags |= MODIFIER_FLAGS.get(str(name), 0)
    return flags


def typed_text(key: str, names: object) -> str:
    """The character a key event carries: its ``key`` when that is one
    character and no shortcut modifier is held, so typing reaches text fields
    while ⌘ and ⌃ chords arrive as shortcuts."""
    held = set(names) if isinstance(names, (list, tuple)) else set()
    if len(key) != 1 or held & {"meta", "ctrl"}:
        return ""
    return key


__all__ = ["MAC_KEYCODES", "MODIFIER_FLAGS", "WINDOWS_EXTENDED", "WINDOWS_SCAN", "WINDOWS_VK", "mac_keycode",
           "modifier_flags", "typed_text", "windows_key"]
