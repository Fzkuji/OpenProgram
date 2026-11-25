"""Reading elements through UI Automation: roles, names, patterns, and the indexed tree."""
from __future__ import annotations

from typing import Any

from ..common.base import BadArgument, NotDispatched, StaleElement, refuse_password_field, check_cancelled
from ..common.tree import Node, leave_out, rebind
from .win32 import (CONTROL_TYPES, EXPAND_COLLAPSE, INVOKE, LEGACY, MAX_DEPTH, MAX_NODES, RANGE_VALUE, SCROLL,
    SELECTION_ITEM, TEXT, TOGGLE, VALUE, WINDOW_PATTERN, WindowState, _THREAD, _TRANSPARENT)
from . import win32


class Automation:
    """UI Automation elements and a window's tree as indexed nodes."""

    @staticmethod
    def _safe(call, default=None):
        try:
            return call()
        except Exception:  # noqa: BLE001 - a vanished element raises COMError
            return default

    def _role(self, el) -> str:
        return CONTROL_TYPES.get(self._safe(lambda: int(el.CurrentControlType), 0), "") or \
            (self._safe(lambda: str(el.CurrentLocalizedControlType), "") or "custom")

    def _name(self, el) -> str:
        return self._safe(lambda: str(el.CurrentName or ""), "") or ""

    def _pattern(self, el, pattern_id: int):
        _, module = win32._uia()
        interfaces = {
            INVOKE: module.IUIAutomationInvokePattern, VALUE: module.IUIAutomationValuePattern,
            RANGE_VALUE: module.IUIAutomationRangeValuePattern, SCROLL: module.IUIAutomationScrollPattern,
            EXPAND_COLLAPSE: module.IUIAutomationExpandCollapsePattern,
            SELECTION_ITEM: module.IUIAutomationSelectionItemPattern, TEXT: module.IUIAutomationTextPattern,
            TOGGLE: module.IUIAutomationTogglePattern, LEGACY: module.IUIAutomationLegacyIAccessiblePattern,
            WINDOW_PATTERN: module.IUIAutomationWindowPattern,
        }
        raw = self._safe(lambda: el.GetCurrentPattern(pattern_id))
        if not raw:
            return None
        return self._safe(lambda: raw.QueryInterface(interfaces[pattern_id]))

    def _children(self, el) -> list[Any]:
        win32._uia()
        walker = _THREAD.walker
        out = []
        child = self._safe(lambda: walker.GetFirstChildElement(el))
        while child and len(out) < 2000:
            out.append(child)
            child = self._safe(lambda c=child: walker.GetNextSiblingElement(c))
        return out

    def _bounds(self, el) -> tuple[float, float, float, float] | None:
        rect = self._safe(lambda: el.CurrentBoundingRectangle)
        if rect is None or rect.right <= rect.left or rect.bottom <= rect.top:
            return None
        return float(rect.left), float(rect.top), float(rect.right - rect.left), float(rect.bottom - rect.top)

    def _value_text(self, el) -> str | None:
        pattern = self._pattern(el, VALUE)
        if pattern is not None:
            return self._safe(lambda: str(pattern.CurrentValue or ""), "")
        text = self._pattern(el, TEXT)
        if text is not None:
            return self._safe(lambda: str(text.DocumentRange.GetText(-1) or ""), "")
        return None

    def _actions(self, el) -> list[str]:
        out = []
        for pattern_id, name in ((EXPAND_COLLAPSE, "expand"), (EXPAND_COLLAPSE, "collapse"),
                                 (SCROLL, "scroll"), (SELECTION_ITEM, "select")):
            if self._pattern(el, pattern_id) is not None and name not in out:
                out.append(name)
        legacy = self._pattern(el, LEGACY)
        default = self._safe(lambda: str(legacy.CurrentDefaultAction or ""), "") if legacy is not None else ""
        if default and default.lower() not in ("press", "click", "invoke", "jump"):
            out.append(default)
        return out

    def _node(self, el, depth: int) -> Node:
        role = self._role(el)
        label = self._name(el)
        flags: list[str] = []
        value = ""
        secure = bool(self._safe(lambda: el.CurrentIsPassword, False))
        if secure:
            flags.append("secure")
        toggle = self._pattern(el, TOGGLE)
        if toggle is not None and not secure:
            state = self._safe(lambda: int(toggle.CurrentToggleState), 0)
            flags.append({1: "checked", 2: "mixed"}.get(state, "unchecked"))
        elif not secure:
            ranged = self._pattern(el, RANGE_VALUE)
            if ranged is not None:
                current = self._safe(lambda: float(ranged.CurrentValue))
                value = f"{current:g}" if current is not None else ""
            else:
                text = self._value_text(el)
                if text and text != label:
                    value = text[:400]
        if not self._safe(lambda: el.CurrentIsEnabled, True):
            flags.append("disabled")
        if self._safe(lambda: el.CurrentHasKeyboardFocus, False):
            flags.append("focused")
        item = self._pattern(el, SELECTION_ITEM)
        if item is not None and self._safe(lambda: item.CurrentIsSelected, False):
            flags.append("selected")
        expand = self._pattern(el, EXPAND_COLLAPSE)
        if expand is not None and self._safe(lambda: int(expand.CurrentExpandCollapseState), 0) == 1:
            flags.append("expanded")
        settable = self._pattern(el, VALUE)
        if not secure and (settable is not None and not self._safe(lambda: settable.CurrentIsReadOnly, True)
                           or self._pattern(el, RANGE_VALUE) is not None):
            flags.append("settable")
        placeholder = self._safe(lambda: str(el.CurrentHelpText or ""), "") if role == "edit" and not value else ""
        return Node(role=role, depth=depth, label=label, value=value, placeholder=placeholder or "",
                    flags=flags, actions=self._actions(el))

    def _read_tree(self, root) -> tuple[list[Node], list[Any], bool]:
        nodes: list[Node] = []
        elements: list[Any] = []
        if root is None:
            return nodes, elements, True
        stack = [(root, 0)]
        while stack:
            check_cancelled()
            if len(nodes) >= MAX_NODES:
                return nodes, elements, False
            el, depth = stack.pop()
            if depth and self._safe(lambda el=el: el.CurrentIsOffscreen, False) and self._role(el) != "menu item":
                continue
            node = self._node(el, depth)
            transparent = node.role in _TRANSPARENT and not node.label and not node.value and not node.actions
            child_depth = depth
            if not transparent or not depth:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(el)
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            stack.extend((child, child_depth) for child in reversed(self._children(el)))
        return nodes, elements, True

    def _element(self, state: WindowState, index):
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise BadArgument("element must be an integer from the last state") from exc
        el = state.elements.get(index)
        if el is None:
            raise StaleElement(f"element {index} is not in the last state; call get_state")
        if self._safe(lambda: el.CurrentControlType) is None:
            el = self._rebind(state, index)
            if el is None:
                raise StaleElement(f"element {index} no longer exists; call get_state for a fresh tree")
        return el

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            if not state.window_id:
                return None
            automation, _ = win32._uia()
            root = self._safe(lambda: automation.ElementFromHandle(state.window_id))
            if root is None:
                return None
            nodes, elements, _ = self._read_tree(root)
            if state.left_out:
                nodes, elements, _ = leave_out(nodes, elements, state.left_out)
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _center(self, el) -> tuple[float, float]:
        frame = self._bounds(el)
        if frame is None:
            raise NotDispatched("this element has no position on screen; act on it by its patterns instead")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _focused(self, state: WindowState | None):
        automation, _ = win32._uia()
        el = self._safe(lambda: automation.GetFocusedElement())
        if el is not None and state is not None and self._safe(lambda: int(el.CurrentProcessId), 0) != state.pid:
            return None
        return el

    def _refuse_secure(self, el) -> None:
        if el is not None and self._safe(lambda: el.CurrentIsPassword, False):
            refuse_password_field("typing into this password field")

    def _scrollable(self, el):
        win32._uia()
        current = el
        for _ in range(12):
            if current is None:
                return None
            pattern = self._pattern(current, SCROLL)
            if pattern is not None:
                return pattern
            current = self._safe(lambda c=current: _THREAD.walker.GetParentElement(c))
        return None

    def _walk(self, root, depth: int) -> list[Any]:
        out, stack = [], [(root, 0)] if root is not None else []
        while stack:
            el, d = stack.pop()
            out.append(el)
            if d < depth:
                stack.extend((c, d + 1) for c in self._children(el))
        return out
