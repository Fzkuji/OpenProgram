"""The actions a step names, each run on the window the model last read."""
from __future__ import annotations

import time
from typing import Any

from ..common import keys as keymap
from ..common.base import (CUE_LEAD_SECONDS, HOVER_SECONDS, MAX_TEXT_CHARS, BadArgument, ComputerUseError,
    NotDispatched, Report, check_cancelled as _check_cancelled, click_args, click_words as _click_words, find_text,
    scroll_args, seconds_arg as _seconds)
from .skylight import SUBTYPE_TOUCH
from ..common.tree import role_name
from .frameworks import _NOT_TEXT_ROLES, _ROW_DEPTH, _TEXT_ROLES, _unlocked
from .clipboard import utf16_len
from . import frameworks


class Actions:
    """``act`` and the actions that are not part of another concern."""

    # Actions that need no earlier state of the window, and actions that need
    # it only to resolve an element (otherwise they act on the focus).
    STATELESS = frozenset({"menu", "show_window", "wait", "open_file"})
    AT_FOCUS = frozenset({"copy", "press_key", "type_text", "paste"})

    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        """Run one action and return a short description of what happened."""
        _unlocked()
        handler = getattr(self, f"_do_{action}", None)
        if handler is None:
            raise NotDispatched(f"unknown action {action!r}")
        app = self.resolve_app(args.get("app") or "", launch=action == "open_file")
        stateless = action in self.STATELESS or (action in self.AT_FOCUS and args.get("element") is None)
        if not stateless:
            # A window the model has not read fails here, before anything is waited for.
            self._state_for(session, args.get("app") or "", app)
        if action != "wait":
            # A step sent to a busy app would run whenever it comes back, on whatever it shows then.
            self._await_answer(app)
        with self._lock:
            if stateless:
                return handler(app, int(app.processIdentifier()), self._states.get(
                    (session, int(app.processIdentifier()))), args)
            app, state = self._state_for(session, args.get("app") or "", app)
            # The window may have moved since it was read; points follow it.
            self._follow_window(state)
            self._control(session, state.pid, str(app.localizedName() or ""), None, state.window_id)
            self._cue(app, state, action, args)
            return handler(app, int(app.processIdentifier()), state, args)

    def _cue(self, app, state, action: str, args: dict[str, Any]) -> None:
        """Show the user where this step lands: the App draws its agent cursor there."""
        try:
            # A window on another Space or in the Dock is not where the point is.
            if state.window_id and state.window_id not in self._onscreen_ids(int(app.processIdentifier())):
                return
            if args.get("element") is not None:
                point = self._center(self._element(state, args["element"]))
            elif args.get("x") is not None and args.get("y") is not None:
                point = self._point(state, args.get("x"), args.get("y"))
            else:
                return
            from openprogram.host.resources.native_window import note_action_point
            note_action_point(str(app.localizedName() or ""), point, action)
        except Exception:
            # The cue is a courtesy; a step never fails because of it.
            return
        # Let the cursor arrive before the step acts.
        time.sleep(CUE_LEAD_SECONDS)

    def _do_click(self, app, pid, state, args) -> str:
        button, count = click_args(args)
        try:
            flags = keymap.modifier_flags(args.get("modifiers"))
        except keymap.KeyNameError as exc:
            raise BadArgument(str(exc)) from exc
        hold = _seconds(args.get("seconds"))
        index = args.get("element")
        if index is not None:
            element = self._element(state, index)
            node = self._node_at(state, index)
            actions = self._actions(element)
            simple = count == 1 and not flags and not hold
            if simple and button == "left":
                row = self._row_of(element, node)
                for action in ("AXPress", "AXConfirm", "AXPick"):
                    if action in actions and self._perform(element, action) == 0:
                        return f"pressed {node.body()}"
                    # Outlook's message cells answer a press with an error and select the row all the same.
                    if action in actions and row is not None and self._attr(row, "AXSelected") is True:
                        return f"selected {node.body()}"
                if row is not None and self._select_row(row):
                    return f"selected {node.body()}"
                if node.role in _TEXT_ROLES and self._settable(element, "AXFocused"):
                    if self._set(element, "AXFocused", True) == 0:
                        return f"focused {node.body()}"
            if simple and button == "right" and "AXShowMenu" in actions:
                if self._perform(element, "AXShowMenu") == 0:
                    return f"opened the context menu of {node.body()}"
            self._background_click(state, pid, self._reachable_center(state, element), button, count, flags, hold)
            return f"{_click_words(button, count)} {node.body()}" + (f" (held {hold:g}s)" if hold else "")
        point = self._point(state, args.get("x"), args.get("y"))
        self._background_click(state, pid, point, button, count, flags, hold)
        return (f"{_click_words(button, count)} at ({args.get('x')}, {args.get('y')})"
                + (f" (held {hold:g}s)" if hold else ""))

    def _row_of(self, element, node):
        """The row of a list or table that ``element`` is or lies in, when it can be selected.

        Selecting the row is what a click on it does, and it needs no mouse:
        a mouse-down, even one delivered in the background, lets the app bring
        its window forward or make itself the front app.
        """
        if node.role not in ("AXRow", "AXOutlineRow", "AXCell"):
            return None
        candidate = element
        for _ in range(_ROW_DEPTH):
            if candidate is None:
                return None
            if self._settable(candidate, "AXSelected"):
                return candidate
            candidate = self._attr(candidate, "AXParent")
            if candidate is None or self._attr(candidate, "AXRole") not in ("AXRow", "AXOutlineRow", "AXCell"):
                return None
        return None

    def _select_row(self, row) -> bool:
        return self._attr(row, "AXSelected") is True or self._set(row, "AXSelected", True) == 0

    def _do_hover(self, app, pid, state, args) -> str:
        """Move the pointer over an element or point, in the app only, and rest there."""
        index = args.get("element")
        if index is not None:
            point, what = self._reachable_center(state, self._element(state, index)), self._node_at(state, index).body()
        else:
            point, what = self._point(state, args.get("x"), args.get("y")), f"({args.get('x')}, {args.get('y')})"
        rest = _seconds(args.get("seconds")) or HOVER_SECONDS
        sky = self._route(state)
        _, _, cg, _ = frameworks._frameworks()
        group = sky.next_group()
        # Approach in a few steps so tracking areas see the pointer enter.
        for fraction in (0.0, 0.5, 1.0):
            at = (point[0] - 12 * (1 - fraction), point[1] - 12 * (1 - fraction))
            move = self._mouse_event(cg, cg.kCGEventMouseMoved, at, cg.kCGMouseButtonLeft)
            self._post_mouse(sky, cg, state, pid, move, at, click_state=0, button=0, subtype=SUBTYPE_TOUCH,
                             group=group)
            time.sleep(0.03)
        time.sleep(rest)
        return f"hovered over {what} for {rest:g}s"

    def _do_wait(self, app, pid, state, args) -> str:
        """Let time pass, then report what changed (a download, an animation, a reply)."""
        rest = _seconds(args.get("seconds")) or 1.0
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            _check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return f"waited {rest:g}s"

    def _do_drag(self, app, pid, state, args) -> str:
        start, source = self._drag_end(state, args, "element", "x", "y")
        end, target = self._drag_end(state, args, "to_element", "to_x", "to_y")
        took = _seconds(args.get("seconds"))
        self._background_drag(state, pid, start, end, took)
        return f"dragged from {source} to {target}" + (f" over {took:g}s" if took else "")

    def _do_scroll(self, app, pid, state, args) -> str:
        direction, pages, pixels = scroll_args(args)
        amount = f"{pixels:g} pixels" if pixels else f"{pages:g} page(s)"
        index = args.get("element")
        if index is not None and pixels:
            element = self._element(state, index)
            self._background_scroll(state, pid, self._center(element), direction, pages, pixels)
            return f"scrolled {direction} {amount} over {self._node_at(state, index).body()}"
        if index is not None:
            element = self._element(state, index)
            node = self._node_at(state, index)
            action = {"up": "AXScrollUpByPage", "down": "AXScrollDownByPage",
                      "left": "AXScrollLeftByPage", "right": "AXScrollRightByPage"}[direction]
            target = element
            for _ in range(12):
                if target is None:
                    break
                if action in self._actions(target):
                    for _ in range(max(1, int(round(pages)))):
                        self._perform(target, action)
                        time.sleep(0.05)
                    return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
                bar = self._attr(target, "AXVerticalScrollBar" if direction in ("up", "down") else "AXHorizontalScrollBar")
                if bar is not None and self._settable(bar, "AXValue"):
                    current = self._attr(bar, "AXValue")
                    if isinstance(current, (int, float)):
                        step = self._page_fraction(target, direction) * pages
                        sign = -1 if direction in ("up", "left") else 1
                        self._set(bar, "AXValue", max(0.0, min(1.0, float(current) + sign * step)))
                        return f"scrolled {direction} {pages:g} page(s) in {node.body()}"
                target = self._attr(target, "AXParent")
            self._background_scroll(state, pid, self._center(element), direction, pages)
            return f"scrolled {direction} {pages:g} page(s) over {node.body()}"
        point = self._point(state, args.get("x"), args.get("y"))
        self._background_scroll(state, pid, point, direction, pages, pixels)
        return f"scrolled {direction} {amount} at ({args.get('x')}, {args.get('y')})"

    def _do_press_key(self, app, pid, state, args) -> str:
        try:
            chord = keymap.parse_chord(str(args.get("key") or ""))
        except keymap.KeyNameError as exc:
            raise BadArgument(str(exc)) from exc
        if args.get("element") is not None:
            self._focus(self._element(state, args["element"]))
        if chord.text:
            self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            raise ComputerUseError(
                "The window is on another Space, where macOS does not deliver keys to it. Use element actions "
                "(click, set_value, menu) instead; type_text and paste still insert text into a focused field.")
        self._refuse_file_panel(app, pid)
        self._post_chord(pid, chord, _seconds(args.get("seconds")))
        return f"pressed {args.get('key')}"

    def _do_type_text(self, app, pid, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise BadArgument(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        if args.get("element") is not None:
            element = self._element(state, args["element"])
            node = self._node_at(state, args["element"])
            self._refuse_secure(node)
            if node.role in _NOT_TEXT_ROLES:
                raise NotDispatched(
                    f"element {args['element']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
            self._focus(element)
        self._refuse_secure(None, self._focused_element(pid))
        self._refuse_secure_input()
        if not self._keys_reach(pid, state):
            if not self._insert(pid, text):
                raise ComputerUseError(
                    "The window is on another Space, where macOS does not deliver keys to it, and the focused "
                    "element takes no inserted text. Focus a text field first, or use element actions.")
            newline_note = "; a newline went in as text, not as Return" if "\n" in text or "\r" in text else ""
            return (f"inserted {len(text)} character(s) at the focused field (the window is on another Space, "
                    f"where keys do not arrive{newline_note})")
        self._refuse_file_panel(app, pid)
        self._post_text(pid, text)
        return f"typed {len(text)} character(s)"

    def _do_set_value(self, app, pid, state, args) -> str:
        index = args.get("element")
        element = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(node, element)
        if not self._settable(element, "AXValue"):
            raise NotDispatched(f"element {index} has no settable value; use click or type_text")
        value = args.get("value")
        if value is None:
            raise BadArgument("value is required")
        current = self._attr(element, "AXValue")
        if isinstance(current, (int, float)) and not isinstance(current, bool):
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
        elif not isinstance(value, str):
            value = str(value)
        code = self._set(element, "AXValue", value)
        if code != 0:
            raise ComputerUseError(f"the app refused the value (Accessibility error {code})")
        return f"set {node.body()} to {value!r}"

    def _do_perform_secondary_action(self, app, pid, state, args) -> str:
        index = args.get("element")
        element = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip()
        if not wanted:
            raise BadArgument("action_name is required")
        actions = self._actions(element)
        normalized = {a.lower(): a for a in actions}
        squashed = "ax" + wanted.replace(" ", "").replace("_", "").lower().removeprefix("ax")
        match = normalized.get(wanted.lower()) or normalized.get(squashed)
        if match is None:
            listed = ", ".join(actions) or "none"
            raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: {listed}")
        code = self._perform(element, match)
        if code != 0:
            raise ComputerUseError(f"{match} failed (Accessibility error {code})")
        return f"performed {match} on {node.body()}"

    def _do_select_text(self, app, pid, state, args) -> str:
        _, ax, _, _ = frameworks._frameworks()
        index = args.get("element")
        element = self._element(state, index)
        node = self._node_at(state, index)
        value = self._attr(element, "AXValue")
        if not isinstance(value, str):
            raise ComputerUseError(f"element {index} has no text value to select in")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        start = find_text(value, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        location = utf16_len(value[:start])
        length = utf16_len(text)
        kind = str(args.get("selection_type") or "text")
        if kind == "cursor_before":
            length = 0
        elif kind == "cursor_after":
            location, length = location + length, 0
        elif kind != "text":
            raise BadArgument("selection_type must be text, cursor_before or cursor_after")
        self._focus(element)
        selection = ax.AXValueCreate(ax.kAXValueCFRangeType, (location, length))
        code = self._set(element, "AXSelectedTextRange", selection)
        if code != 0:
            raise ComputerUseError(f"the app refused the selection (Accessibility error {code})")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_focus(self, app, pid, state, args) -> str:
        index = args.get("element")
        element = self._element(state, index)
        node = self._node_at(state, index)
        if "secure" in node.flags:
            raise ComputerUseError("This is a password field; computer_use does not focus or type into it.")
        if not self._settable(element, "AXFocused"):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        if self._attr(element, "AXFocused") is True:
            return Report(f"already focused {node.body()}")
        self._set(element, "AXFocused", True)
        # Apps apply focus on their next run-loop turn, sometimes later.
        deadline = time.monotonic() + 0.5
        while self._attr(element, "AXFocused") is not True and time.monotonic() < deadline:
            time.sleep(0.05)
        if self._attr(element, "AXFocused") is not True:
            raise ComputerUseError(f"focus did not move to element {index}; the app keeps its text cursor in "
                                   "another window. Click the element instead.")
        return f"focused {node.body()}"
