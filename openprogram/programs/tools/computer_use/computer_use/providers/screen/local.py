"""Target ``screen``: one whole display of this computer, operated with the real pointer and keyboard.

``app`` reads one application window and acts on it in the background.
``screen`` is for what lies outside one window: the desktop, the menu bar,
system dialogs, drags across windows and windows that ignore background
input. Its steps move the user's pointer and type at the front application,
as the user's own hands would, so the call holds the whole machine.

It shows pixels only. get_state returns a screenshot of one display
(``display``, the main one without it), and x,y in every later step
are pixels of the last screenshot of that display, mapped to screen points
through that display's origin and scale.

Before each pointer step the application under the point is found, and
before each key or text step the front application and the focused field,
and the step is refused by the same rules as ``app``: an application on
the owner's refused list, OpenProgram's own windows outside Bypass mode,
password fields when the owner turned them off, and always while secure
keyboard entry is on. Refused applications and OpenProgram's windows are
left out of every screenshot.

It is off unless the owner turns on ``computer_use.whole_screen``: an
ordinary task needs one application, not the whole screen, so by default
the target is neither listed nor callable (``registry.whole_screen``).

``providers/window/provider.py`` calls :func:`providers` for the connection method
``local`` while the whole screen is on; this module offers nothing itself.
The driver of each
platform is in ``desktop/<platform>/screen.py``; a platform without one
yields no target.
"""
from __future__ import annotations

import platform
import time
from typing import Any, Callable

from openprogram.host.machines import Machine
from openprogram.programs._host.execution import ToolReturn

from ... import errors
from ...routing import locks
from ...contract import Approval, Call, Described, Provider, Row, Steps
from .shots import MAX_IMAGE_SIDE, Shot, _scale, _shots, _shots_lock
from .step_args import (Policy, _Stopped, _click, _failure_text, _number, _read_policy, _scroll, _seconds, _stopped,
    _text)
from . import shots


TARGET = "screen"
CAPABILITIES = frozenset({"pixels", "pointer", "hover", "keyboard", "displays"})
_EXTRAS = ("list_displays", "zoom", "paste")
_POINTER = frozenset({"click", "scroll", "drag", "hover"})
_KEYS = frozenset({"type_text", "press_key", "paste"})


HOVER_SECONDS = 0.8
SETTLE_SECONDS = 0.5

_SHOWN = "computer_use controls the whole screen of this computer"
_RULE = "screen"

GRANTS = {
    "screen_recording": "Screen Recording (System Settings > Privacy & Security > Screen & System Audio Recording)",
    "accessibility": "Accessibility (System Settings > Privacy & Security > Accessibility)",
}


def needs(missing: list[str]) -> str:
    """Grants this computer lacks, in the words every target of it uses ("" for none)."""
    named = [GRANTS[name] for name in GRANTS if name in missing]
    return "OpenProgram needs " + " and ".join(named) + "." if named else ""


_NOTE_SHORTCUTS = {
    "macOS": ("super+space opens Spotlight, super+Tab switches applications, ctrl+Up shows Mission Control, "
              "super+w closes the front window, super+q quits the front application, super+c and super+v copy "
              "and paste."),
}


class LocalScreen(Provider):
    """One whole display of this computer at a time, chosen by ``display``."""

    family = "screen"
    capabilities = CAPABILITIES
    state_scope = "agent"

    def __init__(self, machine: Machine, conversation: str = "", *, driver: Callable[[], Any],
                 policy: Callable[[], Policy] = _read_policy) -> None:
        self.machine = machine.id
        self.instance = machine.instance(conversation)
        self._driver = driver
        self._policy = policy

    @property
    def driver(self):
        return self._driver()

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        line = ("a whole display (display), for the desktop, menu bar, system dialogs and drags across "
                "windows; it moves the user's pointer.")
        try:
            granted = self.driver.grants()
        except Exception:
            return Described(line)
        return Described(line, needs([name for name in GRANTS if not granted.get(name, True)]))

    def note(self) -> str:
        system = getattr(self.driver, "system", platform.system())
        version = ""
        try:
            version = self.driver.version()
        except Exception:
            pass
        shortcuts = _NOTE_SHORTCUTS.get(system, "")
        return (f"Note: type=\"screen\" is this computer ({system} {version}".rstrip() + "), where OpenProgram "
                "runs. It moves the real pointer and types with the real keyboard: the user sees the pointer move, "
                "and a step they make at the same moment can land between yours. Keys and text go to the front "
                "application; click a window first to bring it forward. It shows pixels only: get_state returns a "
                "screenshot of one display, every pointer step takes x,y in pixels of the last screenshot of that "
                "display, and every acting call returns a new one. Without display it is the main display; "
                "list_displays lists the others. Applications the owner refused and OpenProgram's own windows are "
                "left out of screenshots, and a step on them is refused (OpenProgram's are allowed in Bypass mode). "
                "Keys use xdotool names (Return, Escape, Tab, BackSpace, Page_Up, F5); super is the Command key. "
                + shortcuts).strip()

    def extra_actions(self) -> tuple[str, ...]:
        return _EXTRAS

    # -- permission -----------------------------------------------------------

    @staticmethod
    def _lists_only(steps: Steps) -> bool:
        return bool(steps) and all(action == "list_displays" for action, _ in steps)

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        if self._lists_only(steps):
            return None
        return Approval(_SHOWN, _RULE)

    def authority(self, steps: Steps, code: bool) -> str:
        return "process.exec"

    def system_access(self, subject: dict, steps: Steps, code: bool) -> str | None:
        if self._lists_only(steps) and not code:
            return None
        return "computer_use:desktop"

    def lock(self, subject: dict, steps: Steps) -> locks.Locks:
        if self._lists_only(steps):
            return locks.Locks()
        return locks.Locks(exclusive=(locks.machine_key(self.instance),))

    def resource(self, subject: dict) -> Row | None:
        display = subject.get("display")
        return Row("desktop", "Screen", f"display {display}" if display not in (None, "") else "main display")

    # -- operating ------------------------------------------------------------

    def _ready(self) -> None:
        """Stop before anything is read or sent when a grant is missing or the screen is locked."""
        driver = self.driver
        granted = driver.grants()
        missing = needs([name for name in GRANTS if not granted.get(name, True)])
        if missing:
            raise errors.PermissionMissing(f"{missing} Ask the user to grant it, then try again. Nothing was sent.")
        if driver.locked():
            raise _Stopped("The screen is locked; nothing can be read or operated until the user unlocks it. "
                           "Nothing was sent.")

    def _displays(self) -> list:
        from ...desktop.common.base import ComputerUseError
        try:
            return list(self.driver.displays())
        except ComputerUseError as exc:
            raise _Stopped(str(exc)) from None

    def _display(self, subject: dict):
        displays = self._displays()
        wanted = subject.get("display")
        if wanted in (None, ""):
            found = next((display for display in displays if display.main), displays[0] if displays else None)
            if found is None:
                raise _Stopped("This computer reports no display.")
            return found
        for display in displays:
            if str(display.id) == str(wanted).strip():
                return display
        raise errors.BadArgument(f"there is no display {wanted!r}. {self._display_lines(displays)}")

    @staticmethod
    def _display_lines(displays: list) -> str:
        rows = [f"- {display.id}{' (main)' if display.main else ''}: {display.size[0]:g}x{display.size[1]:g} points "
                f"at {display.backing:g}x, top left at {display.origin[0]:g},{display.origin[1]:g}"
                for display in displays]
        return "Displays:\n" + "\n".join(rows)

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        if kind != "displays":
            return super().list(kind, subject, args, call)
        try:
            text = self._list_text()
        except _Stopped as exc:
            return _stopped(exc)
        return ToolReturn(text=text)

    def _list_text(self) -> str:
        return (self._display_lines(self._displays()) + "\nName one with display; without it a call shows "
                "the main display.")

    def _shot(self, call: Call, display) -> Shot | None:
        with _shots_lock:
            return _shots.get((call.agent, display.id))

    def _capture(self, call: Call, display, policy: Policy) -> tuple[Shot, bytes | None]:
        """A screenshot of ``display``, which becomes the one later x,y refer to. Without images, only its size."""
        from ...desktop.common.base import ComputerUseError
        scale = _scale(display)
        shot = Shot(display, max(1, int(round(display.size[0] * scale))), max(1, int(round(display.size[1] * scale))))
        image = None
        if call.images:
            try:
                image = self.driver.capture(display, scale, hidden=policy.hides)
            except ComputerUseError as exc:
                raise _Stopped(f"The screen could not be captured: {exc}") from None
        with _shots_lock:
            _shots[(call.agent, display.id)] = shot
        return shot, image

    def _front_line(self) -> str:
        try:
            owner = self.driver.front()
        except Exception:
            return ""
        return f" Front application: {owner.label}." if owner is not None else ""

    def _state_text(self, shot: Shot, image: bytes | None) -> str:
        display = shot.display
        head = (f"Display {display.id}{' (main)' if display.main else ''}, {display.size[0]:g}x{display.size[1]:g} "
                f"points; the screenshot is {shot.width}x{shot.height} pixels.")
        tail = (" Coordinates are pixels of this screenshot." if image is not None else
                " This call reads text only, so no image comes back; x,y still refer to a screenshot of that size.")
        return head + tail + self._front_line()

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        try:
            self._ready()
            display = self._display(subject)
            shot, image = self._capture(call, display, self._policy())
        except _Stopped as exc:
            return _stopped(exc)
        return ToolReturn(text=self._state_text(shot, image), images=[image] if image else [])

    # -- steps ----------------------------------------------------------------

    def _at(self, shot: Shot | None, step: dict, x: str = "x", y: str = "y") -> tuple[float, float]:
        if shot is None:
            raise errors.BadArgument("x,y are pixels of the last screenshot of this display, and there is none yet: "
                                     "call get_state first.")
        px, py = _number(step, x), _number(step, y)
        if not (0 <= px < shot.width and 0 <= py < shot.height):
            raise errors.BadArgument(f"{x},{y} = {px:g},{py:g} lies outside the {shot.width}x{shot.height} "
                                     "screenshot.")
        return shot.point(px, py)

    def _check_point(self, point: tuple[float, float], policy: Policy) -> None:
        from openprogram.runtime.permissions import refusal
        owner = self.driver.owner_at(point)
        if owner is None:
            return
        if policy.refused_app(owner):
            raise refusal.computer_use_refused_app(owner.label)
        if owner.own and not policy.bypass:
            raise refusal.computer_use_own_window(policy.mode or "this mode")

    def _check_keys(self, policy: Policy, what: str) -> None:
        from openprogram.runtime.permissions import refusal
        driver = self.driver
        if driver.secure_input():
            raise _Stopped("Secure keyboard entry is on: a password field has focus somewhere on this computer. Keys "
                           "and text are refused until it closes. Nothing was sent.")
        owner = driver.front()
        if owner is not None:
            if policy.refused_app(owner):
                raise refusal.computer_use_refused_app(owner.label)
            if owner.own and not policy.bypass:
                raise refusal.computer_use_own_window(policy.mode or "this mode")
        if policy.refuse_password_fields and driver.focus_is_password():
            raise refusal.computer_use_password_field(what)

    def _zoom(self, display, shot: Shot | None, step: dict, policy: Policy) -> tuple[str, bytes]:
        from ...desktop.common.base import ComputerUseError
        if shot is None:
            raise errors.BadArgument("zoom takes a region of the last screenshot of this display: call get_state "
                                     "first.")
        px, py, pw, ph = (_number(step, key) for key in ("x", "y", "width", "height"))
        px, py = max(0.0, px), max(0.0, py)
        pw, ph = min(pw, shot.width - px), min(ph, shot.height - py)
        if pw < 4 or ph < 4:
            raise errors.BadArgument(f"the region must lie inside the {shot.width}x{shot.height} screenshot.")
        ratio = shot.scale
        region = (px / ratio, py / ratio, pw / ratio, ph / ratio)
        scale = min(display.backing, MAX_IMAGE_SIDE / max(region[2], region[3], 1.0))
        try:
            image = self.driver.capture(display, scale, region=region, hidden=policy.hides)
        except ComputerUseError as exc:
            raise _Stopped(f"The screen could not be captured: {exc}") from None
        return (f"Zoomed region ({px:g}, {py:g}) {pw:g}x{ph:g} of the last screenshot, at {scale:g} pixels per "
                "point. Coordinates for steps still refer to the last screenshot, not to this image."), image

    def _step(self, action: str, step: dict, shot: Shot | None, policy: Policy, call: Call) -> str:
        """Send one step; what it did, in a few words. Every check comes before anything is sent."""
        driver = self.driver
        if action == "wait":
            seconds = _seconds(step.get("seconds"), 1.0)
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                call.check()
                time.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
            return f"waited {seconds:g} s"
        if action in _POINTER:
            if action == "scroll" and step.get("x") is None and step.get("y") is None:
                point, at = None, driver.pointer()
            else:
                point = at = self._at(shot, step)
            if action == "click":
                button, count = _click(step)
                hold = _seconds(step.get("seconds"), 0.0)
                self._check_point(at, policy)
                driver.click(point, button, count, str(step.get("modifiers") or ""), hold)
                if button == "left":
                    return {1: "clicked", 2: "double-clicked", 3: "triple-clicked"}[count]
                return f"{button}-clicked" + (f" {count} times" if count > 1 else "")
            if action == "hover":
                rest = _seconds(step.get("seconds"), HOVER_SECONDS)
                self._check_point(at, policy)
                driver.move(point)
                time.sleep(rest)
                return "moved the pointer"
            if action == "drag":
                end = self._at(shot, step, "to_x", "to_y")
                took = _seconds(step.get("seconds"), 0.5) or 0.5
                self._check_point(at, policy)
                self._check_point(end, policy)
                driver.drag(point, end, took)
                return "dragged"
            direction, pages, pixels = _scroll(step)
            self._check_point(at, policy)
            driver.scroll(point, direction, pages, pixels)
            return f"scrolled {direction}"
        if action == "press_key":
            key = str(step.get("key") or "").strip()
            if not key:
                raise errors.BadArgument("press_key needs key.")
            hold = _seconds(step.get("seconds"), 0.0)
            self._check_keys(policy, "a key press into the focused password field")
            driver.press(key, hold)
            return f"pressed {key}"
        if action == "type_text":
            text = _text(step)
            self._check_keys(policy, "typing into the focused password field")
            if step.get("clear"):
                driver.press(driver.select_all)
                driver.press("BackSpace")
            driver.type(text)
            if step.get("submit"):
                driver.press(driver.submit)
            return f"typed {len(text)} character(s)"
        if action == "paste":
            text = _text(step)
            self._check_keys(policy, "pasting into the focused password field")
            driver.paste(text)
            return f"pasted {len(text)} character(s)"
        raise errors.NotSupported(f"{action} is not a step of {self.address()}.")

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        from openprogram.runtime.permissions.refusal import Refused

        from ...desktop.common.base import BadArgument, ComputerUseError, NotDispatched
        try:
            self._ready()
            display = self._display(subject)
        except _Stopped as exc:
            return _stopped(exc)
        policy = self._policy()
        batch = len(steps) > 1
        done: list[str] = []
        texts: list[str] = []
        images: list[bytes] = []
        failure = ""
        acted = False   # the screen is read after the steps
        sent = False    # input of this call may have reached the screen
        for number, (action, step) in enumerate(steps, 1):
            try:
                call.check()
            except errors.Timeout:
                if not done:
                    raise
                error = errors.Timeout(f"the call ran out of time. Steps that took effect: {', '.join(done)}. Read "
                                       "the state before going on.")
                error.sent = sent
                raise error from None
            label = f"{number}. {action}" if batch else action
            try:
                if action == "get_state":
                    # Counted as done, so the tally and the number of a failing step stay those of the call.
                    acted = True
                    done.append(f"{label}: the screen is read after the steps" if batch else label)
                    continue
                if action == "list_displays":
                    texts.append(self._list_text())
                    done.append(label)
                    continue
                if action == "zoom":
                    text, image = self._zoom(display, self._shot(call, display), step, policy)
                    texts.append(text)
                    if call.images:
                        images.append(image)
                    done.append(label)
                    continue
                said = self._step(action, step, self._shot(call, display), policy, call)
            except (errors.CallError, Refused, _Stopped, NotDispatched) as exc:
                if isinstance(exc, BadArgument):
                    # A key or modifier name the driver does not know: the step's arguments, as on app.
                    exc = errors.BadArgument(str(exc))
                if not done:
                    if isinstance(exc, NotDispatched):
                        exc = _Stopped(str(exc))
                    if isinstance(exc, _Stopped):
                        return _stopped(exc)
                    raise exc from None
                failure = _failure_text(exc)
                break
            except ComputerUseError as exc:
                # The driver failed partway through a step: part of it may have been sent.
                acted = sent = True
                failure = f"{exc} It may have partly taken effect: read the state before repeating it."
                break
            acted = sent = True
            done.append(f"{label}: {said}" if batch else said)
        if failure:
            failure = (f"step {len(done) + 1} ({steps[len(done)][0]}) failed: " if batch else "") + failure
        shot_text = ""
        if acted:
            time.sleep(SETTLE_SECONDS)
            try:
                shot, image = self._capture(call, display, policy)
                if image is not None:
                    images.append(image)
                shot_text = self._state_text(shot, image)
            except _Stopped as exc:
                shot_text = f"The screen could not be read afterwards: {exc}"
        head = ""
        if done:
            head = ("Done:\n" + "\n".join(done)) if batch else f"Done: {done[0]}."
        if failure:
            head = (head + "\n" if head else "") + f"Error: {failure} The steps after it did not run."
        if batch:
            head += f"\n{len(done)} of {len(steps)} steps done."
        parts = [part for part in (head, *texts, shot_text) if part]
        return ToolReturn(text="\n".join(parts), images=images, is_error=bool(failure))

    def close(self, call: Call) -> None:
        with _shots_lock:
            for key in [key for key in _shots if key[0] == call.agent]:
                _shots.pop(key, None)


def providers(machine: Machine, conversation: str = "") -> list[Provider]:
    """This computer's whole screen on a platform with a driver; else nothing.

    Asked only while the owner has the whole screen on, so nothing is checked here.
    """
    driver = shots._driver_for(platform.system())
    if driver is None:
        return []
    return [LocalScreen(machine, conversation, driver=driver)]


__all__ = ["CAPABILITIES", "GRANTS", "LocalScreen", "TARGET", "needs", "providers"]
