"""Pointer input posted to one window's process through SkyLight, in the background."""
from __future__ import annotations

import math
import time
from typing import Any

from ..common import keys as keymap
from ..common.base import BadArgument, ComputerUseError, NotDispatched, check_cancelled as _check_cancelled
from .skylight import SUBTYPE_DEFAULT, SUBTYPE_TOUCH
from .frameworks import (LINES_PER_TICK, MAX_DRAG_STEPS, PIXELS_PER_EVENT, SCROLL_SETTLE_MAX, TICKS_PER_PAGE,
    WindowState, _BUTTONS)
from .geometry import _intersect, covering_rows, screen_point, window_location
from . import frameworks
from . import skylight


class Pointer:
    """Where a point lands in the window, and clicks, drags and scrolls posted there."""

    def _point(self, state: WindowState, x, y) -> tuple[float, float]:
        if x is None or y is None:
            raise BadArgument("x and y are required when no element is given")
        if not state.has_screenshot:
            raise BadArgument("This window has no screenshot to take coordinates from; use element.")
        try:
            px, py = float(x), float(y)
        except (TypeError, ValueError) as exc:
            raise BadArgument("x and y must be numbers") from exc
        width, height = state.size[0] * state.scale, state.size[1] * state.scale
        if not (math.isfinite(px) and math.isfinite(py) and 0 <= px <= width and 0 <= py <= height):
            raise BadArgument(f"({x}, {y}) is outside the {int(width)}x{int(height)} screenshot")
        return screen_point(px, py, origin=state.origin, scale=state.scale)

    def _visible_rect(self, state: WindowState, element) -> tuple[float, float, float, float] | None:
        """The part of the window where ``element`` can show: the window cut by its scroll areas and web area.

        An element scrolled above a web page sits under the browser's toolbar,
        still inside the window; only its scrolling ancestors know it is hidden.
        """
        rect = (state.origin[0], state.origin[1], state.size[0], state.size[1]) if state.size[0] > 0 else None
        node, depth = self._attr(element, "AXParent"), 0
        while node is not None and depth < 40:
            if self._attr(node, "AXRole") in ("AXScrollArea", "AXWebArea"):
                frame = self._frame(node)
                if frame is not None and frame[2] > 0 and frame[3] > 0:
                    rect = frame if rect is None else _intersect(rect, frame)
            node, depth = self._attr(node, "AXParent"), depth + 1
        return rect

    def _shown(self, rect, frame) -> bool:
        """Whether the centre of ``frame`` lies inside ``rect`` (no rect: nothing to check)."""
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            return False
        if rect is None:
            return True
        x, y = frame[0] + frame[2] / 2, frame[1] + frame[3] / 2
        return rect[0] <= x <= rect[0] + rect[2] and rect[1] <= y <= rect[1] + rect[3]

    def _reachable_center(self, state: WindowState, element) -> tuple[float, float]:
        """Where the pointer can reach ``element``: its centre, scrolled into view first.

        A long page or list keeps the elements scrolled out of view in its
        tree, with no frame or with one outside what is shown, where a pointer
        event lands on nothing or on the toolbar. Those that offer
        AXScrollToVisible are scrolled into view, and the centre is read again
        once it stops moving.
        """
        frame = self._frame(element)
        if self._shown(self._visible_rect(state, element), frame):
            return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2
        if "AXScrollToVisible" in self._actions(element) and self._perform(element, "AXScrollToVisible") == 0:
            frame = self._settled_frame(state, element)
        if (frame is not None and frame[2] > 0 and frame[3] > 0
                and not self._shown(self._visible_rect(state, element), frame)
                and self._scroll_areas_to(element, frame)):
            # Native lists and tables often lack AXScrollToVisible; their scroll bars move them.
            frame = self._settled_frame(state, element)
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            raise NotDispatched(
                "This element has no on-screen frame to point at: it is hidden, such as an option of a menu or "
                "dropdown that is closed now. Open what holds it (click the dropdown or field), then use the "
                "indices the result shows.")
        if not self._shown(self._visible_rect(state, element), frame):
            raise NotDispatched(
                "This element is outside the visible part of the window and could not be scrolled into view; "
                "scroll toward it first, or act on it with set_value or perform_secondary_action.")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _settled_frame(self, state: WindowState, element):
        """The element's frame once a scroll has stopped: pages scroll smoothly, so two reads must agree."""
        deadline = time.monotonic() + SCROLL_SETTLE_MAX
        frame = previous = None
        while time.monotonic() < deadline:
            time.sleep(0.05)
            frame = self._frame(element)
            if frame is not None and frame == previous:
                break
            previous = frame
        self._follow_window(state)
        return frame

    def _scroll_areas_to(self, element, frame) -> bool:
        """Centre ``element`` in each scroll area that holds it by setting the area's scroll bars.

        The position inside an area's contents is the element's frame less the
        contents' frame; a scroll bar's value is the share of the scrollable
        length scrolled past. Returns whether any bar moved.
        """
        moved, node, depth = False, self._attr(element, "AXParent"), 0
        while node is not None and depth < 40:
            if self._attr(node, "AXRole") == "AXScrollArea":
                area = self._frame(node)
                contents = self._attr(node, "AXContents") or []
                inner = self._frame(contents[0]) if contents else None
                if area and inner:
                    for axis, bar_name in ((1, "AXVerticalScrollBar"), (0, "AXHorizontalScrollBar")):
                        shown, total = area[axis + 2], inner[axis + 2]
                        bar = self._attr(node, bar_name)
                        if total <= shown or bar is None or not self._settable(bar, "AXValue"):
                            continue
                        middle = frame[axis] + frame[axis + 2] / 2 - inner[axis]
                        value = max(0.0, min(1.0, (middle - shown / 2) / (total - shown)))
                        moved = self._set(bar, "AXValue", value) == 0 or moved
            node, depth = self._attr(node, "AXParent"), depth + 1
        return moved

    def _route(self, state: WindowState):
        """The SkyLight route for this window, or an explanation of why not."""
        sky = skylight.skylight()
        if not sky.available:
            raise ComputerUseError("Background pointer input is unavailable on this macOS; use element actions.")
        if not state.window_id:
            raise ComputerUseError("The window has no window-server id to route pointer input to; use element actions.")
        return sky

    def _covering(self, state: WindowState) -> list[Any]:
        """Other windows stacked over the operated one, deepest first, as Accessibility windows.

        An app orders its window to the front when it handles a mouse-down,
        even one delivered in the background. Raising these again after the
        gesture puts the stack back as the user left it. The front app's
        windows stay above the operated one anyway and are left alone.
        """
        try:
            appkit, ax, cg, _ = frameworks._frameworks()
            front = appkit.NSWorkspace.sharedWorkspace().frontmostApplication()
            rows = cg.CGWindowListCopyWindowInfo(cg.kCGWindowListOptionOnScreenOnly, cg.kCGNullWindowID) or []
            above = covering_rows(rows, state.window_id, int(front.processIdentifier()) if front is not None else -1)
            sky, windows, found = skylight.skylight(), {}, []
            for row in above:
                pid = int(row["kCGWindowOwnerPID"])
                if pid not in windows:
                    windows[pid] = list(self._attr(ax.AXUIElementCreateApplication(pid), "AXWindows") or [])
                wid = int(row["kCGWindowNumber"])
                found.extend(w for w in windows[pid] if sky.window_id(w) == wid)
            return found
        except Exception:
            # The order is a courtesy; a step never fails because of it.
            return []

    def _restore_order(self, covering: list[Any]) -> None:
        for window in covering:
            self._perform(window, "AXRaise")

    def _mouse_event(self, cg, kind, point, button_code, flags=0):
        source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
        event = cg.CGEventCreateMouseEvent(source, kind, point, button_code)
        if flags:
            cg.CGEventSetFlags(event, flags)
        return event

    def _post_mouse(self, sky, cg, state, pid, event, point, *, click_state, button, subtype, group):
        sky.stamp_mouse(event, pid=pid, window_id=state.window_id, click_state=click_state, button=button,
                        subtype=subtype, group=group, location=window_location(point, state.origin))
        sky.post(pid, event)

    def _primer(self, sky, cg, state, pid, point, group) -> None:
        """A mouseMoved at the point first: AppKit hit-tests against the last tracked position."""
        move = self._mouse_event(cg, cg.kCGEventMouseMoved, point, cg.kCGMouseButtonLeft)
        self._post_mouse(sky, cg, state, pid, move, point, click_state=0, button=0, subtype=SUBTYPE_TOUCH, group=group)
        time.sleep(0.012)

    def _with_modifiers(self, pid: int, flags: int, body) -> None:
        """Hold modifier keys in the target process around a pointer gesture."""
        held = []
        try:
            for flag, code in keymap.MODIFIER_KEYCODES.items():
                if flags & flag:
                    self._post_key_event(pid, code, True, flags)
                    held.append(code)
                    time.sleep(0.008)
            body()
        finally:
            for code in reversed(held):
                self._post_key_event(pid, code, False, 0)
                time.sleep(0.008)

    def _background_click(self, state: WindowState, pid: int, point, button: str, count: int, flags: int,
                          hold: float = 0.0) -> None:
        sky = self._route(state)
        _, _, cg, _ = frameworks._frameworks()
        kinds = {
            "left": (cg.kCGEventLeftMouseDown, cg.kCGEventLeftMouseUp, cg.kCGMouseButtonLeft),
            "right": (cg.kCGEventRightMouseDown, cg.kCGEventRightMouseUp, cg.kCGMouseButtonRight),
            "middle": (cg.kCGEventOtherMouseDown, cg.kCGEventOtherMouseUp, cg.kCGMouseButtonCenter),
        }
        down, up, code = kinds[button]
        number = _BUTTONS[button]
        sky.focus_without_raise(pid, state.window_id)
        time.sleep(0.05)
        group = sky.next_group()

        def gesture():
            self._primer(sky, cg, state, pid, point, group)
            for click in range(1, count + 1):
                event = self._mouse_event(cg, down, point, code, flags)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=click, button=number,
                                 subtype=SUBTYPE_TOUCH, group=group)
                # An NSButton's mouseDown enters a tracking loop that polls for
                # the mouseUp; too tight a gap and the click is dropped.
                time.sleep(max(0.028, hold))
                event = self._mouse_event(cg, up, point, code, flags)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=click, button=number,
                                 subtype=SUBTYPE_TOUCH, group=group)
                if count > 1:
                    time.sleep(0.08)

        covering = self._covering(state)
        try:
            self._with_modifiers(pid, flags, gesture)
        finally:
            self._restore_order(covering)

    def _background_drag(self, state: WindowState, pid: int, start, end, seconds: float = 0.0) -> None:
        sky = self._route(state)
        _, _, cg, _ = frameworks._frameworks()
        left = cg.kCGMouseButtonLeft
        covering = self._covering(state)
        sky.focus_without_raise(pid, state.window_id)
        time.sleep(0.05)
        group = sky.next_group()
        try:
            self._primer(sky, cg, state, pid, start, group)
            event = self._mouse_event(cg, cg.kCGEventLeftMouseDown, start, left)
            self._post_mouse(sky, cg, state, pid, event, start, click_state=1, button=0, subtype=SUBTYPE_DEFAULT,
                             group=group)
            time.sleep(0.12)
            distance = math.hypot(end[0] - start[0], end[1] - start[1])
            steps = max(2, min(MAX_DRAG_STEPS, int(distance / 12) + 1))
            # A slow drag (seconds) spreads the same moves over that time, for apps that wait for a hover.
            pause = max(0.016, seconds / steps) if seconds else 0.016
            for step in range(1, steps + 1):
                t = step / steps
                point = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
                event = self._mouse_event(cg, cg.kCGEventLeftMouseDragged, point, left)
                self._post_mouse(sky, cg, state, pid, event, point, click_state=1, button=0,
                                 subtype=SUBTYPE_DEFAULT, group=group)
                time.sleep(pause)
            time.sleep(0.08)
            event = self._mouse_event(cg, cg.kCGEventLeftMouseUp, end, left)
            self._post_mouse(sky, cg, state, pid, event, end, click_state=1, button=0, subtype=SUBTYPE_DEFAULT,
                             group=group)
        finally:
            self._restore_order(covering)

    def _background_scroll(self, state: WindowState, pid: int, point, direction: str, pages: float,
                           pixels: float = 0.0) -> None:
        sky = self._route(state)
        _, _, cg, _ = frameworks._frameworks()
        if pixels:
            ticks = max(1, int(math.ceil(pixels / PIXELS_PER_EVENT)))
            step, unit = int(round(pixels / ticks)), cg.kCGScrollEventUnitPixel
        else:
            ticks = max(1, int(round(pages * TICKS_PER_PAGE)))
            step, unit = LINES_PER_TICK, cg.kCGScrollEventUnitLine
        # macOS: a positive vertical delta reveals content above.
        dy = {"up": step, "down": -step}.get(direction, 0)
        dx = {"left": step, "right": -step}.get(direction, 0)
        group = sky.next_group()
        self._primer(sky, cg, state, pid, point, group)
        for _ in range(ticks):
            _check_cancelled()
            source = cg.CGEventSourceCreate(cg.kCGEventSourceStateHIDSystemState)
            event = cg.CGEventCreateScrollWheelEvent(source, unit, 2, dy, dx)
            cg.CGEventSetLocation(event, point)
            self._post_mouse(sky, cg, state, pid, event, point, click_state=0, button=0, subtype=SUBTYPE_DEFAULT, group=group)
            time.sleep(0.03)

    def _drag_end(self, state: WindowState, args, element: str, x: str, y: str) -> tuple[tuple[float, float], str]:
        """One end of a drag, by element or by x,y of the last screenshot, and how the result names it."""
        if args.get(element) is not None:
            index = args[element]
            return self._reachable_center(state, self._element(state, index)), self._node_at(state, index).body()
        return self._point(state, args.get(x), args.get(y)), f"({args.get(x)}, {args.get(y)})"

    def _page_fraction(self, area, direction) -> float:
        frame = self._frame(area)
        contents = self._attr(area, "AXContents") or self._attr(area, "AXChildren") or []
        inner = self._frame(contents[0]) if contents else None
        if frame and inner:
            visible = frame[3] if direction in ("up", "down") else frame[2]
            total = inner[3] if direction in ("up", "down") else inner[2]
            if total > visible > 0:
                return 0.9 * visible / (total - visible)
        return 0.25
