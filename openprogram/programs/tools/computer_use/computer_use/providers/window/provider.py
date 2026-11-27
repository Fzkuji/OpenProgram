"""This computer's own windows and screen, through the platform backends in ``desktop/``.

It is offered for the connection method ``local``. It yields the target
``window`` where a backend exists (macOS, Linux on X11,
Windows): application windows read as a screenshot plus
an element tree, and operated by element or by pixel of the last
screenshot, in the background where the platform allows. While
``computer_use.whole_screen`` is on, it also yields the target ``screen``
from ``providers/screen/local.py``. Both run in this process, so there is no client to
share between them.

Approval is per application (``window <application>``); listing applications
asks nothing. A call holds this computer shared and its application alone,
except for steps the platform can only deliver with the real pointer or in
the foreground, which hold the whole computer.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
import platform
import time
from typing import Callable

from openprogram.host.machines import Machine
from openprogram.runtime.agent.tool_execution import ToolReturn

from ... import errors
from ...routing import locks, registry
from ...actions import vocabulary
from ...contract import Approval, Call, Described, Provider, Row, Steps
from ..screen import local as local_screen
from .platforms import _LINES, _NOTES, _backends, _base, normalized_app
from . import agents
from .agents import _Turn, _turn
from .answers import _Partial, _Run, _application, _clip_text, _coded, _failed, _images, _line, _named, _ran_out
from . import platforms


# Steps that act on the application, after which its new state is read.
ACTING = frozenset({
    "click", "type_text", "press_key", "set_value", "scroll", "drag", "perform_secondary_action", "select_text",
    "paste", "focus", "copy", "menu", "show_window", "hover", "wait", "open_file",
})
# Steps that read or record and return their own answer; each takes a call of its own.
_ALONE = frozenset({"find", "zoom", "start_audio_recording", "stop_audio_recording"})

_EXTRAS_EVERYWHERE = tuple(name for name in vocabulary.FAMILIES["window"].extras if name != "open_file")

_CAPABILITIES = frozenset({"pixels", "tree", "windows", "pointer", "hover", "keyboard", "background", "launch",
                           "audio"})


# How long a rule name is kept: one permission decision asks for it several times, some on the event loop.
_RULE_SECONDS = 5.0
# the application as the model named it -> (when it was read, the name rules give it)
_rule_names: dict[str, tuple[float, str]] = {}
_MAX_RULE_NAMES = 256


class AppProvider(Provider):
    """Application windows of this computer, by element tree, through the platform backend."""

    family = "window"
    state_scope = "agent"

    def __init__(self, machine: Machine, conversation: str = "", system: str | None = None) -> None:
        self.machine = machine.id
        self.system = system or platform.system()
        self.instance = machine.instance(conversation)
        self.capabilities = _CAPABILITIES | (frozenset({"clipboard"}) if self.system != "Linux" else frozenset())

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        return Described(_LINES.get(self.system, _LINES["Linux"]), local_screen.needs(platforms._missing_grants(self.system)))

    def note(self) -> str:
        return _NOTES.get(self.system, _NOTES["Linux"])

    def extra_actions(self) -> tuple[str, ...]:
        if self.system == "Darwin":
            return (*_EXTRAS_EVERYWHERE, "open_file")
        return _EXTRAS_EVERYWHERE

    def opening(self, subject: dict) -> Steps:
        return [("get_state", {})] if _named(subject) else [("list_apps", {})]

    # -- operating ------------------------------------------------------------

    def _computer(self):
        return _backends().backend()

    def _claim(self, call: Call) -> Callable[[dict], tuple[str, str] | None]:
        def claim(window: dict) -> tuple[str, str] | None:
            from ...routing import registry
            return registry.claim(window, call.conversation)
        return claim

    def _key(self, computer, app: str) -> str:
        """The application however the model named it: the backend's identity for it, else the name."""
        key = ""
        resolve = getattr(computer, "app_key", None)
        if resolve is not None:
            try:
                key = str(resolve(app) or "")
            except Exception:
                key = ""
        return key.lower() or normalized_app(app)

    def _rule_name(self, app: str) -> str:
        """The application as rules name it: its own display name however the model named it (a bundle id, a
        path, a pid), else the name given. The backend's ``app_name`` matches exactly and does no slow or
        launching work, so approval, the saved rule and every later call get the same name."""
        given = normalized_app(app)
        now = time.monotonic()
        kept = _rule_names.get(given)
        if kept is not None and now - kept[0] < _RULE_SECONDS:
            return kept[1]
        try:
            resolve = getattr(self._computer(), "app_name", None)
            name = str(resolve(str(app).strip()) or "") if resolve is not None else ""
        except Exception:
            name = ""
        out = normalized_app(name) or given
        if len(_rule_names) >= _MAX_RULE_NAMES:
            _rule_names.clear()
        _rule_names[given] = (now, out)
        return out

    @contextmanager
    def _kept(self, app: str):
        """The user's front application stays in front while the step runs; one that comes forward by itself
        (as it starts, or opens a window) hands the front straight back."""
        from openprogram.host._platform import front
        with front.kept(front.named(app) if app else None):
            yield

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        base = _base()
        try:
            computer = self._computer()
            if kind == "apps":
                return ToolReturn(text=computer.list_apps())
            if kind == "windows":
                app = _named(subject) or _named(args)
                if not app:
                    raise errors.BadArgument("list_windows needs app: the application whose windows to list.")
                return ToolReturn(text=computer.list_windows(app))
        except base.ComputerUseError as exc:
            return _failed(exc)
        raise errors.NotSupported(f"{self.address()} has nothing to list as {kind}; it lists apps and windows.")

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        app = _named(subject)
        if not app:
            raise errors.BadArgument("get_state needs app: the application to read (list_apps lists them).")
        include = str(options.get("include") or "all").lower()
        if include == "all" and not call.images:
            include = "tree"
        full = bool(options.get("full"))
        base = _base()
        try:
            computer = self._computer()
            with self._kept(app), _turn(self._key(computer, app), app, call.agent) as turn:
                observation = computer.get_state(call.agent, app, window=subject.get("window") or None,
                                                 window_id=subject.get("window_id"), full=full, as_diff=not full,
                                                 include=include, claim=self._claim(call))
                return ToolReturn(text=turn.state(observation.text), images=_images(observation, call))
        except base.ComputerUseError as exc:
            return _failed(exc)

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        app = _application(subject, steps)
        if len(steps) == 1 and steps[0][0] in _ALONE:
            return self._alone(app, steps[0][0], steps[0][1], call)
        # The shape of the call as sent: [find, get_state] is two steps, and find cannot run among several.
        batch = len(steps) > 1
        state_options: dict = {}
        if len(steps) > 1 and steps[-1][0] == "get_state":
            state_options = dict(steps[-1][1])
            steps = steps[:-1]
        for number, (action, _step) in enumerate(steps, 1):
            if action not in ACTING:
                if not batch:
                    raise errors.NotSupported(f"{action} is not a step of {self.address()}.")
                raise errors.BadArgument(f"step {number}: {action} cannot run among several steps; steps are "
                                         f"{', '.join(sorted(ACTING))}, and a last get_state.")
        if not app:
            raise errors.BadArgument(f"{steps[0][0]} needs app: the application to operate "
                                     "(list_apps lists them).")
        base = _base()
        try:
            computer = self._computer()
            with self._kept(app), _turn(self._key(computer, app), app, call.agent) as turn:
                return self._steps(computer, app, subject, steps, call, turn, state_options)
        except base.ComputerUseError as exc:
            return _failed(exc)

    def _alone(self, app: str, action: str, step: dict, call: Call) -> ToolReturn:
        if not app:
            raise errors.BadArgument(f"{action} needs app: the application (list_apps lists them).")
        base = _base()
        try:
            computer = self._computer()
            if action == "find":
                return ToolReturn(text=computer.find(call.agent, app, step.get("text") or "", step.get("role") or ""))
            if action == "zoom":
                observation = computer.zoom(call.agent, app, step.get("x"), step.get("y"), step.get("width"),
                                            step.get("height"))
                return ToolReturn(text=observation.text, images=_images(observation, call))
            if action == "start_audio_recording":
                return ToolReturn(text=computer.start_audio(call.agent, app, step.get("max_duration")))
            clip = computer.stop_audio(call.agent, app)
            # Models that accept audio input hear the recording itself; others get the text.
            return ToolReturn(text=_clip_text(clip, transcribe=step.get("transcribe") is not False),
                              audio=[] if clip.silent else [clip.data])
        except base.ComputerUseError as exc:
            return _failed(exc)

    def _steps(self, computer, app: str, subject: dict, steps: Steps, call: Call, turn: _Turn,
               state_options: dict) -> ToolReturn:
        """The acting steps of one call, in order, then the state they left."""
        base = _base()
        batch = len(steps) > 1
        include = str(state_options.get("include") or ("all" if call.images else "tree")).lower()
        if include == "all" and not call.images:
            include = "tree"
        run = _Run(full=bool(state_options.get("full")))
        done: list[str] = []
        changed, failure, untouched, silent = False, None, False, False
        for number, (action, step) in enumerate(steps, 1):
            try:
                call.check()
            except errors.Timeout:
                raise _ran_out(done, changed, number, action, "before") from None
            try:
                summary = self._step(computer, app, subject, action, step, call, run)
            except errors.Timeout:
                # Only wait(text) checks the deadline inside a step, and it sends nothing.
                raise _ran_out(done, changed, number, action, "during") from None
            except _Partial as part:
                # Part of the step acted: it is done as far as it went, never to be repeated.
                where = f"step {number} ({action}): " if batch else ""
                coded = _coded(part.exc)
                failure = (f"{coded.code}: " if coded is not None else "Error: ") + \
                    f"{where}the rest of the step failed: {part.exc}"
                done.append(f"{number}. {part.done}" if batch else part.done)
                changed = turn.acted = True
                silent = isinstance(part.exc, base.NotAnswering)
                break
            except base.StaleElement as exc:
                # Nothing of this step was sent. Read afresh rather than replay against another control.
                return self._gone(computer, app, subject, number, action, exc, done, changed, call, turn)
            except base.ComputerUseError as exc:
                where = f"step {number} ({action}): " if batch else ""
                coded = _coded(exc)
                failure = coded.code + ": " + where + str(exc) if coded is not None else f"Error: {where}{exc}"
                # Nothing reached the app: the failing step stopped before input and no step before it acted.
                untouched = isinstance(exc, base.NotDispatched) and not changed
                # A step that failed after its input may have been sent counts as acting.
                turn.acted = turn.acted or not isinstance(exc, base.NotDispatched)
                silent = isinstance(exc, base.NotAnswering)
                break
            changed = changed or not isinstance(summary, base.Report)
            turn.acted = turn.acted or changed
            done.append(f"{number}. {summary}" if batch else str(summary))
        if not changed:
            if failure:
                tally = [f"{len(done)} of {len(steps)} steps done."] if batch else []
                return ToolReturn(text="\n".join([*done, failure, *tally]), is_error=True,
                                  json_data={"action_dispatched": False} if untouched else None)
            return ToolReturn(text="\n".join(done))
        head = ("Done:\n" + "\n".join(done)) if batch else f"Done: {done[0]}."
        if failure:
            head += f"\n{failure} The steps after it did not run."
        if batch:
            head += f"\n{len(done)} of {len(steps)} steps done."
        if silent:
            # The app stopped answering between two steps: the error says so, and there is no state to read.
            return ToolReturn(text=head, is_error=True)
        try:
            computer.settle(call.agent, app)
            observation = computer.get_state(call.agent, app, as_diff=not run.full, full=run.full, include=include,
                                             claim=self._claim(call))
        except base.NotAnswering as exc:
            # The steps went out and the app is still carrying them out, so there is no state to return yet.
            unknown = errors.OutcomeUnknown(
                f"{exc.app} has not answered since ({exc.seconds:.0f} s): it is still carrying the last step out, "
                "so its state could not be read. Do not repeat the step; wait (action wait), then read it with "
                "get_state.")
            return ToolReturn(text=f"{head}\n\n{unknown.text()}", is_error=True)
        except base.ComputerUseError as exc:
            # The steps went out; what they left cannot be read (the app quit, the screen locked). They stay done.
            return ToolReturn(text=f"{head}\n\nIts state could not be read afterwards: {_line(exc)}",
                              is_error=bool(failure))
        return ToolReturn(text=turn.state(f"{head}\n\n{observation.text}"), images=_images(observation, call),
                          is_error=bool(failure))

    def _gone(self, computer, app: str, subject: dict, number: int, action: str, exc: Exception, done: list[str],
              changed: bool, call: Call, turn: _Turn) -> ToolReturn:
        """``ELEMENT_GONE:`` with the current state, so the next step can pick the element afresh."""
        base = _base()
        gone = errors.ElementGone(f"{exc} Step {number} ({action}) and the steps after it did not run; the "
                                  "current state follows.")
        progress = "\n".join(done)
        try:
            observation = computer.get_state(call.agent, app, full=True, as_diff=False,
                                             include="all" if call.images else "tree", claim=self._claim(call))
        except base.ComputerUseError as failure:
            return ToolReturn(text=(f"{progress}\n" if progress else "") + f"{gone.text()}\nThe state could not be "
                                   f"read: {_line(failure)}", is_error=True,
                              json_data={"action_dispatched": changed})
        text = (f"{progress}\n" if progress else "") + f"{gone.text()}\n\n{observation.text}"
        return ToolReturn(text=turn.state(text), images=_images(observation, call), is_error=True,
                          json_data={"status": "observation_required", "action_dispatched": changed,
                                     "next_step": number})

    def _step(self, computer, app: str, subject: dict, action: str, step: dict, call: Call, run: _Run):
        """One step, with what the backend lacks built from what it has."""
        args = {**step, "app": app}
        if action == "wait" and str(step.get("text") or "").strip():
            return self._wait_for(computer, app, step, call, run)
        if action == "type_text" and (step.get("clear") or step.get("submit")):
            return self._type(computer, app, args, call)
        if action == "show_window":
            args.setdefault("window", subject.get("window"))
            args.setdefault("window_id", subject.get("window_id"))
        return computer.act(call.agent, action, {key: value for key, value in args.items()
                                                 if key not in ("clear", "submit")})

    def _type(self, computer, app: str, args: dict, call: Call) -> str:
        """type_text with clear (the field emptied first) and submit (Return pressed after)."""
        base = _base()
        text = str(args.get("text") or "")
        element = args.get("element")
        parts: list[str] = []

        def then(action: str, step: dict) -> str:
            # A later part that fails still follows parts that acted, and the call must say those went in.
            try:
                return computer.act(call.agent, action, {"app": app, **step})
            except base.StaleElement:
                raise
            except base.ComputerUseError as exc:
                if not parts:
                    raise
                raise _Partial("; ".join(parts), exc) from exc

        if args.get("clear"):
            cleared = False
            if element is not None:
                try:
                    then("set_value", {"element": element, "value": ""})
                    cleared = True
                except (base.StaleElement, base.NotAnswering):
                    raise
                except base.ComputerUseError:
                    # A field without a settable value is emptied with the keyboard instead.
                    cleared = False
            if not cleared:
                select_all = "super+a" if self.system == "Darwin" else "ctrl+a"
                then("press_key", {"key": select_all, **({"element": element} if element is not None else {})})
                then("press_key", {"key": "BackSpace"})
            parts.append("cleared the field")
        if text:
            typed = then("type_text", {"text": text, **({"element": element} if element is not None else {})})
            parts.append(str(typed))
        if args.get("submit"):
            then("press_key", {"key": "Return"})
            parts.append("pressed Return")
        if not parts:
            raise base.BadArgument("text is required")
        return "; ".join(parts)

    def _wait_for(self, computer, app: str, step: dict, call: Call, run: _Run) -> str:
        """wait(text): until the window shows ``text``, up to ``seconds`` (10 when not given)."""
        base = _base()
        seconds = base.seconds_arg(step.get("seconds") if step.get("seconds") not in (None, "") else
                                   base.MAX_HOLD_SECONDS)
        wanted = " ".join(str(step["text"]).split()).lower()
        deadline = time.monotonic() + seconds
        # Each look replaces the tree the next diff is taken against, so the state after the steps is read whole.
        run.full = True
        while True:
            call.check()
            observation = computer.get_state(call.agent, app, full=True, as_diff=False, include="tree",
                                             claim=self._claim(call))
            seen = observation.text
            if observation.sparse and wanted not in " ".join(seen.split()).lower():
                # A window with little tree lists the text of its screenshot, read only with a screenshot; on
                # any other window a screenshot adds no text, so it is not taken.
                try:
                    seen += "\n" + computer.get_state(call.agent, app, include="screenshot",
                                                      claim=self._claim(call)).text
                except base.NotAnswering:
                    raise
                except base.ComputerUseError:
                    pass  # no screenshot now (a minimized window, a missing grant); the next look tries again
            if wanted in " ".join(seen.split()).lower():
                return f"waited until {step['text']!r} showed"
            left = deadline - time.monotonic()
            if left <= 0:
                return f"waited {seconds:g}s; {step['text']!r} did not show"
            time.sleep(min(0.5, left))

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        if steps and all(action == "list_apps" for action, _ in steps):
            return None
        app = _application(subject, steps, strict=False)
        name = self._rule_name(app) if app else ""
        return Approval(f"computer_use controls {name or 'an application'}", f"window {name}" if name else None)

    def authority(self, steps: Steps, code: bool) -> str:
        return "process.exec"

    def system_access(self, subject: dict, steps: Steps, code: bool) -> str | None:
        if not code and steps and all(action == "list_apps" for action, _ in steps):
            return None
        return "computer_use:desktop"

    def lock(self, subject: dict, steps: Steps) -> locks.Locks:
        if steps and all(action in vocabulary.LISTS for action, _ in steps):
            return locks.Locks()
        machine = locks.machine_key(self.instance)
        if any(self._needs_machine(action, step) for action, step in steps):
            return locks.Locks(exclusive=(machine,))
        app = _application(subject, steps, strict=False)
        if not app:
            return locks.Locks(shared=(machine,))
        try:
            key = self._key(self._computer(), app)
        except Exception:
            key = normalized_app(app)
        return locks.Locks(shared=(machine,), exclusive=(locks.application_key(self.instance, key),))

    def _needs_machine(self, action: str, step: dict) -> bool:
        """Whether this platform may deliver the step with the real pointer or in the foreground.

        macOS posts every step to the application's process. Elsewhere the
        driver picks foreground delivery while the step runs, from what it
        meets there, so the lock, taken before, holds the machine for every
        action that has such a path. On X11, a click, scroll, key or text
        that AT-SPI cannot carry out goes through XTEST with the window in
        front; hover and drag always do. On Windows, windows that take only
        real input (Store apps, Chromium) get clicks, drags, keys and text
        through SendInput, shortcuts read the real keyboard state, paste
        outside an edit control sends ctrl+V, and focus can bring the window
        to the front (UI Automation's SetFocus activates it) before it gives
        the front back; scroll and hover are posted messages everywhere.
        """
        if self.system == "Darwin":
            return False
        if self.system == "Linux":
            return action in ("click", "scroll", "press_key", "type_text", "hover", "drag")
        return action in ("click", "drag", "press_key", "type_text", "paste", "focus")

    # -- showing --------------------------------------------------------------

    def resource(self, subject: dict) -> Row | None:
        app = _named(subject)
        if not app:
            return None
        return Row("desktop", app, str(subject.get("window") or ""))

    def close(self, call: Call) -> None:
        """Drop the window states this agent kept; its element ids mean nothing to the next agent."""
        try:
            computer = self._computer()
        except Exception:
            return
        held = getattr(computer, "_states", None)
        if isinstance(held, dict):
            # Other agents' reads write this dict while this agent ends: under the driver's lock where it has
            # one, and from a copy of its keys, which CPython takes without letting another thread in.
            with getattr(computer, "_lock", None) or nullcontext():
                for key in [key for key in list(held) if isinstance(key, tuple) and key and key[0] == call.agent]:
                    held.pop(key, None)
        agents.forget(call.agent)


def providers(machine: Machine, conversation: str) -> list[Provider]:
    """This computer's application windows (``window``) where a backend exists."""
    return [AppProvider(machine, conversation)] if platforms.supported() else []


def screen_providers(machine: Machine, conversation: str) -> list[Provider]:
    """This computer's whole screen (``screen``), only while the owner has it on."""
    return local_screen.providers(machine, conversation) if registry.whole_screen() else []


registry.offer("local", providers)
registry.offer("local", screen_providers)


__all__ = ["ACTING", "AppProvider", "providers", "screen_providers"]
