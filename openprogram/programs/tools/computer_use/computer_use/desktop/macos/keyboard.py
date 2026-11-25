"""Keys and text posted to one application's process; secure input refused."""
from __future__ import annotations

import time

from ..common import keys as keymap
from ..common.base import ComputerUseError, refuse_password_field, check_cancelled as _check_cancelled
from ..common.tree import Node
from .frameworks import _SECURE
from . import frameworks
from . import skylight


class Keyboard:
    """Key chords and text posted to the process, and where keys can reach."""

    def _post_key_event(self, pid: int, keycode: int, down: bool, flags: int, text: str = "") -> None:
        _, _, cg, _ = frameworks._frameworks()
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateKeyboardEvent(source, keycode, down)
        cg.CGEventSetFlags(event, flags)
        if text:
            cg.CGEventKeyboardSetUnicodeString(event, len(text.encode("utf-16-le")) // 2, text)
        sky = skylight.skylight()
        if sky.available:
            sky.post(pid, event, keyboard=True)
        else:
            cg.CGEventPostToPid(pid, event)

    def _post_chord(self, pid: int, chord: keymap.Chord, duration: float = 0.0) -> None:
        flags = 0
        for flag, code in keymap.MODIFIER_KEYCODES.items():
            if chord.flags & flag and code != chord.keycode:
                flags |= flag
                self._post_key_event(pid, code, True, flags)
                time.sleep(0.008)
        self._post_key_event(pid, chord.keycode, True, chord.flags)
        time.sleep(max(0.012, min(float(duration or 0), 10.0)))
        self._post_key_event(pid, chord.keycode, False, chord.flags)
        for flag, code in reversed(list(keymap.MODIFIER_KEYCODES.items())):
            if chord.flags & flag and code != chord.keycode:
                flags &= ~flag
                self._post_key_event(pid, code, False, flags)
                time.sleep(0.008)

    def _post_text(self, pid: int, text: str) -> None:
        for index, char in enumerate(text):
            if index % 40 == 0:
                _check_cancelled()
            if char == "\n":
                self._post_chord(pid, keymap.Chord(36, 0))
                continue
            if char == "\t":
                self._post_chord(pid, keymap.Chord(48, 0))
                continue
            self._post_key_event(pid, 0, True, 0, char)
            self._post_key_event(pid, 0, False, 0, char)
            time.sleep(0.004)

    def _refuse_secure_input(self) -> None:
        """Secure keyboard entry means a password field has focus somewhere."""
        _, _, cg, _ = frameworks._frameworks()
        info = cg.CGSessionCopyCurrentDictionary() or {}
        if info.get("kCGSSessionSecureInputPID"):
            raise ComputerUseError("Secure keyboard entry is active (a password field is focused somewhere on this Mac); "
                                   "typing is refused until it closes.")

    def _refuse_secure(self, node: Node | None, element=None) -> None:
        if node is not None and "secure" in node.flags:
            refuse_password_field("typing into this password field")
        if element is not None:
            role = str(self._attr(element, "AXRole") or "")
            subrole = str(self._attr(element, "AXSubrole") or "")
            if _SECURE in (role, subrole):
                refuse_password_field("typing into the focused password field")

    def _keys_reach(self, pid: int, state) -> bool:
        """Whether posted keys reach the app's window. They do not while it is on
        another Space: clicks and Accessibility actions arrive there, keys are dropped."""
        _, ax, cg, _ = frameworks._frameworks()
        root = ax.AXUIElementCreateApplication(pid)
        windows = self._windows(root)
        window = None
        if state is not None and state.window_id:
            window = next((w for w in windows if self._ax_window_id(w) == state.window_id), None)
        if window is None:
            focused = self._attr(root, "AXFocusedWindow")
            window = focused if focused is not None else (windows[0] if windows else None)
        if window is None or self._attr(window, "AXMinimized"):
            return True
        wid = self._ax_window_id(window)
        if not wid:
            return True
        return any(int(r.get("kCGWindowNumber", 0)) == wid for r in cg.CGWindowListCopyWindowInfo(
            cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or [])

    def _insert(self, pid: int, text: str) -> bool:
        """Put ``text`` at the focused field's caret through Accessibility, replacing its selection."""
        element = self._focused_element(pid)
        if element is None or not self._settable(element, "AXSelectedText"):
            return False
        return self._set(element, "AXSelectedText", text) == 0
