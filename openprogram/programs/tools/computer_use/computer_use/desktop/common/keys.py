"""xdotool-style key chords mapped to macOS virtual key codes.

A chord is one or more names joined by ``+``: ``"Return"``, ``"super+c"``,
``"ctrl+shift+Tab"``, ``"KP_0"``. The last name is the key; the others are
modifiers. A single printable character is accepted as a key, and a
character that needs Shift on a US layout (``"A"``, ``"!"``) adds it.
"""
from __future__ import annotations

from dataclasses import dataclass

SHIFT = 0x20000
CONTROL = 0x40000
OPTION = 0x80000
COMMAND = 0x100000
FN = 0x800000

_MODIFIERS = {
    "shift": SHIFT,
    "ctrl": CONTROL, "control": CONTROL,
    "alt": OPTION, "option": OPTION, "opt": OPTION,
    "super": COMMAND, "cmd": COMMAND, "command": COMMAND, "meta": COMMAND, "win": COMMAND,
    "fn": FN,
}

# Key codes of the modifier keys themselves, pressed down while a chord runs.
MODIFIER_KEYCODES = {SHIFT: 56, CONTROL: 59, OPTION: 58, COMMAND: 55, FN: 63}

_CHARACTERS = {
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "`": 50, " ": 49,
}

# Characters typed with Shift on a US layout, and the unshifted key they share.
_SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8",
    "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]", "|": "\\", ":": ";",
    '"': "'", "<": ",", ">": ".", "?": "/", "~": "`",
}

_NAMED = {
    "return": 36, "enter": 36, "tab": 48, "space": 49, "backspace": 51,
    "escape": 53, "esc": 53, "delete": 117, "forwarddelete": 117,
    "home": 115, "end": 119, "prior": 116, "page_up": 116, "pageup": 116,
    "next": 121, "page_down": 121, "pagedown": 121,
    "left": 123, "right": 124, "down": 125, "up": 126,
    "insert": 114, "help": 114, "caps_lock": 57, "capslock": 57,
    "volume_up": 72, "volumeup": 72, "volume_down": 73, "volumedown": 73,
    "mute": 74,
    "minus": 27, "equal": 24, "bracketleft": 33, "bracketright": 30,
    "backslash": 42, "semicolon": 41, "apostrophe": 39, "quoteright": 39,
    "grave": 50, "quoteleft": 50, "comma": 43, "period": 47, "slash": 44,
    "kp_decimal": 65, "kp_multiply": 67, "kp_add": 69, "kp_divide": 75,
    "kp_enter": 76, "kp_subtract": 78, "kp_equal": 81, "num_lock": 71, "kp_clear": 71,
    "kp_0": 82, "kp_1": 83, "kp_2": 84, "kp_3": 85, "kp_4": 86, "kp_5": 87,
    "kp_6": 88, "kp_7": 89, "kp_8": 91, "kp_9": 92,
    "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96, "f6": 97, "f7": 98,
    "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111, "f13": 105,
    "f14": 107, "f15": 113, "f16": 106, "f17": 64, "f18": 79, "f19": 80, "f20": 90,
}

# Shifted names that xdotool spells out.
_NAMED_SHIFTED = {
    "plus": "=", "exclam": "1", "at": "2", "numbersign": "3", "dollar": "4",
    "percent": "5", "asciicircum": "6", "ampersand": "7", "asterisk": "8",
    "parenleft": "9", "parenright": "0", "underscore": "-", "braceleft": "[",
    "braceright": "]", "bar": "\\", "colon": ";", "quotedbl": "'", "less": ",",
    "greater": ".", "question": "/", "asciitilde": "`",
}


class KeyNameError(ValueError):
    """A key name this mapping does not know."""


@dataclass(frozen=True)
class Chord:
    keycode: int
    flags: int
    text: str = ""

    @property
    def modifier_keycodes(self) -> list[int]:
        return [code for flag, code in MODIFIER_KEYCODES.items() if self.flags & flag]


def _key(name: str) -> tuple[int, int, str]:
    if len(name) == 1:
        if name in _CHARACTERS:
            return _CHARACTERS[name], 0, name
        lower = name.lower()
        if lower in _CHARACTERS and name != lower:
            return _CHARACTERS[lower], SHIFT, name
        if name in _SHIFTED:
            return _CHARACTERS[_SHIFTED[name]], SHIFT, name
        raise KeyNameError(f"unknown key {name!r}")
    lower = name.lower()
    if lower in _NAMED:
        return _NAMED[lower], 0, ""
    if lower in _NAMED_SHIFTED:
        return _CHARACTERS[_NAMED_SHIFTED[lower]], SHIFT, ""
    raise KeyNameError(f"unknown key {name!r}")


def parse_chord(chord: str) -> Chord:
    """Parse ``"super+shift+z"`` into a key code and modifier flags."""
    if not isinstance(chord, str) or not chord.strip():
        raise KeyNameError("key must be a non-empty string")
    raw = chord.strip()
    # "+" alone, or a chord ending in "+" ("ctrl++"), names the plus key.
    parts = raw.split("+")
    if raw == "+" or raw.endswith("++"):
        parts = raw[:-1].split("+")[:-1] + ["+"]
    parts = [p.strip() for p in parts]
    if any(not p for p in parts):
        raise KeyNameError(f"malformed key chord {chord!r}")
    flags = 0
    for name in parts[:-1]:
        flag = _MODIFIERS.get(name.lower())
        if flag is None:
            raise KeyNameError(f"unknown modifier {name!r} in {chord!r}")
        flags |= flag
    last = parts[-1]
    if last.lower() in _MODIFIERS and len(parts) == 1:
        flag = _MODIFIERS[last.lower()]
        return Chord(MODIFIER_KEYCODES[flag], flag)
    keycode, extra, text = _key(last)
    return Chord(keycode, flags | extra, text)


def modifier_flags(names: str | None) -> int:
    """Flags for a click modifier list such as ``"cmd+shift"``."""
    if not names:
        return 0
    flags = 0
    for name in str(names).replace(",", "+").split("+"):
        name = name.strip().lower()
        if not name:
            continue
        flag = _MODIFIERS.get(name)
        if flag is None:
            raise KeyNameError(f"unknown modifier {name!r}")
        flags |= flag
    return flags
