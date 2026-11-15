"""Target ``app``: application windows, through ``openprogram/programs/tools/computer_use/computer_use_agent/desktop``.

The model reads an app with ``get_app_state`` (screenshot plus an indexed
Accessibility tree), acts by element index, screenshot pixel or a
description, and gets the changed part of the tree back after every action.
Everything happens in the background: the user's frontmost app, key window
and cursor are not touched. Approval is per application.
"""
from __future__ import annotations

from contextlib import contextmanager
import itertools
from pathlib import Path
import platform
import threading
import time
from typing import Any

from openprogram.programs._host.execution import ToolReturn

NAME = "app"

# How long a call waits for another agent's call on the same application.
APP_WAIT_SECONDS = 60.0

# Actions that change the app and are followed by its new state.
ACTING_ACTIONS = (
    "click", "type_text", "press_key", "set_value", "scroll", "drag", "perform_secondary_action",
    "select_text", "paste", "focus", "copy", "menu", "show_window", "hover", "wait", "open_file",
)
AUDIO_ACTIONS = ("start_audio_recording", "stop_audio_recording")
READING_ACTIONS = ("list_apps", "list_windows", "get_app_state", "zoom", "find")
ACTIONS = (*READING_ACTIONS, *ACTING_ACTIONS, *AUDIO_ACTIONS)
READ_ONLY_ACTIONS = frozenset({"list_apps"})

_GUIDE = (Path(__file__).parent.parent / "guides" / "app.md").read_text(encoding="utf-8")

# macOS Spaces, Open With and the out-of-process file dialog have no counterpart on the other platforms.
_SPACES_LINE = ("- A window on another Space takes clicks and element actions but not keys: there type_text and paste "
                "insert text into the focused field, and press_key fails.\n")
_OPEN_FILE_LINE = (
    "- open_file(app, path): open a document (absolute path) in that app the way Open With does, without raising it; "
    "the next state is of its window. Use it instead of File > Open or cmd+O: the system Open and Save dialogs take "
    "clicks and set_value but not keys. When a state says a file dialog is open, call open_file: it cancels the "
    "dialog first.\n")

# What differs on Linux: GTK and Qt take pointer and key input only while
# focused, and there is no system clipboard format conversion to borrow.
_LINUX_GUIDE = (
    ("Read and operate macOS application windows in the background: the user keeps the keyboard, mouse and front app, "
     "and nothing is raised or activated.",
     "Read and operate Linux (X11) application windows. Element actions run in the background through AT-SPI; a click "
     "or drag at x,y, a scroll at x,y and press_key bring the window forward for that moment and then give focus and "
     "the pointer back, so prefer element_index."),
    ("When an app exposes little to Accessibility", "When an app exposes little to accessibility (or nothing, "
     "e.g. without AT-SPI)"),
    ('super/cmd is Command. Keys go to that app only, so system-wide shortcuts (Spotlight, Mission Control) cannot be '
     'pressed.', 'super/cmd is the Super key. Return on a focused field runs in the background; other keys bring the '
     'window forward for the moment of the key.'),
    ("rest the pointer there (the real cursor does not move) to open tooltips and hover menus; apps that ignore the "
     "pointer while in the background show nothing.", "move the real pointer there and rest, to open tooltips and "
     "hover menus; the pointer stays there."),
    ("un-minimize a window or unhide the app, still in the background.", "un-minimize a window."),
    ("through the clipboard, which is restored afterwards; md for formatted text in rich editors.",
     "inserted straight into the focused field without the clipboard; plain text only (format=text)."),
    ("the app's own Copy on the current selection; returns the copied text and restores the clipboard.",
     "returns the selected text of the element (or the focused one); the clipboard is not touched."),
    (_SPACES_LINE, ""),
    (_OPEN_FILE_LINE, ""),
)
# What differs on Windows: shortcuts read the real keyboard state, and some
# windows (UWP frames, Chromium) take raw input instead of window messages.
_WINDOWS_GUIDE = (
    ("Read and operate macOS application windows in the background: the user keeps the keyboard, mouse and front app, "
     "and nothing is raised or activated.",
     "Read and operate Windows application windows. Element actions run in the background through UI Automation, and "
     "clicks, scrolls and plain keys at a window are posted to it without raising it. A key with modifiers (ctrl+s), "
     "and pointer input to windows that take only real input (Store apps, Chromium), bring the window forward for "
     "that moment and then give focus and the cursor back, so prefer element_index."),
    ("When an app exposes little to Accessibility", "When an app exposes little to UI Automation"),
    ("super/cmd is Command. Keys go to that app only, so system-wide shortcuts (Spotlight, Mission Control) cannot be "
     "pressed.", "super/cmd is the Windows key; ctrl is Control. Use the Windows shortcuts (ctrl+c, not super+c)."),
    ("one of the element's listed actions (AXShowMenu, AXIncrement …).",
     "one of the element's listed actions (expand, collapse, select, or its default action)."),
    ("un-minimize a window or unhide the app, still in the background.",
     "un-minimize a window without activating it."),
    ("through the clipboard, which is restored afterwards; md for formatted text in rich editors.",
     "through the clipboard, whose text is restored afterwards; md for formatted text in rich editors."),
    ("the app's own Copy on the current selection; returns the copied text and restores the clipboard.",
     "returns the selected text of the element (or the focused one); the clipboard is not touched."),
    ("list_windows(app): window ids, titles, minimized / hidden / other-Space state.",
     "list_windows(app): window ids, titles, focused and minimized state."),
    (_SPACES_LINE, ""),
    (_OPEN_FILE_LINE, ""),
)


def guide(system: str | None = None) -> str:
    """The app guide for this host's platform."""
    text = _GUIDE + (
        "\nAn actions batch accepts input actions and an optional final get_app_state. "
        "Use a separate get_app_state or find call when you need another observation. "
        "Re-find elements after opening a result or changing the view. For long lists, "
        "read small groups and return only relevant text instead of printing entire trees. "
        "After submitting a search, verify that its results finished updating before reading them."
    )
    if (system or platform.system()) == "Darwin":
        text += (
            "\nPrefer accessibility element clicks. Coordinate clicks can briefly change window stacking. "
            "If a window is on another Space, use an accessible Search or Submit button; "
            "inserting a newline into a field does not submit it."
        )
    pairs = {"Linux": _LINUX_GUIDE, "Windows": _WINDOWS_GUIDE}.get(system or platform.system(), ())
    for mac, other in pairs:
        text = text.replace(mac, other)
    return text


def available() -> bool:
    """macOS with the PyObjC frameworks, Linux on X11 with AT-SPI, or Windows with UI Automation."""
    if platform.system() not in ("Darwin", "Linux", "Windows"):
        return False
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends import support
    return bool(support()["available"])


def normalized_app(app: Any) -> str:
    """The key an approval rule is written for: the app as the model named it, lower-cased."""
    return " ".join(str(app or "").split()).lower()


def approval(steps: list[tuple[str, dict]], args: dict) -> str | bool:
    """Every action except the read-only listing asks once per application."""
    if steps and all(action in READ_ONLY_ACTIONS for action, _ in steps):
        return False
    return f"computer_use_agent controls {normalized_app(args.get('app')) or 'an application'}"


def _images(observation) -> list[bytes]:
    return [observation.image] if observation.image else []


# One agent acts on an application at a time. Agents of a conversation (the
# main turn, the agents it starts) each keep their own reading of a window
# (see tool.operator), but the window is one: a call (an action, a list of
# actions, a read) holds its application until it returns, so two lists never
# interleave, and a result says when another agent acted in between.
_registry = threading.Lock()
_locks: dict[str, threading.Lock] = {}
# application -> (sequence, agent) of the acting calls on it, newest last
_acted: dict[str, list[tuple[int, str]]] = {}
# (application, agent) -> the newest sequence that agent has seen the result of
_seen: dict[tuple[str, str], int] = {}
_sequence = itertools.count(1)
_KEPT_WRITES = 64
_MAX_SEEN = 4096


def _app_key(computer, app: str) -> str:
    """The application however the model named it: the backend's identity for it, else the name."""
    key = ""
    resolve = getattr(computer, "app_key", None)
    if resolve is not None:
        try:
            key = str(resolve(app) or "")
        except Exception:
            key = ""
    return key.lower() or normalized_app(app)


class _Turn:
    """One call's hold on an application: what it did there and whether it saw the result."""

    def __init__(self, app: str, others: int) -> None:
        self.app = app
        self.others = others    # acting calls of other agents since this agent last saw the application
        self.acted = False      # input of this call may have reached the application
        self.looked = False     # this call returns the application's state

    def state(self, text: str) -> str:
        """A result that carries the application's state, led by what other agents did meanwhile."""
        self.looked = True
        if not self.others:
            return text
        return (f"note: another agent acted on {self.app} {self.others} time(s) since you last looked; what it "
                f"changed is in this result.\n{text}")


@contextmanager
def _held(computer, app: str, agent: str):
    """Hold ``app`` for one call of ``agent``."""
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import NotDispatched, check_cancelled

    key = _app_key(computer, app)
    with _registry:
        lock = _locks.setdefault(key, threading.Lock())
    deadline = time.monotonic() + APP_WAIT_SECONDS
    while not lock.acquire(timeout=0.25):
        check_cancelled()
        if time.monotonic() >= deadline:
            raise NotDispatched(f"another agent has been operating {app} for {APP_WAIT_SECONDS:g}s; nothing was "
                                "sent. Try again when it is done.")
    turn = _Turn(app, 0)
    try:
        with _registry:
            seen = _seen.get((key, agent), 0)
            turn.others = sum(1 for sequence, who in _acted.get(key, ()) if who != agent and sequence > seen)
        yield turn
    finally:
        with _registry:
            writes = _acted.setdefault(key, [])
            if turn.acted:
                writes.append((next(_sequence), agent))
                del writes[:-_KEPT_WRITES]
            if turn.looked:
                if len(_seen) >= _MAX_SEEN:
                    _seen.clear()
                _seen[(key, agent)] = writes[-1][0] if writes else 0
        lock.release()


def _clip_text(clip, *, transcribe: bool) -> str:
    """What the model learns of a recording: where it is, how long, and what was said."""
    head = f"Recorded {clip.seconds:g} seconds of {clip.app}, saved to {clip.path}."
    if clip.silent:
        return head + " It is silent: the app played no sound while recording."
    if not transcribe:
        return head
    from openprogram.runtime import speech_link
    try:
        spoken = speech_link.transcribe(clip.data)
    except speech_link.TranscriptionError as exc:
        return f"{head} Not transcribed: {exc}"
    text = spoken.get("text", "").strip()
    if not text:
        return head + f" No speech was recognized ({spoken.get('engine')})."
    return f"{head}\nTranscript ({spoken.get('engine')}, {spoken.get('model')}):\n{text}"


def open_target(args: dict, session: str) -> ToolReturn:
    """Opening the target: the named app's state, or the app list."""
    if str(args.get("app") or "").strip():
        return run([("get_app_state", {})], args, session)
    return run([("list_apps", {})], args, session)


def _step(action: str, step: dict, app: str) -> dict:
    """One acting step with the defaults the backend expects."""
    return {
        "mouse_button": "left", "click_count": 1, "key": "", "text": "", "value": None, "direction": "down",
        "pages": 1, "pixels": None, "action_name": "", "prefix": "", "suffix": "", "selection_type": "text",
        "format": "text", "duration": 0, "menu_path": None, "window": None, "window_id": None, "path": None,
        "element_index": None, "x": None, "y": None, "to_x": None, "to_y": None,
        **{key: value for key, value in step.items() if value is not None},
        "action": action, "app": app,
    }


def run(steps: list[tuple[str, dict]], args: dict, session: str) -> ToolReturn:
    """Run one action, or several acting steps on one app, with the user's front application kept.

    The backend's own input raises and activates nothing. An application
    that comes forward by itself while it is operated (as it starts, or as it
    opens a window) hands the front straight back.
    """
    from openprogram.host._platform import front

    name = next((str(value).strip() for value in (args.get("app"), *(step.get("app") for _, step in steps))
                 if str(value or "").strip()), "")
    with front.kept(front.named(name) if name else None):
        return _run(steps, args, session)


def _run(steps: list[tuple[str, dict]], args: dict, session: str) -> ToolReturn:
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.backends import backend
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import ComputerUseError, NotDispatched

    try:
        computer = backend()
        if len(steps) == 1 and steps[0][0] not in ACTING_ACTIONS:
            action, step = steps[0]
            merged = {**args, **step}
            app = str(merged.get("app") or "")
            if action == "list_apps":
                return ToolReturn(text=computer.list_apps())
            if action not in ACTIONS:
                raise ComputerUseError(f"unknown app action {action!r}; actions: {', '.join(ACTIONS)}")
            if not app.strip():
                raise ComputerUseError(f"{action} needs app")
            if action == "list_windows":
                return ToolReturn(text=computer.list_windows(app))
            if action == "get_app_state":
                full = bool(merged.get("full"))
                with _held(computer, app, session) as turn:
                    observation = computer.get_state(session, app, window=merged.get("window") or None,
                                                     window_id=merged.get("window_id"), full=full, as_diff=not full,
                                                     include=merged.get("include") or "all")
                    return ToolReturn(text=turn.state(observation.text), images=_images(observation))
            if action == "zoom":
                observation = computer.zoom(session, app, merged.get("x"), merged.get("y"),
                                            merged.get("width"), merged.get("height"))
                return ToolReturn(text=observation.text, images=_images(observation))
            if action == "find":
                return ToolReturn(text=computer.find(session, app, merged.get("text") or "", merged.get("role") or ""))
            if action == "start_audio_recording":
                return ToolReturn(text=computer.start_audio(session, app, merged.get("max_duration")))
            clip = computer.stop_audio(session, app)
            # Models that accept audio input hear the recording itself; others get the text.
            return ToolReturn(text=_clip_text(clip, transcribe=merged.get("transcribe") is not False),
                              audio=[] if clip.silent else [clip.data])
        app = str(args.get("app") or "")
        batch = len(steps) > 1
        observation_args = {"include": "tree"} if args.get("_code_text_only") else {}
        if batch and steps[-1][0] == "get_app_state":
            observation_args.update(steps[-1][1])
            steps = steps[:-1]
        prepared = []
        for number, (action, step) in enumerate(steps, 1):
            if action not in ACTING_ACTIONS:
                if not batch:
                    raise ComputerUseError(f"unknown app action {action!r}; actions: {', '.join(ACTIONS)}")
                raise ComputerUseError(f"step {number}: {action!r} cannot run among several steps; steps are one "
                                       f"of {', '.join(ACTING_ACTIONS)}")
            named = str(step.get("app") or "")
            # The approval names one app, so a step cannot name another one.
            if named and app and normalized_app(named) != normalized_app(app):
                raise ComputerUseError(f"step {number} names another app; these steps drive {app} only")
            app = app or named
            prepared.append((action, step))
        if not app.strip():
            raise ComputerUseError(f"{steps[0][0]} needs app")
        observed_app = observation_args.get("app")
        if observed_app and normalized_app(observed_app) != normalized_app(app):
            raise ComputerUseError("the final observation must use the same app")
        with _held(computer, app, session) as turn:
            return _act(computer, prepared, app, session, batch, turn, observation_args)
    except ComputerUseError as exc:
        return ToolReturn(text=f"Error: {exc}", is_error=True,
                          json_data={"action_dispatched": False} if isinstance(exc, NotDispatched) else None)


def _act(computer, prepared: list[tuple[str, dict]], app: str, session: str, batch: bool,
         turn: _Turn, observation_args: dict | None = None) -> ToolReturn:
    """The acting steps of one call, in order, then the state they left; the application is held throughout."""
    from openprogram.programs.tools.computer_use.computer_use_agent.desktop.base import (ComputerUseError, NotAnswering,
                                                                                          NotDispatched, Report, StaleElement)

    observation_args = observation_args or {}
    state_options = {k: observation_args[k] for k in ("window", "window_id", "full", "include")
                     if k in observation_args}
    done: list[str] = []
    changed, failure, untouched, silent = False, None, False, False
    for number, (action, step) in enumerate(prepared, 1):
        try:
            summary = computer.act(session, action, _step(action, step, app))
        except StaleElement:
            # Input was never dispatched. Observe instead of replaying a click
            # against a different control or repeating earlier batch steps.
            try:
                fresh_options = {"include": "tree"} if state_options.get("include") == "tree" else {}
                observation = computer.get_state(session, app, full=True, as_diff=False, **fresh_options)
            except ComputerUseError as exc:
                return ToolReturn(text=f"The control changed before step {number}; that step and later steps did not run. "
                                       f"The page could not be refreshed: {exc}", is_error=True)
            progress = "\n".join(done)
            text = (f"{progress}\n" if progress else "") + (
                f"Page updated before step {number} ({action}); that step and later steps did not run. "
                "Use this fresh state to choose the next action.\n\n" + observation.text)
            return ToolReturn(text=turn.state(text), images=_images(observation),
                              json_data={"status": "observation_required", "action_dispatched": changed,
                                         "next_step": number})
        except ComputerUseError as exc:
            failure = f"step {number} ({action}) failed: {exc}" if batch else str(exc)
            # Nothing reached the app: the failing step stopped before input and no step before it acted.
            untouched = isinstance(exc, NotDispatched) and not changed
            # A step that failed after its input may have been sent counts as acting.
            turn.acted = turn.acted or not isinstance(exc, NotDispatched)
            silent = isinstance(exc, NotAnswering)
            break
        changed = changed or not isinstance(summary, Report)
        turn.acted = turn.acted or changed
        done.append(f"{number}. {summary}" if batch else str(summary))
    if not changed:
        if failure:
            return ToolReturn(text="\n".join([*done, f"Error: {failure}"]), is_error=True,
                              json_data={"action_dispatched": False} if untouched else None)
        return ToolReturn(text="\n".join(done))
    head = ("Done:\n" + "\n".join(done)) if batch else f"Done: {done[0]}."
    if failure:
        head = (head + "\n" if done else "") + f"Error: {failure} The steps after it did not run."
    if silent:
        # The app stopped answering between two steps: the error says so, and there is no state to read.
        return ToolReturn(text=head, is_error=True)
    try:
        computer.settle(session, app)
        observation = computer.get_state(session, app, as_diff=not state_options.get("full"), **state_options)
    except NotAnswering as exc:
        # The steps went out and the app is still carrying them out, so there is no state to return yet.
        return ToolReturn(text=f"{head}\n\n{exc.app} has not answered since ({exc.seconds:.0f} s): it is still "
                               "carrying that out, so its state could not be read. Do not repeat the step; wait "
                               "(action wait), then read it with get_app_state.", is_error=bool(failure))
    except ComputerUseError as exc:
        # The steps went out; what they left cannot be read (the app quit, the screen locked). They stay done.
        return ToolReturn(text=f"{head}\n\nIts state could not be read afterwards: {exc}", is_error=bool(failure))
    return ToolReturn(text=turn.state(f"{head}\n\n{observation.text}"), images=_images(observation),
                      is_error=bool(failure))


__all__ = ["ACTING_ACTIONS", "ACTIONS", "APP_WAIT_SECONDS", "AUDIO_ACTIONS", "NAME", "available", "approval", "guide",
           "normalized_app", "open_target", "run"]
