"""Connection method ``osworld``: a virtual machine behind an OSWorld-compatible HTTP server.

``GET /screenshot`` returns the screen as PNG; ``POST /execute`` runs a
command there, which drives pyautogui. This module registers the method
with the device layer (its address, check and admission) and offers
``computer_use`` one target on such a machine, ``<machine> screen``. It
shows pixels only: there is no element tree, so pointer steps take x,y of
the last screenshot, and there is no ``window`` target.

Setting the machine up is the consent to operate it: no call asks. A rule
can still name it (``<id> screen``).
"""
from __future__ import annotations

from contextvars import ContextVar
import json
import threading
import time
from urllib.parse import urlparse

from openprogram.host.machines import Connection, Machine, here, kinds
from openprogram.host.machines.kinds import Kind, KindError
from openprogram.programs._host.execution import ToolReturn

from ... import errors
from ...actions import keynames
from ...routing import locks, registry
from ...contract import Approval, Call, Described, Provider, Row, Steps

_TIMEOUT = 30
_CONSUMER = "computer_use.osworld"
_CAPABILITIES = frozenset({"pixels", "pointer", "hover", "keyboard"})
# Wheel clicks for one page, and the pixels one click moves.
_CLICKS_PER_PAGE = 5
_PIXELS_PER_CLICK = 120
MAX_WAIT_SECONDS = 10.0
# How long a cancelled call still waits for the machine's answer before it lets the machine go.
CANCEL_SECONDS = 10.0

# The call a request belongs to, so a cancel can end the wait for a machine that hangs.
_current: ContextVar[Call | None] = ContextVar("osworld_call", default=None)


class _Failed(RuntimeError):
    """A step the machine answered with an error: it ran and failed there."""


class _Abandoned(Exception):
    """The call was cancelled and the machine did not answer within ``CANCEL_SECONDS``."""


def _bounded(fn, call: Call | None):
    """``fn()``, one blocking request. For a call that can be cancelled it runs on a thread of its own, so a
    cancel lets the machine's lock go once the machine answers or ``CANCEL_SECONDS`` pass."""
    if call is None or call.check_cancelled is None:
        return fn()
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:
            box["error"] = exc

    worker = threading.Thread(target=run, name="osworld-request", daemon=True)
    worker.start()
    since = None
    while True:
        worker.join(0.25)
        if not worker.is_alive():
            break
        if since is None and call.cancelled():
            since = time.monotonic()
        if since is not None and time.monotonic() - since >= CANCEL_SECONDS:
            raise _Abandoned()
    if "error" in box:
        raise box["error"]
    return box.get("value")


class OSWorldScreen(Provider):
    family = "screen"
    capabilities = _CAPABILITIES
    state_scope = "conversation"

    def __init__(self, machine: Machine) -> None:
        self.machine = machine.id
        self.name = machine.name
        self.url = str(machine.connection.get("url")).rstrip("/")

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        return Described("virtual machine, pixels only")

    def note(self) -> str:
        return (f"Note: device=\"{self.machine}\" is the whole screen of a machine ({self.name}), behind "
                f"an OSWorld server at {self.url}. It shows pixels only: there is no element tree, so every pointer "
                "step takes x,y in pixels of the last screenshot, and get_state returns a screenshot. Every acting "
                "call returns a new screenshot. Its operating system is not reported: read the screenshot to tell, "
                "and use that system's shortcut keys (super is its Windows or Command key). It has one display and "
                "no clipboard: type text with type_text.")

    # -- operating ------------------------------------------------------------

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        self._one_display(subject)
        token = _current.set(call)
        try:
            image = self._screenshot()
        finally:
            _current.reset(token)
        return ToolReturn(text="Screenshot of the machine. Coordinates are pixels of this screenshot.",
                          images=[image] if call.images else [])

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        self._one_display(subject)
        token = _current.set(call)
        try:
            return self._act(steps, call)
        finally:
            _current.reset(token)

    def _act(self, steps: Steps, call: Call) -> ToolReturn:
        done: list[str] = []
        failure = ""
        sent = False
        batch = len(steps) > 1
        # Every step is checked before the first is sent, so a refused argument means nothing ran and a
        # corrected retry never repeats a step that took effect.
        plan: list[str | float | None] = []
        for number, (action, step) in enumerate(steps, 1):
            try:
                plan.append(None if action == "get_state" else
                            _seconds(step.get("seconds"), 1.0) if action == "wait" else _script(action, step))
            except errors.CallError as exc:
                raise type(exc)(f"step {number} ({action}): {exc}" if batch else str(exc)) from None
        for number, ((action, step), work) in enumerate(zip(steps, plan), 1):
            try:
                call.check()
            except errors.Timeout:
                raise _ran_out(done, sent) from None
            if action == "get_state":
                # Counted as done, so the tally stays that of the call; the screen is read after the steps anyway.
                done.append(f"{number}. get_state: the screen is read after the steps" if batch else action)
                continue
            try:
                if action == "wait":
                    time.sleep(work)
                else:
                    self._execute(work, sent=sent)
                    sent = True
            except _Failed as exc:
                failure = f"step {number} ({action}) failed: {exc}" if batch else str(exc)
                break
            except errors.OutcomeUnknown as exc:
                progress = f"Steps done: {', '.join(done) or 'none'}. " if batch else ""
                raise errors.OutcomeUnknown(f"{progress}{action} was sent and the machine did not answer: {exc} "
                                            "Read the state before deciding; do not repeat the step.") from None
            done.append(f"{number}. {action}" if batch else action)
        if done:
            # A cancelled call takes no closing screenshot from a machine that may hang.
            try:
                call.check()
            except errors.Timeout:
                raise _ran_out(done, sent) from None
            time.sleep(0.5)
        try:
            image = self._screenshot()
        except errors.CallError as exc:
            if not done:
                raise
            return ToolReturn(text=f"Done: {', '.join(done)}. The screen could not be read afterwards: {exc}",
                              is_error=bool(failure))
        head = ("Done:\n" + "\n".join(done)) if len(done) > 1 else (f"Done: {done[0]}." if done else "")
        if failure:
            head = (head + "\n" if head else "") + f"Error: {failure} The steps after it did not run."
        if batch:
            head += f"\n{len(done)} of {len(steps)} steps done."
        return ToolReturn(text=(head + " " if head else "") + "Coordinates are pixels of this screenshot.",
                          images=[image] if call.images else [], is_error=bool(failure))

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        return Approval(None, self.target)

    def lock(self, subject: dict, steps: Steps) -> locks.Locks:
        return locks.Locks(exclusive=(locks.machine_key(self.machine),))

    def resource(self, subject: dict) -> Row | None:
        return Row("machine", self.name, self.url)

    # -- the server -----------------------------------------------------------

    def _one_display(self, subject: dict) -> None:
        if subject.get("display") not in (None, ""):
            raise errors.NotSupported(f"{self.address()} has one display; leave display out.")

    def _client(self):
        """The machine's server, reached through the outbound policy. Setting the
        address is the owner's authorization to reach that private origin."""
        from openprogram.host.security.safe_http import configured_safe_client
        from openprogram.host.security.url_policy import OwnerURLException, normalize_origin

        origin = normalize_origin(self.url)
        return configured_safe_client(_CONSUMER, self.url, timeout=_TIMEOUT + 5,
                                      owner_exception=OwnerURLException(consumer=_CONSUMER, origin=origin))

    def _screenshot(self) -> bytes:
        def get() -> bytes:
            with self._client() as client:
                response = client.get(f"{self.url}/screenshot")
                response.raise_for_status()
                return response.content
        call = _current.get()
        try:
            data = _bounded(get, call)
        except _Abandoned:
            call.check()
            raise errors.MachineUnreachable(f"machine {self.machine} at {self.url} did not return a screenshot in "
                                            "time. Nothing was sent.") from None
        except Exception as exc:
            raise errors.MachineUnreachable(
                f"machine {self.machine} at {self.url} did not return a screenshot: {exc}. Nothing was sent.") from exc
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise errors.MachineUnreachable(
                f"machine {self.machine} at {self.url} returned something other than a PNG screenshot: it is not "
                "an OSWorld server.")
        return data

    def _execute(self, script: str, *, sent: bool) -> None:
        body = json.dumps({"command": ["python3", "-c", script], "shell": False, "timeout": _TIMEOUT}).encode()

        def post():
            with self._client() as client:
                return client.post(f"{self.url}/execute", content=body, headers={"Content-Type": "application/json"})
        call = _current.get()
        try:
            response = _bounded(post, call)
        except _Abandoned:
            call.check()
            raise errors.OutcomeUnknown("the call was cancelled before the machine answered") from None
        except Exception as exc:
            if _never_connected(exc) and not sent:
                raise errors.MachineUnreachable(
                    f"machine {self.machine} at {self.url} is not reachable: {exc}. Nothing was sent.") from exc
            if _never_connected(exc):
                raise _Failed(f"the machine stopped answering: {exc}") from exc
            raise errors.OutcomeUnknown(str(exc)) from exc
        try:
            response.raise_for_status()
            payload = json.loads(response.content or b"{}")
        except Exception as exc:
            raise _Failed(f"the machine did not run the step: {exc}") from exc
        if not isinstance(payload, dict):
            raise _Failed("the machine returned a malformed reply")
        if payload.get("error") or payload.get("returncode", 0) != 0:
            raise _Failed(str(payload.get("error") or payload.get("stderr") or f"exit {payload.get('returncode')}").strip())


def _ran_out(done: list[str], sent: bool) -> errors.Timeout:
    """``TIMEOUT:`` with the steps that took effect."""
    error = errors.Timeout(f"the call ran out of time. Steps that took effect: {', '.join(done) or 'none'}. Read "
                           "the state before going on.")
    error.sent = sent
    return error


def _never_connected(exc: BaseException) -> bool:
    """Whether the request failed before it could have reached the machine."""
    names = {cls.__name__ for cls in type(exc).__mro__}
    return bool(names & {"ConnectError", "ConnectTimeout", "PoolTimeout", "UnsupportedProtocol", "URLPolicyError",
                         "URLDenied"}) or isinstance(exc, (ConnectionRefusedError, PermissionError))


def _number(step: dict, key: str) -> int:
    try:
        return int(round(float(step[key])))
    except (KeyError, TypeError, ValueError):
        raise errors.BadArgument(f"{key} must be a number of screenshot pixels.") from None


def _seconds(value, default: float) -> float:
    try:
        seconds = float(default if value in (None, "") else value)
    except (TypeError, ValueError):
        raise errors.BadArgument("seconds must be a number.") from None
    if not 0 <= seconds <= MAX_WAIT_SECONDS:
        raise errors.BadArgument(f"seconds must be between 0 and {MAX_WAIT_SECONDS:g}.")
    return seconds


def _modifiers(step: dict) -> list[str]:
    held = str(step.get("modifiers") or "")
    return keynames.pyautogui(held) if held else []


def _script(action: str, step: dict) -> str:
    """The pyautogui program for one step; every value is passed as a JSON literal."""
    lit = json.dumps
    if action == "click":
        button = str(step.get("mouse_button") or "left").lower()
        if button not in {"left", "right", "middle"}:
            raise errors.BadArgument("mouse_button is left, right or middle.")
        try:
            clicks = int(step.get("click_count") or 1)
        except (TypeError, ValueError):
            clicks = 0
        if not 1 <= clicks <= 3:
            raise errors.BadArgument("click_count is 1, 2 or 3.")
        x, y, held = _number(step, "x"), _number(step, "y"), _modifiers(step)
        hold = _seconds(step.get("seconds"), 0)
        press = (f"pyautogui.moveTo({x}, {y}); pyautogui.mouseDown(button={lit(button)}); time.sleep({hold}); "
                 f"pyautogui.mouseUp(button={lit(button)})" if hold else
                 f"pyautogui.click({x}, {y}, button={lit(button)}, clicks={clicks}, interval=0.08)")
        down = "".join(f"pyautogui.keyDown({lit(key)}); " for key in held)
        up = "".join(f"; pyautogui.keyUp({lit(key)})" for key in reversed(held))
        return f"import time, pyautogui; {down}{press}{up}"
    if action == "hover":
        rest = _seconds(step.get("seconds"), 0.8)
        return (f"import time, pyautogui; pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}, duration=0.2); "
                f"time.sleep({rest})")
    if action == "drag":
        took = _seconds(step.get("seconds"), 0.5) or 0.5
        return (f"import pyautogui; pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}); "
                f"pyautogui.dragTo({_number(step, 'to_x')}, {_number(step, 'to_y')}, duration={took}, button='left')")
    if action == "scroll":
        direction = str(step.get("direction") or "down").lower()
        if direction not in {"up", "down", "left", "right"}:
            raise errors.BadArgument("direction is up, down, left or right.")
        try:
            if step.get("pixels") not in (None, ""):
                clicks = max(1, int(round(float(step["pixels"]) / _PIXELS_PER_CLICK)))
            else:
                clicks = max(1, int(round(float(step.get("pages") or 1) * _CLICKS_PER_PAGE)))
        except (TypeError, ValueError):
            raise errors.BadArgument("pages and pixels are numbers.") from None
        at = (f"pyautogui.moveTo({_number(step, 'x')}, {_number(step, 'y')}); "
              if step.get("x") is not None and step.get("y") is not None else "")
        if direction in {"up", "down"}:
            return f"import pyautogui; {at}pyautogui.scroll({clicks if direction == 'up' else -clicks})"
        return f"import pyautogui; {at}pyautogui.hscroll({-clicks if direction == 'left' else clicks})"
    if action == "type_text":
        text = str(step.get("text") or "")
        if not text:
            raise errors.BadArgument("type_text needs text.")
        clear = "pyautogui.hotkey('ctrl', 'a'); pyautogui.press('backspace'); " if step.get("clear") else ""
        submit = "; pyautogui.press('enter')" if step.get("submit") else ""
        return f"import pyautogui; {clear}pyautogui.write({lit(text)}, interval=0.02){submit}"
    if action == "press_key":
        keys = keynames.pyautogui(str(step.get("key") or ""))
        if not keys or not all(keys):
            raise errors.BadArgument("press_key needs key.")
        hold = _seconds(step.get("seconds"), 0)
        if hold:
            down = "; ".join(f"pyautogui.keyDown({lit(key)})" for key in keys)
            up = "; ".join(f"pyautogui.keyUp({lit(key)})" for key in reversed(keys))
            return f"import time, pyautogui; {down}; time.sleep({hold}); {up}"
        return f"import pyautogui; pyautogui.hotkey(*{lit(keys)})"
    raise errors.NotSupported(f"{action} is not available on a machine behind an OSWorld server.")


def _normalize(settings: dict) -> dict:
    url = str(settings.get("url") or "").strip().rstrip("/")
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise KindError("url must be an http:// or https:// address")
    return {**settings, "url": url}


def _admit(settings: dict) -> None:
    """A server on a loopback address may be this computer itself: its screenshot, taken now, is compared
    with this computer's own display. Where either cannot be read, nothing is claimed."""
    url = str(settings.get("url") or "")
    if not here.is_loopback(urlparse(url).hostname):
        return
    probe = Machine("new", "new", Connection("osworld", dict(settings)))
    try:
        shot = OSWorldScreen(probe)._screenshot()
    except errors.CallError:
        return
    from . import shots
    if shots.shows_this_display(shot):
        raise KindError(f"Refused: the OSWorld server at {url} shows this computer's own display, and this "
                        "computer is built in, never added as a machine.")


def _open(machine: Machine, conversation: str) -> list[Provider]:
    return [OSWorldScreen(machine)]


def _check(machine: Machine) -> tuple[bool, str]:
    try:
        OSWorldScreen(machine)._screenshot()
    except errors.CallError as exc:
        return False, str(exc)[:500]
    return True, "Connected · the server returned a screenshot"


KIND = kinds.register(Kind(
    name="osworld",
    label="OSWorld server",
    label_zh="OSWorld 服务器",
    settings={
        "type": "object",
        "properties": {
            "url": {"type": "string", "title": "Server address",
                    "description": "http:// or https:// address of the OSWorld server inside the machine",
                    "examples": ["http://192.168.64.2:5000"],
                    "x-zh": {"title": "服务器地址", "description": "机器里 OSWorld 服务器的 http:// 或 https:// 地址"}},
        },
        "required": ["url"],
    },
    check=_check,
    where=lambda settings: str(settings.get("url") or ""),
    normalize=_normalize,
    address=lambda settings: str(settings.get("url") or ""),
    admit=_admit,
))
registry.offer("osworld", _open)

__all__ = ["KIND", "OSWorldScreen"]
