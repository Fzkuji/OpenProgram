"""The actions a step names, each run on the window the model last read."""
from __future__ import annotations

import time
from typing import Any

from ..common.base import (MAX_COPY_CHARS, MAX_TEXT_CHARS, BadArgument, NotDispatched, ComputerUseError, Report,
    check_cancelled, find_text, menu_key, menu_path, seconds_arg)
from ..common.tree import role_name
from .x11 import App, _MENU_ITEMS, _NOT_TEXT_ROLES, _TOGGLE_ROLES, _atspi
from . import x11


class Actions:
    """``act`` and the element actions: text, values, selection, focus, clipboard and menus."""

    def act(self, session: str, action: str, args: dict[str, Any]) -> str:
        with self._lock:
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

    def _insert(self, acc, text: str) -> bool:
        """Insert ``text`` at the caret of an editable element, in the background."""
        Atspi = _atspi()
        if "EditableText" not in self._ifaces(acc) or not self._has(acc, "EDITABLE"):
            return False
        caret = self._safe(lambda: Atspi.Text.get_caret_offset(acc), -1)
        if caret is None or caret < 0:
            caret = self._safe(lambda: Atspi.Text.get_character_count(acc), 0) or 0
        if self._safe(lambda: Atspi.Text.get_n_selections(acc), 0):
            selected = self._safe(lambda: Atspi.Text.get_selection(acc, 0))
            if selected is not None and selected.end_offset > selected.start_offset:
                self._safe(lambda: Atspi.EditableText.delete_text(acc, selected.start_offset, selected.end_offset))
                caret = selected.start_offset
        if not self._safe(lambda: Atspi.EditableText.insert_text(acc, caret, text, len(text.encode())), False):
            return False
        self._safe(lambda: Atspi.Text.set_caret_offset(acc, caret + len(text)))
        return True

    def _do_type_text(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        if len(text) > MAX_TEXT_CHARS:
            raise BadArgument(f"text is longer than {MAX_TEXT_CHARS} characters; use paste")
        target = None
        if args.get("element") is not None:
            target = self._element(state, args["element"])
            node = self._node_at(state, args["element"])
            if node.role in _NOT_TEXT_ROLES:
                raise NotDispatched(
                    f"element {args['element']} is a {role_name(node.role)}, not a text field: typed text "
                    "would land in whatever field has focus. Click it, use set_value, or pick the text field.")
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        lines = text.split("\n")
        if target is not None and "\n" not in text and self._insert(target, text):
            return f"typed {len(text)} character(s)"
        if target is not None and "EditableText" in self._ifaces(target) and all(
                self._insert(target, line) and (i == len(lines) - 1 or self._do_action(target, "activate"))
                for i, line in enumerate(lines)):
            return f"typed {len(text)} character(s); each newline pressed Return"
        state = state or self._ensure_window(app)
        if args.get("element") is not None:
            self._grab_focus(target)
        with self._foreground(state):
            for i, line in enumerate(lines):
                if line:
                    x11._x("type", "--clearmodifiers", "--delay", 8, "--", line, timeout=60)
                if i < len(lines) - 1:
                    x11._x("key", "Return")
        return f"typed {len(text)} character(s) (the window came forward for the keys, then focus went back)"

    def _do_set_value(self, app, state, args) -> str:
        Atspi = _atspi()
        index = args.get("element")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(acc)
        value = args.get("value")
        if value is None:
            raise BadArgument("value is required")
        if node.role in _TOGGLE_ROLES:
            wanted = value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
            on = self._has(acc, "CHECKED") or self._has(acc, "PRESSED")
            if on != wanted and not any(self._do_action(acc, name) for name in ("toggle", "click", "press")):
                raise ComputerUseError(f"element {index} cannot be toggled")
            return f"set {node.body()} to {wanted}"
        if "Value" in self._ifaces(acc) and not isinstance(value, bool):
            try:
                number_value = float(value)
            except (TypeError, ValueError) as exc:
                raise ComputerUseError("this element takes a number") from exc
            if not self._safe(lambda: Atspi.Value.set_current_value(acc, number_value), False):
                raise ComputerUseError("the app refused the value")
            return f"set {node.body()} to {number_value:g}"
        if "EditableText" in self._ifaces(acc) and self._has(acc, "EDITABLE"):
            if not self._safe(lambda: Atspi.EditableText.set_text_contents(acc, str(value)), False):
                raise ComputerUseError("the app refused the text")
            return f"set {node.body()} to {str(value)!r}"
        raise NotDispatched(f"element {index} has no settable value; use click or type_text")

    def _do_perform_secondary_action(self, app, state, args) -> str:
        index = args.get("element")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        wanted = str(args.get("action_name") or "").strip()
        if not wanted:
            raise BadArgument("action_name is required")
        actions = self._actions(acc)
        if not self._do_action(acc, wanted):
            raise ComputerUseError(f"element {index} does not expose {wanted!r}; its actions are: "
                                   + (", ".join(actions) or "none"))
        return f"performed {wanted} on {node.body()}"

    def _do_select_text(self, app, state, args) -> str:
        Atspi = _atspi()
        index = args.get("element")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        value = self._text(acc)
        if value is None:
            raise ComputerUseError(f"element {index} has no text to select in")
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        start = find_text(value, text, args.get("prefix") or "", args.get("suffix") or "")
        if start is None:
            raise ComputerUseError(f"{text!r} was not found in element {index} with that prefix and suffix")
        end = start + len(text)
        kind = str(args.get("selection_type") or "text")
        if kind not in ("text", "cursor_before", "cursor_after"):
            raise BadArgument("selection_type must be text, cursor_before or cursor_after")
        if kind == "text":
            done = (self._safe(lambda: Atspi.Text.set_selection(acc, 0, start, end), False)
                    or self._safe(lambda: Atspi.Text.add_selection(acc, start, end), False))
        else:
            done = self._safe(lambda: Atspi.Text.set_caret_offset(acc, start if kind == "cursor_before" else end), False)
        if not done:
            raise ComputerUseError("the app refused the selection")
        where = {"text": "selected", "cursor_before": "placed the cursor before", "cursor_after": "placed the cursor after"}[kind]
        return f"{where} {text!r} in {node.body()}"

    def _do_paste(self, app, state, args) -> str:
        text = args.get("text")
        if not isinstance(text, str) or not text:
            raise BadArgument("text is required")
        kind = str(args.get("format") or "text").lower()
        if kind not in ("text", "md", "markdown", "html"):
            raise BadArgument("format must be text, md or html")
        if kind != "text":
            raise ComputerUseError("On Linux paste inserts plain text through the element's editable text; "
                                   "use format=text.")
        target = None
        if args.get("element") is not None:
            target = self._element(state, args["element"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        if target is None or not self._insert(target, text):
            raise ComputerUseError("no editable text field has focus; focus one (focus or click) first")
        return f"pasted {len(text)} character(s) as text (inserted directly; the clipboard was not used)"

    def _do_focus(self, app, state, args) -> str:
        index = args.get("element")
        acc = self._element(state, index)
        node = self._node_at(state, index)
        self._refuse_secure(acc)
        if not self._has(acc, "FOCUSABLE") or not self._grab_focus(acc):
            raise ComputerUseError(f"element {index} cannot take keyboard focus; click it instead")
        return f"focused {node.body()}"

    def _do_copy(self, app, state, args) -> str:
        """The selected text of the element (or the focused one), read without the clipboard."""
        Atspi = _atspi()
        target = None
        if args.get("element") is not None:
            target = self._element(state, args["element"])
        target = target or self._live_focused(app, state)
        self._refuse_secure(target)
        if target is None or "Text" not in self._ifaces(target):
            raise ComputerUseError("no text element has focus; pass element of the text to copy from")
        ranges = []
        for i in range(self._safe(lambda: Atspi.Text.get_n_selections(target), 0) or 0):
            selected = self._safe(lambda i=i: Atspi.Text.get_selection(target, i))
            if selected is not None and selected.end_offset > selected.start_offset:
                ranges.append(self._safe(lambda s=selected: Atspi.Text.get_text(target, s.start_offset, s.end_offset), ""))
        text = "\n".join(r for r in ranges if r)
        if not text:
            raise ComputerUseError("nothing is selected; select text first (select_text, or press_key ctrl+a)")
        clipped = text if len(text) <= MAX_COPY_CHARS else text[:MAX_COPY_CHARS] + "\n…(truncated)"
        return Report(f"copied {len(text)} character(s) (read from the selection; the clipboard was not touched). "
                      f"Copied text:\n<<<\n{clipped}\n>>>")

    def _menu_bar(self, app: App):
        for window in self._accessible_windows(app):
            stack = [(window, 0)]
            while stack:
                acc, depth = stack.pop()
                if self._role(acc) == "menu bar":
                    return acc
                if depth < 6:
                    stack.extend((c, depth + 1) for c in self._children(acc))
        return None

    def _menu_children(self, item) -> list[Any]:
        return [c for c in self._children(item) if self._role(c) in _MENU_ITEMS]

    def _menu_line(self, item) -> str:
        parts = [self._name(item)]
        attrs = self._safe(lambda: item.get_attributes(), {}) or {}
        shortcut = attrs.get("keybinding") if isinstance(attrs, dict) else ""
        if not shortcut:
            Atspi = _atspi()
            if "Action" in self._ifaces(item):
                shortcut = self._safe(lambda: Atspi.Action.get_key_binding(item, 0), "") or ""
                shortcut = shortcut.split(";")[-1] if shortcut else ""
        if shortcut:
            parts.append(f"({shortcut})")
        if self._role(item) in ("check menu item", "radio menu item") and self._has(item, "CHECKED"):
            parts.append("[checked]")
        if not self._has(item, "ENABLED") and not self._has(item, "SENSITIVE"):
            parts.append("[disabled]")
        if self._menu_children(item):
            parts.append("▸")
        return " ".join(parts)

    def _do_menu(self, app, state, args) -> str:
        path = menu_path(args)
        bar = self._menu_bar(app)
        if bar is None:
            raise ComputerUseError(f"{app.name} has no menu bar (apps with a header bar keep their menu in a button; "
                                   "click it and read the state)")
        level = self._menu_children(bar)
        if not path:
            return Report("menu bar: " + " | ".join(t for t in (self._name(i) for i in level) if t)
                          + ". Pass menu_path to list a menu or press an item.")
        item, walked = None, []
        for step in path:
            wanted = menu_key(step)
            item = next((i for i in level if menu_key(self._name(i).replace("_", "")) == wanted), None)
            if item is None:
                where = " > ".join(walked) or "the menu bar"
                raise ComputerUseError(f"{step!r} is not in {where}; it has: "
                                       + " | ".join(t for t in (self._name(i) for i in level) if t))
            walked.append(self._name(item))
            level = self._menu_children(item)
            # GTK lists a submenu as a "menu" child holding the items.
            if len(level) == 1 and self._role(level[0]) == "menu" and not self._name(level[0]):
                level = self._menu_children(level[0])
        if level:
            return Report(" > ".join(walked) + ":\n" + "\n".join(f"- {self._menu_line(i)}" for i in level
                                                                  if self._name(i)))
        if not self._has(item, "ENABLED") and not self._has(item, "SENSITIVE"):
            raise ComputerUseError(" > ".join(walked) + " is disabled right now")
        if not any(self._do_action(item, name) for name in ("click", "activate", "press")):
            raise ComputerUseError(f"pressing {' > '.join(walked)} failed")
        return "chose " + " > ".join(walked)
