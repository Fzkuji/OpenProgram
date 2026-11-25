"""Reading and acting through the Accessibility API: attributes, actions, and the indexed tree."""
from __future__ import annotations

import threading
import time
from typing import Any

from ..common.base import StaleElement
from ..common.base import (BadArgument, ComputerUseError, NotAnswering, PermissionMissing, NotDispatched,
    check_cancelled as _check_cancelled)
from ..common.tree import Node, TRANSPARENT_ROLES, leave_out, rebind
from .frameworks import (ACTION_TIMEOUT, ANSWER_TIMEOUT, MAX_DEPTH, MAX_NODES, SCREEN_RECORDING_MISSING,
    UNANSWERED_MAX, WindowState, _ATTRS, _ERROR_TYPE, _Frameworks, _INVALID_ELEMENT, _NOT_ANSWERED, _SECURE,
    _SETTABLE_ROLES, _TOGGLE_ROLES, _log)
from . import frameworks


def _await(start):
    done, result = threading.Event(), []

    def callback(value, error):
        result.extend((value, error))
        done.set()

    start(callback)
    deadline = time.monotonic() + 8
    while not done.wait(0.02):
        _check_cancelled()
        if time.monotonic() >= deadline:
            raise ComputerUseError("Window capture timed out")
    if result[1] is not None or result[0] is None:
        # Without the grant every capture fails the same way; the model is told which grant, not the error code.
        preflight = getattr(_Frameworks.get()[2], "CGPreflightScreenCaptureAccess", None)
        if preflight is not None and not preflight():
            raise PermissionMissing(SCREEN_RECORDING_MISSING)
        raise ComputerUseError(f"Window capture failed: {result[1]}")
    return result[0]


class Accessibility:
    """Accessibility attributes and actions of elements, and a window's tree as indexed nodes."""

    def _attr(self, element, name):
        _, ax, _, _ = frameworks._frameworks_cached()
        code, value = ax.AXUIElementCopyAttributeValue(element, name, None)
        return value if code == 0 else None

    def _attrs(self, element, names):
        _, ax, _, _ = frameworks._frameworks_cached()
        code, values = ax.AXUIElementCopyMultipleAttributeValues(element, names, 0, None)
        if code != 0 or values is None:
            return {}
        out = {}
        for name, value in zip(names, values):
            if type(value).__name__ == "AXValueRef" and ax.AXValueGetType(value) == _ERROR_TYPE:
                continue
            out[name] = value
        return out

    def _actions(self, element) -> list[str]:
        _, ax, _, _ = frameworks._frameworks_cached()
        code, names = ax.AXUIElementCopyActionNames(element, None)
        return [str(n) for n in names or []] if code == 0 else []

    def _settable(self, element, name) -> bool:
        _, ax, _, _ = frameworks._frameworks_cached()
        code, value = ax.AXUIElementIsAttributeSettable(element, name, None)
        return code == 0 and bool(value)

    def _frame(self, element) -> tuple[float, float, float, float] | None:
        _, ax, _, _ = frameworks._frameworks_cached()
        values = self._attrs(element, ["AXPosition", "AXSize"])
        if "AXPosition" not in values or "AXSize" not in values:
            return None
        ok1, point = ax.AXValueGetValue(values["AXPosition"], ax.kAXValueCGPointType, None)
        ok2, size = ax.AXValueGetValue(values["AXSize"], ax.kAXValueCGSizeType, None)
        if not ok1 or not ok2:
            return None
        return float(point.x), float(point.y), float(size.width), float(size.height)

    def _set(self, element, name, value) -> int:
        _, ax, _, _ = frameworks._frameworks_cached()
        return self._taken(ax.AXUIElementSetAttributeValue, element, name, value)

    def _perform(self, element, action) -> int:
        _, ax, _, _ = frameworks._frameworks_cached()
        return self._taken(ax.AXUIElementPerformAction, element, action)

    def _taken(self, request, element, *args) -> int:
        """Send a request that changes the app (an action, a new value); 0 when the app took it.

        The call returns once the app has handled the request. A handler that
        outlasts the wait (a button that saves a file, a command that runs a
        dialog, a menu that stays open) makes it return kAXErrorCannotComplete,
        although the app received the request once and is carrying it out. That
        counts as taken: sending it again, or clicking instead, would do it
        twice. The same code at once is a request that could not be made.
        """
        _, ax, _, _ = frameworks._frameworks_cached()
        ax.AXUIElementSetMessagingTimeout(element, ACTION_TIMEOUT)
        started = time.monotonic()
        code = request(element, *args)
        if code == _NOT_ANSWERED and time.monotonic() - started >= ACTION_TIMEOUT * 0.9:
            return 0
        return code

    def _answers(self, pid: int) -> bool:
        """Whether the app answers Accessibility requests now: False only when one went unanswered."""
        _, ax, _, _ = frameworks._frameworks_cached()
        root = ax.AXUIElementCreateApplication(pid)
        ax.AXUIElementSetMessagingTimeout(root, ANSWER_TIMEOUT)
        started = time.monotonic()
        code, _ = ax.AXUIElementCopyAttributeValue(root, "AXRole", None)
        return not (code == _NOT_ANSWERED and time.monotonic() - started >= ANSWER_TIMEOUT * 0.9)

    def _await_answer(self, app) -> None:
        """Wait for ``app`` to answer again; NotAnswering once it has stayed silent for UNANSWERED_MAX seconds.

        A busy app answers no read (its windows then look closed) and keeps a
        step sent to it for later. Requests are handled in order, so the answer
        to this one comes after the app has finished what it was sent before,
        or while it shows the dialog or menu that step opened.
        """
        pid = int(app.processIdentifier())
        started = time.monotonic()
        while not self._answers(pid):
            _check_cancelled()
            waited = time.monotonic() - started
            if waited >= UNANSWERED_MAX:
                raise NotAnswering(str(app.localizedName() or f"pid {pid}"), waited)

    def _node(self, element, depth: int):
        values = self._attrs(element, _ATTRS)
        role = str(values.get("AXRole") or "AXUnknown")
        subrole = str(values.get("AXSubrole") or "")
        label = values.get("AXTitle") or values.get("AXDescription") or ""
        label = str(label) if isinstance(label, str) else ""
        help_text = values.get("AXHelp")
        if not label and isinstance(help_text, str):
            label = help_text
        raw_value = values.get("AXValue")
        value = ""
        flags: list[str] = []
        secure = role == _SECURE or subrole == _SECURE
        if secure:
            flags.append("secure")
        elif role in _TOGGLE_ROLES and (isinstance(raw_value, (int, float, bool))
                                        or str(raw_value).strip().lower() in ("0", "1", "true", "false")):
            on = raw_value if isinstance(raw_value, (int, float, bool)) else str(raw_value).strip().lower() in ("1", "true")
            flags.append("checked" if on else "unchecked")
        elif isinstance(raw_value, str):
            value = raw_value
        elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            value = f"{raw_value:g}" if isinstance(raw_value, float) else str(raw_value)
        if values.get("AXEnabled") is False:
            flags.append("disabled")
        if values.get("AXFocused") is True:
            flags.append("focused")
        if values.get("AXSelected") is True:
            flags.append("selected")
        if values.get("AXExpanded") is True:
            flags.append("expanded")
        if role in _SETTABLE_ROLES and not secure and self._settable(element, "AXValue"):
            flags.append("settable")
        placeholder = values.get("AXPlaceholderValue")
        node = Node(role=role, subrole=subrole, depth=depth, label=label, value=value,
                    placeholder=str(placeholder) if isinstance(placeholder, str) else "",
                    flags=flags, actions=self._actions(element))
        children = values.get("AXChildren") or []
        return node, list(children)

    def _walk(self, roots: list[tuple[Any, int]], nodes, elements, budget: list[int], *, menus_open_only=False,
              seen: set | None = None):
        stack = list(reversed(roots))
        # An element reached twice is a cycle (an app listing itself as its
        # window's child); reading it again would repeat it to MAX_DEPTH.
        seen = set() if seen is None else seen
        while stack:
            _check_cancelled()
            if budget[0] <= 0:
                return False
            element, depth = stack.pop()
            try:
                if element in seen:
                    continue
                seen.add(element)
            except TypeError:  # an unhashable element: walk it without the guard
                pass
            node, children = self._node(element, depth)
            transparent = (node.role in TRANSPARENT_ROLES and not node.label and not node.value
                           and not [a for a in node.actions if a not in ("AXScrollToVisible", "AXShowMenu")])
            child_depth = depth
            if not transparent:
                node.index = len(nodes)
                nodes.append(node)
                elements.append(element)
                budget[0] -= 1
                child_depth = depth + 1
            if child_depth > MAX_DEPTH:
                continue
            if menus_open_only and node.role == "AXMenuBarItem" and "selected" not in node.flags:
                continue
            stack.extend((child, child_depth) for child in reversed(children))
        return True

    def _read_tree(self, root, window):
        """The window, the menus and dialogs beside it, and the menu bar with only open menus
        expanded (the menu action reads closed ones), so every read covers the same ground."""
        nodes: list[Node] = []
        elements: list[Any] = []
        budget = [MAX_NODES]
        complete = True
        # The application element is never part of the tree; a child pointing back at it is a cycle.
        seen: set = {root}
        if window is not None:
            complete = self._walk([(window, 0)], nodes, elements, budget, seen=seen)
        # Menus and popovers opened by the app sit beside its windows.
        for child in self._attr(root, "AXChildren") or []:
            role = str(self._attr(child, "AXRole") or "")
            if role in ("AXMenu", "AXPopover") or (
                    role == "AXWindow" and child != window
                    and self._attr(child, "AXSubrole") in ("AXFloatingWindow", "AXSystemDialog", "AXDialog")):
                complete = self._walk([(child, 0)], nodes, elements, budget, seen=seen) and complete
        menubar = self._attr(root, "AXMenuBar")
        if menubar is not None and budget[0] > 0:
            complete = self._walk([(menubar, 0)], nodes, elements, budget, menus_open_only=True,
                                  seen=seen) and complete
        return nodes, elements, complete

    def _element(self, state: WindowState, index) -> Any:
        if index is None or isinstance(index, bool):
            raise BadArgument("element is required")
        try:
            index = int(index)
        except (TypeError, ValueError) as exc:
            raise BadArgument("element must be an integer from the last state") from exc
        element = state.elements.get(index)
        if element is None:
            raise StaleElement(f"element {index} is not in the last state of this window. "
                                   "Use an index from the latest tree (get_state, or find).")
        _, ax, _, _ = frameworks._frameworks()
        code, _ = ax.AXUIElementCopyAttributeValue(element, "AXRole", None)
        if code == _INVALID_ELEMENT:
            element = self._rebind(state, index)
            if element is None:
                raise StaleElement(f"element {index} no longer exists. Call get_state for a fresh tree.")
        return element

    def _rebind(self, state: WindowState, index: int):
        """The element now behind ``index`` when the app replaced the one read earlier (see :func:`tree.rebind`)."""
        try:
            _, ax, _, _ = frameworks._frameworks()
            root = ax.AXUIElementCreateApplication(state.pid)
            ax.AXUIElementSetMessagingTimeout(root, 3.0)
            windows = self._windows(root)
            if state.window_id:
                found = [w for w in windows if self._ax_window_id(w) == state.window_id]
            else:
                found = [w for w in windows if str(self._attr(w, "AXTitle") or "") == state.window_title]
            if len(found) != 1:
                return None
            nodes, elements, _ = self._read_tree(root, found[0])
            if state.left_out:
                nodes, elements, _ = leave_out(nodes, elements, state.left_out)
            return rebind(state, index, nodes, elements)
        except Exception:
            # The step then fails as it did before: the model reads the window again.
            _log.debug("re-reading a window for element %s failed pid=%s", index, state.pid, exc_info=True)
            return None

    def _node_at(self, state: WindowState, index) -> Node:
        return state.by_index[int(index)]

    def _center(self, element) -> tuple[float, float]:
        frame = self._frame(element)
        if frame is None or frame[2] <= 0 or frame[3] <= 0:
            raise NotDispatched("This element has no on-screen frame to point at.")
        return frame[0] + frame[2] / 2, frame[1] + frame[3] / 2

    def _focus(self, element) -> None:
        if self._attr(element, "AXFocused") is not True and self._settable(element, "AXFocused"):
            self._set(element, "AXFocused", True)
            time.sleep(0.08)

    def _focused_element(self, pid: int):
        _, ax, _, _ = frameworks._frameworks()
        return self._attr(ax.AXUIElementCreateApplication(pid), "AXFocusedUIElement")
