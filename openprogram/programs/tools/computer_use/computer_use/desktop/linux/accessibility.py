"""Reading elements through AT-SPI: roles, names, states, actions, text, and the indexed tree."""
from __future__ import annotations

from typing import Any

from ..common.base import BadArgument, NotDispatched, StaleElement, refuse_password_field, check_cancelled
from ..common.tree import Node, leave_out, rebind
from .x11 import (App, MAX_DEPTH, MAX_NODES, WindowState, _CLASS_NAME, _PRIMARY, _SECURE, _TOGGLE_ROLES,
    _TRANSPARENT, _atspi)


class Accessibility:
    """AT-SPI accessibles and a window's tree as indexed nodes."""

    @staticmethod
    def _safe(call, default=None):
        try:
            return call()
        except Exception:  # noqa: BLE001 - a vanished element raises GLib.Error
            return default

    def _role(self, acc) -> str:
        return self._safe(lambda: acc.get_role_name(), "") or ""

    def _name(self, acc) -> str:
        return self._safe(lambda: acc.get_name(), "") or ""

    def _label(self, acc, role: str) -> str:
        name = self._name(acc)
        if name and _CLASS_NAME.fullmatch(name) and (role in _TRANSPARENT or name.startswith(("Gtk", "Adw", "Hdy"))):
            return ""
        return name or self._safe(lambda: acc.get_description(), "") or ""

    def _children(self, acc) -> list[Any]:
        count = self._safe(lambda: acc.get_child_count(), 0) or 0
        out = []
        for i in range(min(count, 2000)):
            child = self._safe(lambda i=i: acc.get_child_at_index(i))
            if child is not None:
                out.append(child)
        return out

    def _has(self, acc, state_name: str) -> bool:
        Atspi = _atspi()
        state = getattr(Atspi.StateType, state_name)
        return bool(self._safe(lambda: acc.get_state_set().contains(state), False))

    def _ifaces(self, acc) -> set[str]:
        return set(self._safe(lambda: acc.get_interfaces(), []) or [])

    def _actions(self, acc) -> list[str]:
        Atspi = _atspi()
        if "Action" not in self._ifaces(acc):
            return []
        count = self._safe(lambda: Atspi.Action.get_n_actions(acc), 0) or 0
        return [self._safe(lambda i=i: Atspi.Action.get_action_name(acc, i), "") or "" for i in range(count)]

    def _do_action(self, acc, name: str) -> bool:
        Atspi = _atspi()
        for i, action in enumerate(self._actions(acc)):
            if action.lower() == name.lower():
                return bool(self._safe(lambda i=i: Atspi.Action.do_action(acc, i), False))
        return False

    def _extents(self, acc) -> tuple[float, float, float, float] | None:
        Atspi = _atspi()
        if "Component" not in self._ifaces(acc):
            return None
        rect = self._safe(lambda: Atspi.Component.get_extents(acc, Atspi.CoordType.SCREEN))
        if rect is None or rect.width <= 0 or rect.height <= 0:
            return None
        return float(rect.x), float(rect.y), float(rect.width), float(rect.height)

    def _text(self, acc, limit: int = -1) -> str | None:
        Atspi = _atspi()
        if "Text" not in self._ifaces(acc):
            return None
        count = self._safe(lambda: Atspi.Text.get_character_count(acc), 0) or 0
        end = count if limit < 0 else min(count, limit)
        return self._safe(lambda: Atspi.Text.get_text(acc, 0, end), "") or ""

    def _value(self, acc) -> float | None:
        Atspi = _atspi()
        if "Value" not in self._ifaces(acc):
            return None
        return self._safe(lambda: float(Atspi.Value.get_current_value(acc)))

    def _node(self, acc, depth: int) -> Node:
        role = self._role(acc) or "unknown"
        label = self._label(acc, role)
        flags: list[str] = []
        value = ""
        secure = role == _SECURE
        if secure:
            flags.append("secure")
        elif role in _TOGGLE_ROLES:
            flags.append("checked" if self._has(acc, "CHECKED") or self._has(acc, "PRESSED") else "unchecked")
        else:
            number_value = self._value(acc)
            if number_value is not None:
                value = f"{number_value:g}"
            elif role not in ("label", "push button", "menu item", "page tab", "link") or not label:
                text = self._text(acc, 400)
                if text and text != label:
                    value = text
        if not self._has(acc, "ENABLED") and not self._has(acc, "SENSITIVE"):
            flags.append("disabled")
        if self._has(acc, "FOCUSED"):
            flags.append("focused")
        if self._has(acc, "SELECTED"):
            flags.append("selected")
        if self._has(acc, "EXPANDED"):
            flags.append("expanded")
        if not secure and ("EditableText" in self._ifaces(acc) and self._has(acc, "EDITABLE")
                           or "Value" in self._ifaces(acc) and role not in ("progress bar", "level bar")):
            flags.append("settable")
        placeholder = ""
        attrs = self._safe(lambda: acc.get_attributes(), {}) or {}
        if isinstance(attrs, dict):
            placeholder = str(attrs.get("placeholder-text", ""))
        actions = [a for a in self._actions(acc) if a and a.lower() not in _PRIMARY]
        return Node(role=role, depth=depth, label=label, value=value, placeholder=placeholder,
                    flags=flags, actions=actions)

    def _read_tree(self, window) -> tuple[list[Node], list[Any], bool]:
        nodes: list[Node] = []
        elements: list[Any] = []
        if window is None:
            return nodes, elements, True
        stack = [(window, 0)]
        seen: set = set()
        # GTK 3 marks what is on screen SHOWING; GTK 4 sets only VISIBLE below the window.
        shown = "SHOWING" if any(self._has(c, "SHOWING") for c in self._children(window)) else "VISIBLE"
        while stack:
            check_cancelled()
            if len(nodes) >= MAX_NODES:
                return nodes, elements, False
            acc, depth = stack.pop()
            key = self._safe(lambda acc=acc: hash(acc), id(acc))
            if key in seen:
                continue
            seen.add(key)
            if depth and (not self._has(acc, shown) or self._name(acc).endswith("TooltipWindow")):
                continue
            node = self._node(acc, depth)
            transparent = (node.role in _TRANSPARENT and not node.label and not node.value and not node.actions)
            child_depth = depth
            if not transparent or not depth:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(acc)
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            # A combo box lists its popup items while closed; they are read when it opens.
            if node.role == "combo box" and not self._has(acc, "EXPANDED"):
                children = [c for c in self._children(acc) if self._role(c) != "menu"]
            else:
                children = self._children(acc)
            stack.extend((child, child_depth) for child in reversed(children))
        return nodes, elements, True

    def _element(self, state: WindowState, index):
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise BadArgument("element must be an integer from the last state") from exc
        acc = state.elements.get(index)
        if acc is None:
            raise StaleElement(f"element {index} is not in the last state; call get_state")
        if not self._role(acc):
            acc = self._rebind(state, index)
            if acc is None:
                raise StaleElement(f"element {index} no longer exists; call get_state for a fresh tree")
        return acc

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            app = next((app for app in self._running() if app.pid == state.pid), None)
            if app is None:
                return None
            if state.window_id:
                window, _ = self._pair(app, None, state.window_id)
            else:
                named = [w for w in self._accessible_windows(app) if self._name(w) == state.window_title]
                window = named[0] if len(named) == 1 else None
            if window is None:
                return None
            nodes, elements, _ = self._read_tree(window)
            if state.left_out:
                nodes, elements, _ = leave_out(nodes, elements, state.left_out)
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _center(self, acc) -> tuple[float, float]:
        frame = self._extents(acc)
        if frame is None:
            raise NotDispatched("this element has no position on screen; act on it by its actions instead")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _focused(self, state: WindowState | None):
        for acc in (state.elements.values() if state else ()):
            if self._has(acc, "FOCUSED"):
                return acc
        return None

    def _live_focused(self, app: App, state: WindowState | None):
        """The element holding keyboard focus in the app's window, read now."""
        acc, _ = self._pair(app, prefer_id=state.window_id if state else 0)
        if acc is None:
            return None
        stack = [acc]
        seen = 0
        while stack and seen < MAX_NODES:
            current = stack.pop()
            seen += 1
            if self._has(current, "FOCUSED"):
                return current
            if self._has(current, "SHOWING") or self._has(current, "VISIBLE") or current is acc:
                stack.extend(self._children(current))
        return None

    def _refuse_secure(self, acc) -> None:
        if acc is not None and self._role(acc) == _SECURE:
            refuse_password_field("typing into this password field")
