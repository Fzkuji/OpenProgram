"""The arguments each action of each target takes, checked before anything runs.

One tool covers every target, so its JSON schema leaves ``args`` open and the
actions live in the target guides. This table is the schema the guides
describe: an unknown action or argument fails at once with the action's
signature, instead of being dropped on the way to a backend. A few names
models carry over from Playwright or pyautogui are rewritten to ours.
"""
from __future__ import annotations

from difflib import get_close_matches

_POINTER = ("x", "y")

ACTION_ARGS: dict[str, dict[str, tuple[str, ...]]] = {
    "app": {
        "list_apps": (),
        "list_windows": (),
        "get_app_state": ("full", "include"),
        "zoom": ("x", "y", "width", "height"),
        "find": ("text", "role"),
        "click": ("element_index", *_POINTER, "mouse_button", "click_count", "key", "duration"),
        "type_text": ("text", "element_index"),
        "press_key": ("key", "element_index", "duration"),
        "set_value": ("element_index", "value"),
        "scroll": ("element_index", *_POINTER, "direction", "pages", "pixels"),
        "drag": (*_POINTER, "to_x", "to_y"),
        "hover": ("element_index", *_POINTER, "duration"),
        "wait": ("duration",),
        "perform_secondary_action": ("element_index", "action_name"),
        "select_text": ("element_index", "text", "prefix", "suffix", "selection_type"),
        "paste": ("text", "format", "element_index"),
        "copy": ("element_index",),
        "focus": ("element_index",),
        "menu": ("menu_path",),
        "show_window": (),
        "open_file": ("path",),
        "start_audio_recording": ("max_duration",),
        "stop_audio_recording": ("transcribe",),
    },
    "browser": {
        "tabs": ("all",),
        "open": ("url", "visible"),
        "read": ("changes_only",),
        "click": ("ref", "role", "name", "x", "y", "mouse_button", "click_count", "modifiers"),
        "type": ("ref", "role", "name", "text", "clear", "submit"),
        "paste": ("ref", "role", "name", "text", "format"),
        "select": ("ref", "role", "name", "value"),
        "press": ("key", "ref", "role", "name"),
        "hover": ("ref", "role", "name"),
        "scroll": ("ref", "role", "name", "direction", "amount"),
        "drag": ("ref", "role", "name", "to_ref", "to_role", "to_name"),
        "upload": ("ref", "role", "name", "path", "field"),
        "navigate": ("url",),
        "back": (),
        "forward": (),
        "reload": (),
        "wait": ("text", "seconds"),
        "screenshot": (),
        "console": ("pattern", "only_errors", "limit"),
        "network": ("pattern", "only_errors", "limit"),
        "evaluate": ("script", "ref", "role", "name"),
        "close": (),
        "panes": (),
        "start_video_recording": ("pane", "max_duration"),
        "stop_video_recording": ("discard",),
    },
    "vm": {
        "screenshot": (),
        "click": (*_POINTER, "mouse_button", "click_count"),
        "move": _POINTER,
        "drag": (*_POINTER, "to_x", "to_y"),
        "scroll": (*_POINTER, "direction", "clicks"),
        "type_text": ("text",),
        "press_key": ("key",),
        "wait": ("duration",),
    },
}

# Arguments that address the target rather than one action; any step may carry them.
SHARED_ARGS: dict[str, tuple[str, ...]] = {
    "app": ("app", "window", "window_id"),
    "browser": ("browser", "tab"),
    "vm": (),
}

ACTION_ALIASES: dict[str, dict[str, str]] = {
    "app": {"type": "type_text", "press": "press_key", "screenshot": "get_app_state"},
    "browser": {"select_option": "select", "fill": "type", "goto": "navigate", "press_key": "press",
                "go_back": "back", "go_forward": "forward", "snapshot": "read", "observe": "read",
                "list_pages": "tabs", "list_tabs": "tabs", "new_tab": "open", "take_screenshot": "screenshot",
                "type_text": "type", "console_messages": "console", "network_requests": "network",
                "start_recording": "start_video_recording", "stop_recording": "stop_video_recording",
                "list_panes": "panes"},
    "vm": {"type": "type_text", "press": "press_key", "hotkey": "press_key", "move_to": "move"},
}

# (action or "*", alias) -> our name. A list value keeps its first item.
ARG_ALIASES: dict[str, dict[tuple[str, str], str]] = {
    "app": {},
    "browser": {("select", "values"): "value", ("select", "option"): "value", ("select", "label"): "value",
                ("evaluate", "expression"): "script", ("evaluate", "function"): "script",
                ("evaluate", "code"): "script", ("type", "value"): "text", ("press", "keys"): "key",
                ("*", "page"): "tab", ("*", "tab_id"): "tab", ("wait", "duration"): "seconds",
                ("drag", "target_ref"): "to_ref"},
    "vm": {("press_key", "keys"): "key"},
}


def signature(target: str, action: str) -> str:
    names = ACTION_ARGS[target][action]
    return f"{action}({', '.join(names)})" if names else action


def _unknown_action(target: str, action: str) -> str:
    actions = ACTION_ARGS[target]
    near = get_close_matches(action, actions, n=1)
    hint = f" Did you mean {signature(target, near[0])}?" if near else ""
    return f"unknown {target} action {action!r}.{hint} Actions: {', '.join(actions)}"


def check_step(target: str, action: str, step: dict) -> tuple[str, dict, str]:
    """Return the action and arguments in our names, or an error naming what is wrong."""
    actions = ACTION_ARGS.get(target)
    if actions is None:
        return action, step, ""
    action = ACTION_ALIASES[target].get(action, action) if action not in actions else action
    if action not in actions:
        return action, step, _unknown_action(target, action)
    allowed = set(actions[action]) | set(SHARED_ARGS[target])
    renamed = dict(step)
    for key in list(step):
        if key in allowed:
            continue
        ours = ARG_ALIASES[target].get((action, key)) or ARG_ALIASES[target].get(("*", key))
        if ours and renamed.get(ours) in (None, ""):
            value = renamed.pop(key)
            renamed[ours] = value[0] if isinstance(value, list) and value else value
    unknown = sorted(key for key in renamed if key not in allowed and renamed[key] is not None)
    if unknown:
        return action, step, (f"{action} does not take {', '.join(unknown)}; it is "
                              f"{signature(target, action)}.")
    return action, renamed, ""


def check(target: str, steps: list[tuple[str, dict]], shared: dict) -> tuple[list[tuple[str, dict]], dict, str]:
    """Check every step and the arguments they share; the first problem is the error."""
    if target not in ACTION_ARGS:
        return steps, shared, ""
    checked = []
    for number, (action, step) in enumerate(steps, 1):
        # Shared args may hold an action's own arguments when there is one step.
        merged = {**shared, **step} if len(steps) == 1 else step
        name, renamed, error = check_step(target, action, merged)
        if error:
            return steps, shared, f"step {number}: {error}" if len(steps) > 1 else error
        if len(steps) == 1:
            shared = {key: value for key, value in renamed.items() if key in SHARED_ARGS[target]}
            renamed = {key: value for key, value in renamed.items() if key not in SHARED_ARGS[target]}
        checked.append((name, renamed))
    if len(steps) > 1:
        stray = sorted(key for key in shared if key not in SHARED_ARGS[target] and shared[key] is not None)
        if stray:
            return steps, shared, (f"args shared by several steps may only hold "
                                  f"{', '.join(SHARED_ARGS[target]) or 'nothing'}; move {', '.join(stray)} into the steps.")
    return checked, shared, ""


__all__ = ["ACTION_ARGS", "SHARED_ARGS", "check", "check_step", "signature"]
