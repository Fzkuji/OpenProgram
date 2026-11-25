"""Which platforms have a window backend, what the model reads about each, and the grants macOS lacks."""
from __future__ import annotations

import platform
import threading
import time
from typing import Any



_SYSTEMS = {"Darwin", "Linux", "Windows"}

# How long describe() keeps what it read of the macOS grants: a turn reads the description more than once.
_GRANTS_SECONDS = 5.0


def normalized_app(app: Any) -> str:
    """An application as rules are written for it: the name the model gave, spaces collapsed, lower-cased."""
    return " ".join(str(app or "").split()).lower()


def _backends():
    from ...desktop import backends
    return backends


def _base():
    from ...desktop.common import base
    return base


def supported() -> bool:
    """Whether this computer has a backend: macOS with PyObjC, Linux on X11 with AT-SPI, or Windows with UIA."""
    if platform.system() not in _SYSTEMS:
        return False
    try:
        return bool(_backends().support()["available"])
    except Exception:
        return False


_LINES = {
    "Darwin": "application windows, read and operated in the background; app names the application.",
    "Linux": ("application windows, read by element tree; element steps run in the background, pointer and key "
              "steps bring the window forward for a moment; app names the application."),
    "Windows": ("application windows, read by element tree and operated in the background, except shortcuts and "
                "windows that take only real input; app names the application."),
}

_NOTES = {
    "Darwin": (
        'Note: type="window" is this computer, a Mac (macOS). Every step runs in the background: the user keeps '
        "the keyboard, the pointer and the front application, and nothing is raised. Keys are xdotool names and "
        "super is Command (super+c); keys go to that application only, so system-wide shortcuts (Spotlight, "
        "Mission Control) cannot be pressed. hover rests a pointer only the application sees; one that ignores the "
        "pointer in the background shows nothing. A window on another Space takes clicks and element steps but not "
        "keys: there type_text and paste insert text into the focused field, and press_key fails. open_file(path) "
        "opens a document in the application the way Open With does, without raising it: use it instead of "
        "File > Open or super+o, since the system Open and Save dialogs take clicks and set_value but not keys, and "
        "when a state says a file dialog is open, open_file cancels it first. paste goes through the clipboard, "
        "which is restored afterwards; copy is the application's own Copy and restores the clipboard. show_window "
        "un-minimizes a window or unhides the application, still in the background. Prefer element clicks: a click "
        "at x,y can briefly change the order of windows. A newline inserted into a field does not submit it: use "
        "submit=true or the field's Search or Submit button."
    ),
    "Linux": (
        'Note: type="window" is this computer, a Linux desktop on X11. Element steps run in the background through '
        "AT-SPI. A click, drag or scroll at x,y, hover and press_key bring the window forward for that moment, then "
        "give focus and the pointer back (hover leaves the real pointer where it rested), so prefer element. Return "
        "on a focused field runs in the background. Keys are xdotool names and super is the Super key. paste "
        "inserts plain text straight into the focused field without the clipboard (format=text only); copy "
        "returns the selected text without touching the clipboard. show_window un-minimizes a window. There is no "
        "open_file. An application without accessibility (GTK and Qt apps need it turned on) shows no tree: act "
        "with x,y on the screenshot."
    ),
    "Windows": (
        'Note: type="window" is this computer, a Windows PC. Element steps run in the background through UI '
        "Automation, and clicks, scrolls and plain keys at a window are posted to it without raising it. A key "
        "with modifiers (ctrl+s), and pointer input to windows that take only real input (Store apps, Chromium, "
        "Firefox), bring the window forward for that moment, then give focus and the cursor back, so prefer "
        "element. Keys are xdotool names; super is the Windows key and ctrl is Control: use the Windows shortcuts "
        "(ctrl+c, not super+c). perform_secondary_action takes one of the element's listed actions (expand, "
        "collapse, select, or its default action). paste goes through the clipboard, whose text is restored "
        "afterwards; copy returns the selected text without touching the clipboard. show_window un-minimizes a "
        "window without activating it. There is no open_file."
    ),
}


_grants_lock = threading.Lock()
_grants: tuple[float, list[str]] = (0.0, [])


def _missing_grants(system: str) -> list[str]:
    """The operating-system grants the backend lacks (keys of ``providers.screen.local.GRANTS``), read without a prompt
    and kept a few seconds."""
    global _grants
    if system != "Darwin" or platform.system() != "Darwin":
        return []
    with _grants_lock:
        read_at, missing = _grants
        if time.monotonic() - read_at < _GRANTS_SECONDS:
            return list(missing)
    try:
        from ...desktop.macos import frameworks as macos
        said = macos.missing_permissions()
        missing = [name for name, text in (("accessibility", macos.ACCESSIBILITY_MISSING),
                                           ("screen_recording", macos.SCREEN_RECORDING_MISSING)) if text in said]
    except Exception:
        missing = []
    with _grants_lock:
        _grants = (time.monotonic(), list(missing))
    return list(missing)
