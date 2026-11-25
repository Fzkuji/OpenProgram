"""The actions a step names, each run on the window the model last read."""
from __future__ import annotations

import time
from typing import Any

from ..common.base import BadArgument, NotDispatched, ComputerUseError, check_cancelled, find_text, seconds_arg
from .send_input import _Forward
from .win32 import EXPAND_COLLAPSE, INVOKE, LEGACY, RANGE_VALUE, SELECTION_ITEM, TEXT, TOGGLE, VALUE
from . import win32


class Actions:
    """``act`` and the element actions that are not part of another concern."""

    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        with self._lock:
            win32._uia()
            handler = getattr(self, f"_do_{action}", None)
            if handler is None:
                raise NotDispatched(f"unknown action {action!r}")
            if action in self.STATELESS or (action in self.AT_FOCUS and args.get("element") is None):
                app = self.resolve_app(args.get("app") or "", launch=False)
                return handler(app, self._states.get((session, app.pid)), args)
            app, state = self._state_for(session, args.get("app") or "")
            return handler(app, state, args)

    def _do_wait(self, app, state, args) -> str:
        rest = seconds_arg(args.get("seconds")) or 1.0
        deadline = time.monotonic() + rest
        while time.monotonic() < deadline:
            check_cancelled()
            time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
        return f"waited {rest:g}s"

    def _do_set_value(self, app, state, args) -> str:
        index = args.get("element")
        el = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(el)
        value = args.get("value")
        if value is None:
            raise BadArgument("value is required")
        toggle = self._pattern(el, TOGGLE)
        if toggle is not None:
            wanted = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
            for _ in range(3):
                if (self._safe(lambda: int(toggle.CurrentToggleState), 0) == 1) == wanted:
                    return f"set {node.body()} to {wanted}"
                toggle.Toggle()
            raise ComputerUseError(f"element {index} did not take the state {wanted}")
        ranged = self._pattern(el, RANGE_VALUE)
        if ranged is not None and not isinstance(value, bool):
            try:
                number_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
            if not self._safe(lambda: ranged.SetValue(number_value) or True, False):
                raise ComputerUseError("the app refused the value")
            return f"set {node.body()} to {number_value:g}"
        pattern = self._pattern(el, VALUE)
        if pattern is not None and not self._safe(lambda: pattern.CurrentIsReadOnly, True):
            if not self._safe(lambda: pattern.SetValue(str(value)) or True, False):
                raise ComputerUseError("the app refused the text")
            return f"set {node.body()} to {str(value)!r}"
        legacy = self._pattern(el, LEGACY)
        if legacy is not None and self._safe(lambda: legacy.SetValue(str(value)) or True, False):
            return f"set {node.body()} to {str(value)!r}"
        raise NotDispatched(f"element {index} has no settable value; use click or type_text")

    def _do_perform_secondary_action(self, app, state, args) -> str:
        index = args.get("element")
        el = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip().lower()
        if not wanted:
            raise BadArgument("action_name is required")
        calls = {"expand": (EXPAND_COLLAPSE, "Expand"), "collapse": (EXPAND_COLLAPSE, "Collapse"),
                 "select": (SELECTION_ITEM, "Select"), "invoke": (INVOKE, "Invoke"), "toggle": (TOGGLE, "Toggle")}
        if wanted in calls:
            pattern = self._pattern(el, calls[wanted][0])
            if pattern is not None and self._safe(lambda: getattr(pattern, calls[wanted][1])() or True, False):
                return f"performed {wanted} on {node.body()}"
        legacy = self._pattern(el, LEGACY)
        default = self._safe(lambda: str(legacy.CurrentDefaultAction or ""), "") if legacy is not None else ""
        if default and default.lower() == wanted and self._safe(lambda: legacy.DoDefaultAction() or True, False):
            return f"performed {default} on {node.body()}"
        raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: "
                               + (", ".join(self._actions(el)) or "none"))

    def _do_select_text(self, app, state, args) -> str:
        index = args.get("element")
        el = self._element(state, index)
        node = self._node_at(state, index)
        pattern = self._pattern(el, TEXT)
        if pattern is None:
            raise ComputerUseError(f"element {index} has no text to select in")
        whole = self._safe(lambda: str(pattern.DocumentRange.GetText(-1) or ""), "")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        start = find_text(whole, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        kind = str(args.get("selection_type") or "text")
        if kind not in ("text", "cursor_before", "cursor_after"):
            raise BadArgument("selection_type must be text, cursor_before or cursor_after")
        # Move a range from the document start by characters, then shrink it to the match.
        found = pattern.DocumentRange.Clone()
        unit_character, endpoint_start, endpoint_end = 0, 0, 1
        found.MoveEndpointByRange(endpoint_end, found, endpoint_start)
        found.MoveEndpointByUnit(endpoint_end, unit_character, start + len(text))
        found.MoveEndpointByUnit(endpoint_start, unit_character, start)
        if kind == "cursor_before":
            found.MoveEndpointByRange(endpoint_end, found, endpoint_start)
        elif kind == "cursor_after":
            found.MoveEndpointByRange(endpoint_start, found, endpoint_end)
        if not self._safe(lambda: found.Select() or True, False):
            raise ComputerUseError("the app refused the selection")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_focus(self, app, state, args) -> str:
        index = args.get("element")
        el = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(el)
        if not self._safe(lambda: el.CurrentIsKeyboardFocusable, False):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        front = win32._user32().GetForegroundWindow()
        self._safe(lambda: el.SetFocus())
        if front and win32._user32().GetForegroundWindow() != front:
            _Forward.restore(int(front))
        return f"focused {node.body()}"
