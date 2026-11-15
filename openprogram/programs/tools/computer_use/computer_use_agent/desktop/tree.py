"""Render an Accessibility tree as indexed text, and diff two renderings.

Each element becomes one line: its index, role, label and state. The
model refers back to an element by that index. After an action the tool
returns only the lines that changed since the previous state of the same
window, the way Codex's ``get_app_state`` does, unless the change is so
large that the full tree is shorter to read.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import re

# Roles whose unnamed instances only group other elements. They are
# skipped and their children take their place in the tree.
TRANSPARENT_ROLES = frozenset({"AXGroup", "AXUnknown", "AXLayoutArea", "AXSplitGroup", "AXLayoutItem"})

# Actions every element of a role performs by being clicked. They are
# implied by ``click`` and left out of the secondary action list.
PRIMARY_ACTIONS = frozenset({"AXPress", "AXConfirm"})

MAX_TEXT = 160


def role_name(role: str, subrole: str = "") -> str:
    """``AXTextField`` -> ``text field``; a subrole is appended in parentheses."""
    def words(value: str) -> str:
        value = value[2:] if value.startswith("AX") else value
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value).lower() or "element"
    name = words(role or "AXUnknown")
    if subrole and subrole not in {"AXUnknown"}:
        sub = words(subrole)
        if sub != name:
            name = f"{name} ({sub})"
    return name


# Bidirectional marks some apps wrap numbers in; invisible, and they break text search.
_BIDI_MARKS = re.compile("[\u200e\u200f\u202a-\u202e\u2066-\u2069]")


def _clip(value: str, limit: int = MAX_TEXT) -> str:
    value = " ".join(_BIDI_MARKS.sub("", str(value)).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _quote(value: str) -> str:
    return '"' + _clip(value).replace('"', '\\"') + '"'


@dataclass
class Node:
    role: str
    depth: int
    subrole: str = ""
    label: str = ""
    value: str = ""
    placeholder: str = ""
    flags: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    index: int = -1

    def body(self) -> str:
        """The line without its index, which is what a diff compares."""
        parts = [role_name(self.role, self.subrole)]
        if self.label:
            parts.append(_quote(self.label))
        if self.value and self.value != self.label:
            parts.append("value=" + _quote(self.value))
        if self.placeholder and not self.value:
            parts.append("placeholder=" + _quote(self.placeholder))
        if self.flags:
            parts.append("[" + ", ".join(self.flags) + "]")
        secondary = [a for a in self.actions if a not in PRIMARY_ACTIONS]
        if secondary:
            parts.append("actions: " + ", ".join(secondary))
        return " ".join(parts)

    def line(self) -> str:
        return "  " * self.depth + f"{self.index} " + self.body()

    def key(self) -> str:
        return "  " * self.depth + self.body()


def render(nodes: list[Node]) -> str:
    return "\n".join(node.line() for node in nodes)


def signature(nodes: list[Node]) -> list[str]:
    """Index-free lines, the part of a state two states are compared on."""
    return [node.key() for node in nodes]


@dataclass
class Snapshot:
    """What the model was last shown of one window: each line and its index."""
    keys: list[str]
    indices: list[int]
    next_index: int


def snapshot(nodes: list[Node], next_index: int = 0) -> Snapshot:
    """``next_index`` is what :func:`number` returned, so freed indices are never reused."""
    return Snapshot(keys=signature(nodes), indices=[node.index for node in nodes],
                    next_index=max(next_index, max((node.index for node in nodes), default=-1) + 1))


_ROLE_END = re.compile(r' (?="|value=|placeholder=|\[|actions:)')
_LABEL = re.compile(r'"(?:[^"\\]|\\.)*"')


def _identity(key: str) -> tuple[int, str, str]:
    """Depth, role and label of a line: what an element keeps when its value or state changes."""
    stripped = key.lstrip(" ")
    parts = _ROLE_END.split(stripped, 1)
    label = _LABEL.match(parts[1]) if len(parts) == 2 else None
    return len(key) - len(stripped), parts[0], label.group(0) if label else ""


def _continued(before: list[str], after: list[str]) -> dict[int, int]:
    """Which of the lines ``before`` each line of ``after`` continues, within one changed block.

    An element with the same depth, role and label is the same element with
    a new value or state, wherever the block moved it: lines added above it
    do not take its place. What is left pairs in place when depth and role
    hold (a button whose title changed).
    """
    old, new = [_identity(key) for key in before], [_identity(key) for key in after]
    pairs: dict[int, int] = {}
    for op, a0, a1, b0, b1 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if op == "equal":
            pairs.update((b0 + offset, a0 + offset) for offset in range(b1 - b0))
        elif op == "replace":
            pairs.update((b0 + offset, a0 + offset) for offset in range(min(a1 - a0, b1 - b0))
                         if old[a0 + offset][:2] == new[b0 + offset][:2])
    return pairs


def number(nodes: list[Node], previous: Snapshot | None) -> int:
    """Give every node its index, keeping the indices the model already knows.

    An element that is unchanged since ``previous``, or changed in place
    (same depth and role, e.g. a new value), keeps its index; an element
    that is new gets the next unused one. So an index the model read
    earlier never silently points at a different element after an
    insertion above it. Without ``previous`` the nodes are numbered 0..n-1.
    Returns the next unused index.
    """
    if previous is None:
        for position, node in enumerate(nodes):
            node.index = position
        return len(nodes)
    current = signature(nodes)
    fresh = previous.next_index
    matcher = difflib.SequenceMatcher(a=previous.keys, b=current, autojunk=False)
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            kept = {offset: offset for offset in range(b1 - b0)}
        elif op == "replace":
            kept = _continued(previous.keys[a0:a1], current[b0:b1])
        else:
            kept = {}
        for offset, node in enumerate(nodes[b0:b1]):
            if offset in kept:
                node.index = previous.indices[a0 + kept[offset]]
            else:
                node.index = fresh
                fresh += 1
    return fresh


def _still(before: Node, after: Node) -> bool:
    """Whether a line read again is the control the model saw: it reads the same, or it has a name and keeps it."""
    if before.key() == after.key():
        return True
    named = bool(before.label or before.placeholder)
    return named and (before.depth, before.role, before.subrole, before.label, before.placeholder) == (
        after.depth, after.role, after.subrole, after.label, after.placeholder)


def rebind(state, index: int, nodes: list[Node], elements: list):
    """The live element behind ``index`` once an app has replaced the one read earlier, else ``None``.

    Apps, web pages above all, rebuild their accessibility elements while
    the window still shows the same controls, on their own or after another
    agent's step; the element kept from the last read is then dead although
    its line is still there. ``nodes`` and ``elements`` are the same window
    read again. Numbered against what the model last saw (:func:`number`),
    ``index`` names the same line, and it is taken only while that line
    reads the same or keeps its role and name, so the step lands on the
    control the model meant; an unnamed control that changed is left to a
    fresh read.

    ``state`` (a backend's ``WindowState``) gets the fresh elements; its
    ``snapshot``, the model's last view, is kept, so the state returned
    after the step still reports everything that changed since that view.
    """
    before = state.by_index.get(index)
    if before is None or state.snapshot is None:
        return None
    number(nodes, state.snapshot)
    after = next((node for node in nodes if node.index == index), None)
    if after is None or not _still(before, after):
        return None
    state.elements = {node.index: element for node, element in zip(nodes, elements)}
    state.nodes, state.by_index = nodes, {node.index: node for node in nodes}
    return state.elements[index]


def diff(previous: Snapshot | None, nodes: list[Node]) -> tuple[str, bool]:
    """Return ``(text, is_diff)`` for nodes already given indices by :func:`number`.

    Lines that are new or changed carry a ``+`` and their index; removed
    lines carry a ``-`` and no index, since they can no longer be addressed.
    Unchanged lines are left out: their indices are the ones already shown.
    """
    if previous is None:
        return render(nodes), False
    current = signature(nodes)
    matcher = difflib.SequenceMatcher(a=previous.keys, b=current, autojunk=False)
    out: list[str] = []
    changed = 0
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        if op == "equal":
            continue
        for line in previous.keys[a0:a1]:
            out.append("- " + line.strip())
            changed += 1
        for node in nodes[b0:b1]:
            out.append("+ " + node.line().strip())
            changed += 1
    if not out:
        return "(no changes in the Accessibility tree)", True
    if changed >= max(12, len(current) // 2):
        return render(nodes), False
    return "\n".join(out), True
