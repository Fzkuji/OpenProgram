"""One vocabulary for every target: the actions, their arguments and the names models carry over.

The same act has one action name and one set of argument names on every
target. A target belongs to a family, which adds the arguments that address
its subject (``app``, ``tab``, ``display``) and the actions only that kind of
surface has. A provider says which capabilities it has and which of its
family's extra actions it offers; this module checks a call against that
before anything runs:

* an action of another tool (``left_click``, ``snapshot``, ``fill``) is
  rewritten to ours, with the argument it implies, and an argument under
  another name (``ref``, ``element_index``, ``duration``) to ours;
* an action the target lacks answers ``NOT_SUPPORTED:`` with the step that
  works, an unknown one ``UNKNOWN_ACTION:`` with the nearest name, a wrong
  argument ``BAD_ARGUMENT:`` with the action's signature.

Adding a kind touches only its provider. A new family adds one entry to
``FAMILIES`` and one guide.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from difflib import get_close_matches
import math
from typing import Any, Callable, Iterable

from . import errors
from .keynames import chord_of, normalize_key

# The actions every surface has, with the arguments of each.
CORE: dict[str, tuple[str, ...]] = {
    "get_state": ("include", "full"),
    "click": ("element", "x", "y", "mouse_button", "click_count", "modifiers", "seconds"),
    "type_text": ("text", "element", "clear", "submit"),
    "press_key": ("key", "element", "seconds"),
    "scroll": ("element", "x", "y", "direction", "pages", "pixels"),
    "drag": ("element", "x", "y", "to_element", "to_x", "to_y", "seconds"),
    "hover": ("element", "x", "y", "seconds"),
    "wait": ("seconds", "text"),
    "find": ("text", "role"),
    "set_value": ("element", "value"),
}

# The capability a core action needs from its target, and what to do without it.
_ACTION_NEEDS: dict[str, tuple[str, str]] = {
    "click": ("pointer", "this target has no pointer"),
    "scroll": ("pointer", "this target has no pointer"),
    "drag": ("pointer", "this target has no pointer"),
    "hover": ("hover", "this target cannot rest the pointer without clicking"),
    "type_text": ("keyboard", "this target takes no keyboard input"),
    "press_key": ("keyboard", "this target takes no keyboard input"),
    "find": ("tree", "this target shows pixels only; read the screenshot of get_state"),
    "set_value": ("tree", "this target shows pixels only; click the field at x,y, then type_text"),
}
# What works instead of an extra action a target of its family can lack.
_INSTEAD: dict[str, str] = {
    "open_file": "open it from the application: get_state(app), then menu File > Open, or press_key ctrl+o",
}
# Arguments that point into an element tree.
_TREE_ARGUMENTS = ("element", "to_element")

# list actions and the kind of thing each lists (``Provider.list``).
LISTS: dict[str, str] = {"list_apps": "apps", "list_windows": "windows", "list_tabs": "tabs",
                         "list_displays": "displays", "list_panes": "panes"}

# Actions that only read: one that failed applied nothing.
READS = frozenset({"get_state", "find", "zoom", "wait", "read_console_messages", "read_network_requests", *LISTS})


@dataclass(frozen=True)
class Family:
    """One kind of surface: the arguments that address its subject and the actions only it has."""
    name: str
    shared: tuple[str, ...]
    extras: dict[str, tuple[str, ...]]
    # Arguments this family adds to core actions: (action or "*") -> names.
    more: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def arguments(self, action: str) -> tuple[str, ...]:
        """The arguments ``action`` takes in this family, shared ones excluded."""
        if action in self.extras:
            return self.extras[action]
        names = CORE[action] + self.more.get(action, ())
        if "element" in names:
            names += tuple(name for name in self.more.get("*", ()) if name not in names)
        return names


_PAGE_ELEMENT = ("element", "role", "name")

FAMILIES: dict[str, Family] = {
    "window": Family(
        name="window",
        shared=("app", "window", "window_id"),
        extras={
            "list_apps": (),
            "list_windows": (),
            "zoom": ("x", "y", "width", "height"),
            "focus": ("element",),
            "show_window": (),
            "menu": ("menu_path",),
            "paste": ("text", "format", "element"),
            "copy": ("element",),
            "select_text": ("element", "text", "prefix", "suffix", "selection_type"),
            "perform_secondary_action": ("element", "action_name"),
            "open_file": ("path",),
            "start_audio_recording": ("max_duration",),
            "stop_audio_recording": ("transcribe",),
        },
    ),
    "screen": Family(
        name="screen",
        shared=("display",),
        extras={
            "list_displays": (),
            "zoom": ("x", "y", "width", "height"),
            "paste": ("text",),
        },
    ),
    "browser": Family(
        name="browser",
        shared=("tab",),
        extras={
            "list_tabs": ("all",),
            "create_tab": ("url", "visible"),
            "close_tab": (),
            "navigate": ("url",),
            "back": (),
            "forward": (),
            "reload": (),
            "paste": (*_PAGE_ELEMENT, "text", "format"),
            "upload_file": (*_PAGE_ELEMENT, "path", "field"),
            "evaluate": ("script", *_PAGE_ELEMENT),
            "read_console_messages": ("pattern", "only_errors", "limit"),
            "read_network_requests": ("pattern", "only_errors", "limit"),
            "list_panes": (),
            "start_video_recording": ("pane", "max_duration"),
            "stop_video_recording": ("discard",),
        },
        # A page element is also found by its role and name.
        more={"*": ("role", "name"), "drag": ("to_role", "to_name")},
    ),
}

# Names carried over from other tools: (family or "*", name) -> (our action, the arguments it implies).
_ACTIONS: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {
    ("*", "screenshot"): ("get_state", {"include": "screenshot"}),
    ("*", "take_screenshot"): ("get_state", {"include": "screenshot"}),
    ("*", "get_app_state"): ("get_state", {}),
    ("*", "read"): ("get_state", {}),
    ("*", "snapshot"): ("get_state", {}),
    ("*", "read_page"): ("get_state", {}),
    ("*", "observe"): ("get_state", {}),
    ("*", "left_click"): ("click", {}),
    ("*", "tap"): ("click", {}),
    ("*", "double_click"): ("click", {"click_count": 2}),
    ("*", "triple_click"): ("click", {"click_count": 3}),
    ("*", "right_click"): ("click", {"mouse_button": "right"}),
    ("*", "middle_click"): ("click", {"mouse_button": "middle"}),
    ("*", "long_press"): ("click", {"seconds": 1}),
    ("*", "type"): ("type_text", {}),
    ("*", "write"): ("type_text", {}),
    ("*", "input_text"): ("type_text", {}),
    ("*", "press"): ("press_key", {}),
    ("*", "key"): ("press_key", {}),
    ("*", "keypress"): ("press_key", {}),
    ("*", "hotkey"): ("press_key", {}),
    ("*", "scroll_at"): ("scroll", {}),
    ("*", "left_click_drag"): ("drag", {}),
    ("*", "drag_and_drop"): ("drag", {}),
    ("*", "move"): ("hover", {}),
    ("*", "mouse_move"): ("hover", {}),
    ("*", "move_to"): ("hover", {}),
    ("*", "sleep"): ("wait", {}),
    ("*", "fill"): ("set_value", {}),
    ("*", "select"): ("set_value", {}),
    ("*", "select_option"): ("set_value", {}),
    ("window", "open"): ("get_state", {}),
    ("window", "open_app"): ("get_state", {}),
    ("window", "launch_app"): ("get_state", {}),
    ("browser", "tabs"): ("list_tabs", {}),
    ("browser", "list_pages"): ("list_tabs", {}),
    ("browser", "open"): ("create_tab", {}),
    ("browser", "new_tab"): ("create_tab", {}),
    ("browser", "new_page"): ("create_tab", {}),
    ("browser", "close"): ("close_tab", {}),
    ("browser", "close_page"): ("close_tab", {}),
    ("browser", "goto"): ("navigate", {}),
    ("browser", "navigate_page"): ("navigate", {}),
    ("browser", "go_back"): ("back", {}),
    ("browser", "go_forward"): ("forward", {}),
    ("browser", "upload"): ("upload_file", {}),
    ("browser", "console"): ("read_console_messages", {}),
    ("browser", "console_messages"): ("read_console_messages", {}),
    ("browser", "network"): ("read_network_requests", {}),
    ("browser", "network_requests"): ("read_network_requests", {}),
    ("browser", "panes"): ("list_panes", {}),
    ("browser", "start_recording"): ("start_video_recording", {}),
    ("browser", "stop_recording"): ("stop_video_recording", {}),
}

# Argument names carried over: (action or "*", name) -> ours.
_ARGUMENTS: dict[tuple[str, str], str] = {
    ("*", "element_index"): "element",
    ("*", "ref"): "element",
    ("*", "index"): "element",
    ("*", "uid"): "element",
    ("*", "element_id"): "element",
    ("*", "to_ref"): "to_element",
    ("*", "target_ref"): "to_element",
    ("*", "to_element_index"): "to_element",
    ("*", "duration"): "seconds",
    ("*", "button"): "mouse_button",
    ("*", "page"): "tab",
    ("*", "tab_id"): "tab",
    ("click", "key"): "modifiers",
    ("click", "text"): "modifiers",
    ("press_key", "keys"): "key",
    ("press_key", "text"): "key",
    ("scroll", "amount"): "pixels",
    ("scroll", "scroll_amount"): "pixels",
    ("scroll", "scroll_direction"): "direction",
    ("set_value", "values"): "value",
    ("set_value", "option"): "value",
    ("set_value", "label"): "value",
    ("set_value", "text"): "value",
    ("type_text", "value"): "text",
    ("evaluate", "expression"): "script",
    ("evaluate", "function"): "script",
    ("evaluate", "code"): "script",
    ("get_state", "changes_only"): "full",
}


# Screenshot pixels one wheel click scrolls (about three lines), for ``scroll_amount``, which counts clicks.
_WHEEL_CLICK_PIXELS = 100


def _wheel_pixels(clicks: Any) -> Any:
    """Wheel clicks as pixels; a value that is not a number is left for the provider to refuse."""
    try:
        return float(clicks) * _WHEEL_CLICK_PIXELS
    except (TypeError, ValueError):
        return clicks


def _point(value: Any) -> tuple[Any, Any] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return value[0], value[1]
    if isinstance(value, dict) and "x" in value and "y" in value:
        return value["x"], value["y"]
    return None


_MODIFIER_NAMES = frozenset({"ctrl", "alt", "shift", "super", "fn"})


def _only_modifiers(value: Any) -> bool:
    held = normalize_key(chord_of(value), modifiers_only=True)
    return bool(held) and set(held.split("+")) <= _MODIFIER_NAMES


def _corner(value: Any) -> int | float:
    """One corner coordinate of a region: a number, or a number written as text."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return value
    try:
        number = float(value) if isinstance(value, str) else math.nan
    except ValueError:
        number = math.nan
    if not math.isfinite(number):
        raise errors.BadArgument("region is [x0, y0, x1, y1] in screenshot pixels.")
    return number


def _carried_arguments(action: str, step: dict, allowed: set[str]) -> dict:
    """``step`` with the argument names of other tools rewritten to ours; nothing is dropped."""
    renamed = dict(step)
    # A point written as one value: ``coordinate=[x, y]``, and for a drag its two ends.
    start = _point(renamed.get("start_coordinate"))
    point = _point(renamed.get("coordinate"))
    if action == "drag" and start and point and "x" not in renamed and "to_x" not in renamed:
        renamed.pop("start_coordinate"), renamed.pop("coordinate")
        renamed["x"], renamed["y"], renamed["to_x"], renamed["to_y"] = *start, *point
    elif point and "x" not in renamed and "y" not in renamed and "x" in allowed:
        renamed.pop("coordinate")
        renamed["x"], renamed["y"] = point
    # A region written as its two corners: ``region=[x0, y0, x1, y1]``.
    region = renamed.get("region")
    if isinstance(region, (list, tuple)) and len(region) == 4 and "width" in allowed and "x" not in renamed:
        x0, y0, x1, y1 = (_corner(corner) for corner in region)
        renamed.pop("region")
        renamed.update(x=x0, y=y0, width=x1 - x0, height=y1 - y0)
    for key in list(renamed):
        if key in allowed:
            continue
        ours = _ARGUMENTS.get((action, key)) or _ARGUMENTS.get(("*", key))
        if not ours or ours not in allowed or renamed.get(ours) not in (None, ""):
            continue
        if ours == "modifiers" and chord_of(renamed[key]) and not _only_modifiers(renamed[key]):
            # Other tools write held keys as click's text; any other text is not ours to read as keys.
            continue
        value = renamed.pop(key)
        if key == "changes_only":
            value = not value
        elif key == "scroll_amount":
            value = _wheel_pixels(value)
        renamed[ours] = value
    if action == "press_key" and isinstance(renamed.get("key"), (list, tuple)):
        # Several keys of one press are a chord, never one key with the rest dropped.
        renamed["key"] = chord_of(renamed["key"])
    if action == "set_value" and isinstance(renamed.get("value"), (list, tuple)) and len(renamed["value"]) == 1:
        renamed["value"] = renamed["value"][0]
    return renamed


@dataclass(frozen=True)
class Surface:
    """What a call is checked against: one target's family, capabilities and extra actions."""
    target: str
    family: str
    capabilities: frozenset[str]
    extras: tuple[str, ...] = ()
    # action -> other targets of the same machine that have it, for the answer to an unknown action.
    elsewhere: Callable[[str], Iterable[str]] | None = None

    @property
    def table(self) -> Family:
        return FAMILIES[self.family]

    def actions(self) -> tuple[str, ...]:
        """The actions this target takes, core ones first."""
        core = tuple(name for name in CORE if _need(name) is None or _need(name)[0] in self.capabilities)
        return core + tuple(name for name in self.table.extras if name in self.extras)

    def arguments(self, action: str) -> tuple[str, ...]:
        names = self.table.arguments(action)
        if "tree" not in self.capabilities:
            names = tuple(name for name in names if name not in _TREE_ARGUMENTS and name not in ("role", "name",
                                                                                             "to_role", "to_name"))
        return names

    def signature(self, action: str) -> str:
        """The action with every argument it takes: its own, then the family's shared ones."""
        own = self.arguments(action)
        names = own + tuple(name for name in self.table.shared if name not in own)
        return f"{action}({', '.join(names)})" if names else action


def _need(action: str) -> tuple[str, str] | None:
    return _ACTION_NEEDS.get(action)


def canonical(family: str, action: str) -> tuple[str, dict[str, Any]]:
    """``action`` in our name, with the arguments a carried-over name implies."""
    table = FAMILIES[family]
    if action in CORE or action in table.extras:
        return action, {}
    found = _ACTIONS.get((family, action)) or _ACTIONS.get(("*", action))
    return (found[0], dict(found[1])) if found else (action, {})


# Generic words each family rewrites to an operation of its own; another family's is a different operation.
_LOCAL = frozenset({"open", "close"})


def counterpart(family: str, action: str) -> str:
    """The action of ``family`` that ``action``, written for another target, names; a generic word names none."""
    return action if action in _LOCAL else canonical(family, action)[0]


def carried(family: str) -> tuple[str, ...]:
    """The names of other tools this family rewrites to its own actions."""
    table = FAMILIES[family]
    return tuple(name for (owner, name) in _ACTIONS
                 if owner in ("*", family) and name not in CORE and name not in table.extras)


def _unknown(surface: Surface, action: str) -> errors.CallError:
    table = surface.table
    known = (*CORE, *table.extras)
    others = sorted(surface.elsewhere(action)) if surface.elsewhere else []
    if action in known:
        # The family has it; this target does not. A target of the same machine that has it is the step that works.
        need = _need(action)
        why = need[1] if need else f"{surface.target} does not have {action}" + (
            f"; {_INSTEAD[action]}" if action in _INSTEAD else "")
        there = f" {', '.join(others)} {'has' if len(others) == 1 else 'have'} it." if others else ""
        return errors.NotSupported(f"{action} is not available on {surface.target}: {why}.{there} "
                                   f"Actions here: {', '.join(surface.actions())}.")
    if others:
        return errors.UnknownAction(f"{surface.target} has no action {action!r}; it is an action of "
                                    f"{', '.join(others)}. Actions here: {', '.join(surface.actions())}.")
    near = get_close_matches(action, surface.actions(), n=1)
    hint = f" Did you mean {surface.signature(near[0])}?" if near else ""
    return errors.UnknownAction(f"{surface.target} has no action {action!r}.{hint} "
                                f"Actions here: {', '.join(surface.actions())}.")


def check_step(surface: Surface, action: str, step: dict) -> tuple[str, dict]:
    """One step in our names, or the coded error that says what is wrong with it."""
    name, implied = canonical(surface.family, str(action or "").strip())
    if name not in surface.actions():
        raise _unknown(surface, name)
    table = surface.table
    allowed = set(table.arguments(name)) | set(table.shared)
    renamed = _carried_arguments(name, {key: value for key, value in step.items() if value is not None}, allowed)
    for key, value in implied.items():
        renamed.setdefault(key, value)
    unknown = sorted(key for key in renamed if key not in allowed)
    if unknown:
        raise errors.BadArgument(f"{name} does not take {', '.join(unknown)}; it is {surface.signature(name)}.")
    if "tree" not in surface.capabilities:
        pointed = sorted(key for key in renamed if key in _TREE_ARGUMENTS or key in ("role", "name"))
        if pointed:
            raise errors.NotSupported(f"{surface.target} shows pixels only, so {name} takes no {', '.join(pointed)}: "
                                      "point by x,y of the last screenshot.")
        if name == "get_state" and renamed.get("include") == "tree":
            raise errors.NotSupported(f"{surface.target} shows pixels only: get_state returns a screenshot.")
        if name == "wait" and renamed.get("text"):
            raise errors.NotSupported(f"{surface.target} shows pixels only, so wait cannot look for text: "
                                      "wait(seconds), then read the screenshot.")
    if name == "press_key" and renamed.get("key") not in (None, ""):
        renamed["key"] = normalize_key(str(renamed["key"]))
    if name == "click" and renamed.get("modifiers"):
        renamed["modifiers"] = normalize_key(chord_of(renamed["modifiers"]), modifiers_only=True)
    return name, renamed


def check(surface: Surface, steps: list[tuple[str, dict]], shared: dict, *,
          listed: bool = False) -> tuple[list[tuple[str, dict]], dict]:
    """Every step of a call and the arguments they share, in our names.

    ``shared`` addresses the subject. When the call sent ``action`` +
    ``args`` (``listed`` False), it also holds the one step's own arguments;
    with a list of ``actions`` it holds only what the steps share, whatever
    the list's length. Raises the coded error of the first problem.
    """
    table = surface.table
    shared = {key: value for key, value in shared.items() if value is not None}
    for key in list(shared):
        ours = _ARGUMENTS.get(("*", key))
        if key not in table.shared and ours in table.shared and ours not in shared:
            shared[ours] = shared.pop(key)
    if listed or len(steps) != 1:
        stray = sorted(key for key in shared if key not in table.shared)
        if stray:
            where = "args beside a list of actions" if steps else "args without an action"
            raise errors.BadArgument(f"{where} may only hold {', '.join(table.shared) or 'nothing'}; "
                                     f"move {', '.join(stray)} into the steps.")
    checked = []
    for number, (action, step) in enumerate(steps, 1):
        merged = {**shared, **step} if len(steps) == 1 else step
        try:
            name, renamed = check_step(surface, action, merged)
        except errors.CallError as exc:
            if len(steps) > 1:
                raise type(exc)(f"step {number}: {exc}") from None
            raise
        if len(steps) == 1:
            shared = {key: value for key, value in renamed.items() if key in table.shared}
            renamed = {key: value for key, value in renamed.items() if key not in table.shared}
        checked.append((name, renamed))
    return checked, shared


__all__ = ["CORE", "FAMILIES", "Family", "LISTS", "READS", "Surface", "canonical", "carried", "check", "check_step",
           "counterpart"]
