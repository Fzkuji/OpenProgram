"""Input that needs the foreground: XTest clicks, key chords, the window raised for the step."""
from __future__ import annotations

from contextlib import contextmanager
import time

from ..common import keys as keymap
from ..common.base import HOVER_SECONDS, BadArgument, NotDispatched, click_args, click_words, scroll_args, seconds_arg
from .x11 import NOTCHES_PER_PAGE, PIXELS_PER_NOTCH, WindowState, _BUTTONS, _MODIFIERS, _PRIMARY, _atspi, _stacking
from . import x11


class ForegroundInput:
    """Where a point lands, and the steps that go through XTest with the window in front."""

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

    @contextmanager
    def _foreground(self, state: WindowState, *, keep_pointer: bool = False):
        """Bring the window forward for XTEST input, then give focus and the pointer back."""
        if not state.window_id:
            raise NotDispatched("this window has no X window id; act by element")
        from openprogram.host._platform import front

        # The front guard would give the focus back in the middle of the input.
        with front.forward():
            previous = x11._x("getactivewindow", check=False)
            before = _stacking()
            pointer = dict(line.split("=", 1) for line in x11._x("getmouselocation", "--shell", check=False).splitlines()
                           if "=" in line)
            x11._x("windowactivate", "--sync", state.window_id, check=False, timeout=3)
            if x11._x("getactivewindow", check=False) != str(state.window_id):
                # Without an EWMH window manager, activation does nothing; focus the window directly.
                x11._x("windowraise", state.window_id, check=False)
                x11._x("windowfocus", "--sync", state.window_id, check=False, timeout=3)
            try:
                yield
            finally:
                if not keep_pointer and "X" in pointer and "Y" in pointer:
                    x11._x("mousemove", pointer["X"], pointer["Y"], check=False)
                if previous and previous != str(state.window_id):
                    # The windows that stood over it go back over it, then the user's window takes the focus.
                    x11._put_under(state.window_id, before)
                    x11._x("windowactivate", previous, check=False, timeout=3)

    @staticmethod
    def _chord(key: str) -> list[str]:
        """An xdotool chord for a key name, with macOS modifier names mapped (cmd -> super)."""
        try:
            keymap.parse_chord(key)
        except keymap.KeyNameError as exc:
            if "modifier" in str(exc) or "malformed" in str(exc) or "non-empty" in str(exc):
                raise BadArgument(str(exc)) from exc
        parts = key.strip().split("+")
        if key.strip().endswith("++") or key.strip() == "+":
            parts = key.strip()[:-1].split("+")[:-1] + ["plus"]
        mods = []
        for name in parts[:-1]:
            mapped = _MODIFIERS.get(name.strip().lower())
            if mapped is None:
                raise BadArgument(f"unknown modifier {name!r} in {key!r}")
            mods.append(mapped)
        last = parts[-1].strip()
        last = {"enter": "Return", "esc": "Escape", "backspace": "BackSpace", "delete": "Delete",
                "pageup": "Prior", "pagedown": "Next", "page_up": "Prior", "page_down": "Next", "space": "space",
                " ": "space"}.get(last.lower(), _MODIFIERS.get(last.lower(), last))
        return ["+".join([*mods, last])]

    def _do_click(self, app, state, args) -> str:
        button, count = click_args(args)
        try:
            mods = [_MODIFIERS[m.strip().lower()] for m in str(args.get("modifiers") or "").replace(",", "+").split("+")
                    if m.strip()]
        except KeyError as exc:
            raise BadArgument(f"unknown modifier {exc.args[0]!r}") from exc
        hold = seconds_arg(args.get("seconds"))
        index = args.get("element")
        if index is not None:
            acc = self._element(state, index)
            node = self._node_at(state, index)
            if count == 1 and not mods and not hold and button == "left":
                for name in _PRIMARY:
                    if self._do_action(acc, name):
                        return f"pressed {node.body()}"
                if node.role in ("text", "entry", "terminal", "document text") and self._grab_focus(acc):
                    return f"focused {node.body()}"
            if count == 1 and not mods and not hold and button == "right":
                for name in ("menu", "popup", "showmenu", "show menu", "context menu"):
                    if self._do_action(acc, name):
                        return f"opened the context menu of {node.body()}"
            point, what = self._center(acc), node.body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        self._xtest_click(state, point, button, count, mods, hold)
        return f"{click_words(button, count)} {what}" + (f" (held {hold:g}s)" if hold else "") + \
            " (the window came forward for the click, then focus went back)"

    def _xtest_click(self, state, point, button: str, count: int, mods: list[str], hold: float) -> None:
        with self._foreground(state):
            x11._x("mousemove", "--sync", int(round(point[0])), int(round(point[1])))
            for mod in mods:
                x11._x("keydown", mod)
            try:
                if hold:
                    x11._x("mousedown", _BUTTONS[button])
                    time.sleep(hold)
                    x11._x("mouseup", _BUTTONS[button])
                else:
                    x11._x("click", "--repeat", count, "--delay", 60, _BUTTONS[button])
            finally:
                for mod in reversed(mods):
                    x11._x("keyup", mod, check=False)
            time.sleep(0.05)

    def _grab_focus(self, acc) -> bool:
        """Move keyboard focus inside the app. GTK also presents the window when it does
        this, so the window the user had active gets focus back."""
        Atspi = _atspi()
        previous = x11._x("getactivewindow", check=False)
        done = bool(self._safe(lambda: Atspi.Component.grab_focus(acc), False))
        if previous:
            time.sleep(0.05)
            if x11._x("getactivewindow", check=False) != previous:
                x11._x("windowactivate", previous, check=False, timeout=3)
        return done

    def _do_hover(self, app, state, args) -> str:
        index = args.get("element")
        if index is not None:
            point, what = self._center(self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = seconds_arg(args.get("seconds")) or HOVER_SECONDS
        with self._foreground(state, keep_pointer=True):
            x11._x("mousemove", "--sync", int(round(point[0])), int(round(point[1])))
            time.sleep(rest)
        return f"rested the pointer over {what} for {rest:g}s (on Linux the real pointer moves there)"

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
        with self._foreground(state):
            x11._x("mousemove", "--sync", int(start[0]), int(start[1]))
            x11._x("mousedown", 1)
            try:
                for step in range(1, 13):
                    t = step / 12
                    x11._x("mousemove", int(start[0] + (end[0] - start[0]) * t), int(start[1] + (end[1] - start[1]) * t))
                    time.sleep(pause)
            finally:
                x11._x("mouseup", 1)
        return f"dragged from {source} to {target}" + (f" over {took:g}s" if took else "")

    def _scroll_bar(self, acc, direction: str):
        """The scroll bar that moves ``acc``'s content: its own, or one of an enclosing scroll pane."""
        vertical = direction in ("up", "down")
        current = acc
        for _ in range(12):
            if current is None:
                return None
            for child in self._children(current):
                if self._role(child) == "scroll bar" and "Value" in self._ifaces(child):
                    horizontal = self._has(child, "HORIZONTAL")
                    if vertical != horizontal:
                        return child
            current = self._safe(lambda current=current: current.get_parent())
        return None

    def _do_scroll(self, app, state, args) -> str:
        Atspi = _atspi()
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element")
        if index is not None:
            acc = self._element(state, index)
            node = self._node_at(state, index)
            bar = self._scroll_bar(acc, direction)
            if bar is not None:
                low = self._safe(lambda: Atspi.Value.get_minimum_value(bar), 0.0)
                high = self._safe(lambda: Atspi.Value.get_maximum_value(bar), 0.0)
                current = self._value(bar) or 0.0
                frame = self._extents(bar)
                page = (frame[3] if direction in ("up", "down") else frame[2]) * 0.9 if frame else (high - low) / 4
                step = pixels if pixels else page * pages
                sign = -1 if direction in ("up", "left") else 1
                target = max(low, min(high, current + sign * step))
                if self._safe(lambda: Atspi.Value.set_current_value(bar, target), False):
                    return f"scrolled {direction} {amount} in {node.body()}"
            point, what = self._center(acc), f"over {node.body()}"
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"at ({args.get('x')}, {args.get('y')})"
        notches = max(1, int(round(pixels / PIXELS_PER_NOTCH))) if pixels else max(1, int(round(pages * NOTCHES_PER_PAGE)))
        wheel = {"up": 4, "down": 5, "left": 6, "right": 7}[direction]
        with self._foreground(state):
            x11._x("mousemove", "--sync", int(point[0]), int(point[1]))
            x11._x("click", "--repeat", notches, "--delay", 20, wheel)
        return f"scrolled {direction} {amount} {what}"

    def _do_press_key(self, app, state, args) -> str:
        key = str(args.get("key") or "").strip()
        if not key:
            raise BadArgument("key is required")
        chord = self._chord(key)
        hold = seconds_arg(args.get("seconds"))
        target = None
        if args.get("element") is not None:
            if state is None:
                raise NotDispatched(f"call get_state for {app.name} first")
            target = self._element(state, args["element"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        # Return on a focused field fires its default action without leaving the background.
        if chord == ["Return"] and not hold and target is not None and self._do_action(target, "activate"):
            return f"pressed {key}"
        state = state or self._ensure_window(app)
        if args.get("element") is not None:
            self._grab_focus(target)
        with self._foreground(state):
            if hold:
                x11._x("keydown", *chord)
                time.sleep(hold)
                x11._x("keyup", *chord)
            else:
                x11._x("key", "--clearmodifiers", *chord)
        return f"pressed {key} (the window came forward for the key, then focus went back)"
