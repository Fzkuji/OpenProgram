"""Pointer input: window messages to the child under the point, or ``SendInput`` where they are ignored."""
from __future__ import annotations

import ctypes
import time

from ..common.base import HOVER_SECONDS, BadArgument, click_args, click_words, scroll_args, seconds_arg
from .send_input import _Forward, _send_key, _send_mouse
from .win32 import (EXPAND_COLLAPSE, FOREGROUND_CLASSES, INVOKE, LEGACY, MK_CONTROL, MK_LBUTTON, MK_SHIFT,
    NOTCHES_PER_PAGE, PIXELS_PER_NOTCH, SELECTION_ITEM, TOGGLE, VK_MODIFIERS, WHEEL_DELTA, WM_LBUTTONDBLCLK,
    WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEHWHEEL, WM_MOUSEMOVE, WM_MOUSEWHEEL, WindowState, _BUTTON_MESSAGES, _class)
from . import win32


class Pointer:
    """Where a point lands, and clicks, hovers, drags and scrolls."""

    def _point(self, state: WindowState, x, y) -> tuple[float, float]:
        if x is None or y is None:
            raise BadArgument("give element, or x and y in pixels of the last screenshot")
        if not state.has_screenshot:
            raise BadArgument("this window has no screenshot to take coordinates from; call get_state")
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError) as exc:
            raise BadArgument("x and y must be numbers") from exc
        w, h = state.size[0] * state.scale, state.size[1] * state.scale
        if not (0 <= px < w and 0 <= py < h):
            raise BadArgument(f"({px:g}, {py:g}) is outside the {int(w)}x{int(h)} screenshot")
        return state.origin[0] + px / state.scale, state.origin[1] + py / state.scale

    def _foreground_only(self, state: WindowState) -> bool:
        return _class(state.window_id) in FOREGROUND_CLASSES

    def _target_child(self, hwnd: int, sx: float, sy: float) -> tuple[int, int, int]:
        """The deepest visible child under a screen point, and the point in its client coordinates."""
        from ctypes import wintypes
        user32 = win32._user32()
        current = hwnd
        for _ in range(32):
            point = wintypes.POINT(int(sx), int(sy))
            user32.ScreenToClient(current, ctypes.byref(point))
            child = user32.ChildWindowFromPointEx(current, point, 0x1 | 0x2 | 0x4)  # skip invisible/disabled/transparent
            if not child or int(child) == current:
                return current, point.x, point.y
            current = int(child)
        point = wintypes.POINT(int(sx), int(sy))
        user32.ScreenToClient(current, ctypes.byref(point))
        return current, point.x, point.y

    @staticmethod
    def _lparam(x: int, y: int) -> int:
        return ((y & 0xFFFF) << 16) | (x & 0xFFFF)

    def _post_click(self, state, point, button: str, count: int, mods: list[int], hold: float) -> None:
        user32 = win32._user32()
        target, cx, cy = self._target_child(state.window_id, *point)
        down, up, flag = _BUTTON_MESSAGES[button]
        extra = (MK_SHIFT if 0x10 in mods else 0) | (MK_CONTROL if 0x11 in mods else 0)
        lparam = self._lparam(cx, cy)
        user32.PostMessageW(target, WM_MOUSEMOVE, extra, lparam)
        for n in range(count):
            message = WM_LBUTTONDBLCLK if button == "left" and n == 1 else down
            user32.PostMessageW(target, message, flag | extra, lparam)
            if hold:
                time.sleep(hold)
            user32.PostMessageW(target, up, extra, lparam)
            time.sleep(0.03)

    def _send_input_click(self, state, point, button: str, count: int, mods: list[int], hold: float) -> None:
        with self._forward(state):
            user32 = win32._user32()
            user32.SetCursorPos(int(point[0]), int(point[1]))
            flags = {"left": (0x2, 0x4), "right": (0x8, 0x10), "middle": (0x20, 0x40)}[button]
            for vk in mods:
                _send_key(vk, False)
            try:
                for _ in range(count):
                    _send_mouse(flags[0])
                    if hold:
                        time.sleep(hold)
                    _send_mouse(flags[1])
                    time.sleep(0.05)
            finally:
                for vk in reversed(mods):
                    _send_key(vk, True)

    def _forward(self, state: WindowState):
        return _Forward(state.window_id)

    def _do_click(self, app, state, args) -> str:
        button, count = click_args(args)
        held = str(args.get("modifiers") or "").replace(",", "+").split("+")
        mods = [VK_MODIFIERS[m.strip().lower()] for m in held if m.strip() and m.strip().lower() in VK_MODIFIERS]
        unknown = [m for m in held if m.strip() and m.strip().lower() not in VK_MODIFIERS]
        if unknown:
            raise BadArgument(f"unknown modifier {unknown[0]!r}")
        hold = seconds_arg(args.get("seconds"))
        index = args.get("element")
        if index is not None:
            el = self._element(state, index)
            node = self._node_at(state, index)
            if count == 1 and not mods and not hold and button == "left":
                for pattern_id, call in ((INVOKE, "Invoke"), (TOGGLE, "Toggle"), (SELECTION_ITEM, "Select"),
                                         (EXPAND_COLLAPSE, None), (LEGACY, "DoDefaultAction")):
                    pattern = self._pattern(el, pattern_id)
                    if pattern is None:
                        continue
                    if call is None:
                        state_now = self._safe(lambda: int(pattern.CurrentExpandCollapseState), 0)
                        call = "Collapse" if state_now == 1 else "Expand"
                    if self._safe(lambda: getattr(pattern, call)() or True, False):
                        return f"pressed {node.body()}"
                if node.role == "edit" and self._safe(lambda: el.SetFocus() or True, False):
                    return f"focused {node.body()}"
            point, what = self._center(el), node.body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        if self._foreground_only(state):
            self._send_input_click(state, point, button, count, mods, hold)
            tail = " (this window takes real input only: it came forward for the click, then focus went back)"
        else:
            self._post_click(state, point, button, count, mods, hold)
            tail = ""
        return f"{click_words(button, count)} {what}" + (f" (held {hold:g}s)" if hold else "") + tail

    def _do_hover(self, app, state, args) -> str:
        index = args.get("element")
        if index is not None:
            point, what = self._center(self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = seconds_arg(args.get("seconds")) or HOVER_SECONDS
        target, cx, cy = self._target_child(state.window_id, *point)
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            win32._user32().PostMessageW(target, WM_MOUSEMOVE, 0, self._lparam(cx, cy))
            time.sleep(0.1)
        return f"rested the pointer over {what} for {rest:g}s"

    def _drag_end(self, state: WindowState, args, element: str, x: str, y: str) -> tuple[tuple[float, float], str]:
        """One end of a drag, by element or by x,y of the last screenshot, and how the result names it."""
        if args.get(element) is not None:
            index = args[element]
            return self._center(self._element(state, index)), self._node_at(state, index).body()
        return self._point(state, args.get(x), args.get(y)), f"({args.get(x)}, {args.get(y)})"

    def _do_drag(self, app, state, args) -> str:
        start, source = self._drag_end(state, args, "element", "x", "y")
        end, target = self._drag_end(state, args, "to_element", "to_x", "to_y")
        took = seconds_arg(args.get("seconds"))
        pause = max(0.015, took / 12) if took else 0.015
        if self._foreground_only(state):
            with self._forward(state):
                user32 = win32._user32()
                user32.SetCursorPos(int(start[0]), int(start[1]))
                _send_mouse(0x2)
                for step in range(1, 13):
                    t = step / 12
                    user32.SetCursorPos(int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
                    time.sleep(pause)
                _send_mouse(0x4)
        else:
            user32 = win32._user32()
            child, sx, sy = self._target_child(state.window_id, *start)
            user32.PostMessageW(child, WM_LBUTTONDOWN, MK_LBUTTON, self._lparam(sx, sy))
            for step in range(1, 13):
                t = step / 12
                px, py = start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t
                # Mouse messages after a press go to the window that took the press.
                from ctypes import wintypes
                pt = wintypes.POINT(int(px), int(py))
                user32.ScreenToClient(child, ctypes.byref(pt))
                user32.PostMessageW(child, WM_MOUSEMOVE, MK_LBUTTON, self._lparam(pt.x, pt.y))
                time.sleep(pause)
            user32.PostMessageW(child, WM_LBUTTONUP, 0, self._lparam(pt.x, pt.y))
        return f"dragged from {source} to {target}" + (f" over {took:g}s" if took else "")

    def _do_scroll(self, app, state, args) -> str:
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element")
        if index is not None and not pixels:
            el = self._element(state, index)
            node = self._node_at(state, index)
            pattern = self._scrollable(el)
            if pattern is not None:
                large_decrement, no_amount, large_increment = 0, 2, 3
                step = large_decrement if direction in ("up", "left") else large_increment
                for _ in range(max(1, int(round(pages)))):
                    horizontal = step if direction in ("left", "right") else no_amount
                    vertical = step if direction in ("up", "down") else no_amount
                    if not self._safe(lambda: pattern.Scroll(horizontal, vertical) or True, False):
                        break
                else:
                    return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
            point, what = self._center(el), f"over {node.body()}"
        elif index is not None:
            el = self._element(state, index)
            point, what = self._center(el), f"over {self._node_at(state, index).body()}"
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        notches = max(1, int(round(pixels / PIXELS_PER_NOTCH))) if pixels else max(1, int(round(pages * NOTCHES_PER_PAGE)))
        sign = 1 if direction in ("up", "right") else -1
        message = WM_MOUSEWHEEL if direction in ("up", "down") else WM_MOUSEHWHEEL
        target, _, _ = self._target_child(state.window_id, *point)
        user32 = win32._user32()
        for _ in range(notches):
            wparam = ((sign * WHEEL_DELTA) & 0xFFFF) << 16
            user32.PostMessageW(target, message, wparam, self._lparam(int(point[0]), int(point[1])))
            time.sleep(0.02)
        return f"scrolled {direction} {amount} {what}"
