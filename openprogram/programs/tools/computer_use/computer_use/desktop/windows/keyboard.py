"""Keys and text: window messages to the focused child, or ``SendInput`` for chords."""
from __future__ import annotations

import struct
import time

from ..common.base import MAX_TEXT_CHARS, BadArgument, NotDispatched, ComputerUseError, seconds_arg
from ..common.tree import role_name
from .formats import key_chord
from .send_input import _send_key, _send_unicode
from .win32 import WM_CHAR, WM_KEYDOWN, WM_KEYUP, _EXTENDED_VKS, _NOT_TEXT_ROLES
from . import win32


class Keyboard:
    """The press_key and type_text actions."""

    def _post_key(self, hwnd: int, vk: int, *, up: bool) -> None:
        scan = win32._user32().MapVirtualKeyW(vk, 0)
        lparam = 1 | (scan << 16) | ((1 << 24) if vk in _EXTENDED_VKS else 0)
        if up:
            lparam |= (1 << 30) | (1 << 31)
        win32._user32().PostMessageW(hwnd, WM_KEYUP if up else WM_KEYDOWN, vk, lparam)

    def _post_text(self, hwnd: int, text: str) -> None:
        user32 = win32._user32()
        for unit in struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le")):
            if unit == 0x0A:
                self._post_key(hwnd, 0x0D, up=False)
                user32.PostMessageW(hwnd, WM_CHAR, 0x0D, 1)
                self._post_key(hwnd, 0x0D, up=True)
            else:
                user32.PostMessageW(hwnd, WM_CHAR, unit, 1)
            time.sleep(0.004)

    def _do_press_key(self, app, state, args) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            raise BadArgument("key is required")
        mods, vk, char = key_chord(key)
        chord = bool(mods)  # a character's own Shift travels in WM_CHAR, so only named modifiers need the keyboard
        if not vk and char:
            scanned = win32._user32().VkKeyScanW(char)
            if scanned == -1:
                raise ComputerUseError(f"{char!r} has no key on this keyboard layout")
            vk = scanned & 0xFF
            mods = mods + [vk_mod for bit, vk_mod in ((0x100, 0x10), (0x200, 0x11), (0x400, 0x12))
                           if scanned & bit and vk_mod not in mods]
        hold = seconds_arg(args.get("seconds"))
        if args.get("element") is not None:
            el = self._element(state, args["element"])
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        if not chord and not self._foreground_only(state):
            target = self._focus_hwnd(state)
            self._post_key(target, vk, up=False)
            if char:
                win32._user32().PostMessageW(target, WM_CHAR, ord(char), 1)
            if hold:
                time.sleep(hold)
            self._post_key(target, vk, up=True)
            return f"pressed {key}"
        # Shortcuts read the keyboard state, which posted messages do not change.
        with self._forward(state):
            for mod in mods:
                _send_key(mod, False)
            try:
                _send_key(vk, False)
                if hold:
                    time.sleep(hold)
                _send_key(vk, True)
            finally:
                for mod in reversed(mods):
                    _send_key(mod, True)
        return f"pressed {key} (a shortcut needs the real keyboard: the window came forward, then focus went back)"

    def _do_type_text(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise BadArgument(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        if args.get("element") is not None:
            el = self._element(state, args["element"])
            node = self._node_at(state, args["element"])
            if node.role in _NOT_TEXT_ROLES:
                raise NotDispatched(
                    f"element {args['element']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
            self._refuse_secure(el)
            self._safe(lambda: el.SetFocus())
        else:
            self._refuse_secure(self._focused(state))
        state = state or self._ensure_window(app)
        if self._foreground_only(state):
            with self._forward(state):
                for unit in struct.unpack(f"<{len(text.encode('utf-16-le')) // 2}H", text.encode("utf-16-le")):
                    if unit == 0x0A:
                        _send_key(0x0D, False)
                        _send_key(0x0D, True)
                    else:
                        _send_unicode(unit)
            return f"typed {len(text)} character(s) (this window takes real input only: it came forward, then focus went back)"
        self._post_text(self._focus_hwnd(state), text)
        return f"typed {len(text)} character(s)"
