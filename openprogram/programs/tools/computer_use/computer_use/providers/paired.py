"""Another computer's own OpenProgram, operated through the pairing (connection method ``openprogram``).

This side (sending): a paired machine yields three targets, ``<device>
window``, ``<device> screen`` and ``<device> browser``; a browser call may
name any ``app``. A call to one of them is not checked or run here. The
whole call (type, app, display, title, action, args, actions, code,
timeout) goes over the paired device's credential to the other Worker's
``POST /api/computer-use/call``, which runs ``computer_use`` on that
computer and answers with text, screenshots and whether it failed. The
tool puts ``[device=..., type=...]`` in front, as for any other device.

The list does not ask the other computer what it has. Asking would cost a
round trip to every paired device on every turn, most often to a laptop
that is asleep; and the other computer already answers precisely: a type
it does not have, a browser it cannot reach or a screen it keeps off is
refused there with the targets it does have. So the three types are
listed while the device is switched on, and what is not there comes back
as the answer. A device whose checks have failed for a while
(``status.unreachable``) says so in its block and a call to it is answered
at once, with nothing sent and a fresh check started behind the answer; one
that has only just failed is still tried.

The other side (receiving): ``serve`` runs a forwarded call only while the
owner there has turned on ``computer_use.paired_devices`` ("Let paired
devices operate this computer", off by default). It operates that computer
only, never a device it reaches in turn. That computer's own settings apply:
its whole screen stays off unless turned on there, its operating-system
permissions are its own, and its permission rules for ``computer_use``
are read there: a deny rule refuses, and an ask rule refuses too, since
nobody there is asked. This side asks its own user once per target, with
a rule key that names the device (``studio window safari``).
"""
from __future__ import annotations

import base64
import logging
import threading
import time
from typing import Any

from openprogram.host import machines
from openprogram.host.machines import Machine
from openprogram.runtime.agent.tool_execution import ToolReturn

from .. import errors
from ..routing import registry
from ..contract import Approval, Call, Described, Provider, Row, Steps, refused

_log = logging.getLogger(__name__)

METHOD = "openprogram"
#: Where the other Worker runs a forwarded call.
PATH = "/api/computer-use/call"
#: The setting on the receiving computer that lets paired devices operate it.
SETTING = ("computer_use", "paired_devices")
#: The detail the receiving Worker answers with while that setting is off.
OFF = "paired_devices_off"
#: Seconds to reach one address of the other computer before the next is tried.
CONNECT_SECONDS = 6.0
#: Seconds a check of an offline device stays fresh enough that a call does not start another.
RECHECK_AFTER = 10.0
#: How long a cancelled call still waits for the other computer's answer.
CANCEL_SECONDS = 10.0
# What a call sends; ``device`` never: the other computer operates itself.
_CALL_KEYS = ("type", "app", "display", "title", "action", "args", "actions", "code", "timeout")
_LISTING_ONLY = frozenset({"list_apps", "list_windows", "list_displays", "list_tabs", "guide"})

_LINES = {
    "window": "application windows of that computer (it lists its own with list_apps)",
    "screen": "its whole screen, when that computer has Whole screen on",
    "browser": "OpenProgram's browser there, or with app one of the user's browsers there",
}


class PairedTarget(Provider):
    """One type on another computer's OpenProgram; the call runs there."""

    forwards = True
    state_scope = "conversation"

    def __init__(self, machine_id: str, name: str, family: str, app: str = "") -> None:
        self.machine = machine_id
        self.name = name
        self.family = family
        self.app = app

    def for_app(self, app: str) -> "PairedTarget":
        return PairedTarget(self.machine, self.name, self.family, app)

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        return Described(_LINES[self.family])

    def note(self) -> str:
        return (f"Note: device=\"{self.machine}\" is another computer ({self.name}) running its own OpenProgram. "
                "Each call is sent there whole and runs on that computer, with its applications, its browsers "
                "and its operating system's shortcut keys; paths are its paths. Its answers, errors and the "
                "targets it lists are that computer's. It is operated only while its owner lets paired devices "
                "operate it.")

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        if self.family == "window":
            app = " ".join(str(subject.get("app") or "").split()).lower()
            if not app:
                if steps and all(action in _LISTING_ONLY for action, _ in steps):
                    return None
                return Approval(f"computer_use controls an application on {self.name}")
            return Approval(f"computer_use controls {app} on {self.name}", f"{self.machine} window {app}")
        if self.family == "screen":
            if steps and all(action in _LISTING_ONLY for action, _ in steps):
                return None
            return Approval(f"computer_use controls the whole screen of {self.name}", self.target)
        what = f"your {self.app}" if self.app else "OpenProgram's browser"
        return Approval(f"computer_use controls {what} on {self.name}", self.target)

    def runs_page_script(self, steps: Steps) -> bool:
        return any(action == "evaluate" for action, _ in steps)

    # -- showing --------------------------------------------------------------

    def resource(self, subject: dict) -> Row | None:
        return Row("machine", self.name, self.family)

    # -- forwarding -----------------------------------------------------------

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        return self.forward({"type": self.family, "app": self.app, "action": "get_state"}, call)

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        actions = [{"action": action, "args": dict(step)} for action, step in steps]
        return self.forward({"type": self.family, "app": self.app, "actions": actions}, call)

    def forward(self, call_args: dict, call: Call) -> ToolReturn:
        """Send the whole call to the other computer; its answer, or a coded failure with nothing sent."""
        try:
            return _forward(self, call_args, call)
        except errors.CallError as exc:
            return refused(exc)


def _sent(call_args: dict) -> dict:
    sent = {key: call_args[key] for key in _CALL_KEYS if call_args.get(key) not in (None, "", [], {})}
    return sent


def _forward(target: PairedTarget, call_args: dict, call: Call) -> ToolReturn:
    import httpx

    from openprogram.host.machines import peer, status

    machine = machines.get_machine(target.machine)
    if machine is None or not machine.enabled or not peer.is_paired(machine):
        raise errors.MachineUnreachable(f"device {target.machine!r} was switched off or removed; nothing was sent.")
    offline = status.unreachable(machine)
    if offline is not None:
        # Its checks have failed for a while: answered at once, and checked again behind the answer so a
        # return is noticed (a check already running, or one only seconds old, is not repeated).
        status.state_of(machine, max_age=RECHECK_AFTER)
        raise errors.MachineUnreachable(f"{machine.name} is offline ({offline}); nothing was sent.")
    routes, token = peer.credential(machine)
    if not token:
        raise errors.MachineUnreachable(f"{machine.name} is not paired; pair it again in Settings → Devices.")
    body = _sent(call_args)
    code_seconds = float(body.get("timeout") or 60) if body.get("code") else 0.0
    read_seconds = max(code_seconds, registry.FINISH_SECONDS) + 30
    reasons: list[str] = []
    for route in routes:
        try:
            response = _bounded(lambda: httpx.post(
                f"{route}{PATH}", json=body, headers={"Authorization": f"Bearer {token}"},
                timeout=httpx.Timeout(read_seconds, connect=CONNECT_SECONDS), trust_env=False), call)
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            reasons.append(f"{route}: {exc.__class__.__name__}")
            continue
        except httpx.TimeoutException as exc:
            raise errors.OutcomeUnknown(f"{machine.name} did not answer within {read_seconds:g} s; the call may "
                                        "have run there.") from exc
        except httpx.HTTPError as exc:
            raise errors.OutcomeUnknown(f"the connection to {machine.name} broke ({exc.__class__.__name__}); the "
                                        "call may have run there.") from exc
        return _answer(machine, route, response)
    message = "; ".join(reasons) or "no address"
    status.note(machine, False, message)
    raise errors.MachineUnreachable(f"{machine.name} is offline ({message}); nothing was sent.")


def _answer(machine: Machine, route: str, response) -> ToolReturn:
    from openprogram.host.machines import status

    if response.status_code == 200:
        status.note(machine, True, route)
        data = response.json()
        images = []
        for image in data.get("images") or []:
            try:
                images.append(base64.b64decode(str(image), validate=True))
            except ValueError:
                continue
        return ToolReturn(text=str(data.get("text") or ""), images=images, is_error=bool(data.get("is_error")))
    detail = ""
    try:
        detail = str((response.json() or {}).get("detail") or "")
    except ValueError:
        pass
    if response.status_code == 403 and detail == OFF:
        status.note(machine, True, route)
        raise errors.PermissionMissing(
            f"{machine.name} does not let paired devices operate it; nothing was sent. Its owner turns it on there: "
            "Settings → Devices → This computer → Let paired devices operate this computer.")
    if response.status_code == 401:
        status.note(machine, False, "the other computer revoked this one; pair again")
        raise errors.MachineUnreachable(f"{machine.name} revoked this computer; pair it again in Settings → "
                                        "Devices. Nothing was sent.")
    if response.status_code == 404:
        raise errors.NotSupported(f"the OpenProgram on {machine.name} cannot run computer_use for other devices; "
                                  "update it there. Nothing was sent.")
    raise errors.MachineUnreachable(f"{machine.name} answered {response.status_code}; nothing was sent.")


class _Abandoned(Exception):
    pass


def _bounded(fn, call: Call):
    """``fn()`` on a thread of its own, so a cancelled call stops waiting once ``CANCEL_SECONDS`` pass."""
    box: dict = {}

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - handed back to the caller
            box["error"] = exc

    worker = threading.Thread(target=run, name="computer-use-paired", daemon=True)
    worker.start()
    since = None
    while True:
        worker.join(0.25)
        if not worker.is_alive():
            break
        if since is None and call.cancelled():
            since = time.monotonic()
        if since is not None and time.monotonic() - since >= CANCEL_SECONDS:
            raise errors.OutcomeUnknown("the call was stopped while it ran on the other computer; it may have "
                                        "taken effect there.")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _open(machine: Machine, conversation: str) -> list[Provider]:
    return [PairedTarget(machine.id, machine.name, family) for family in ("window", "screen", "browser")]


registry.offer(METHOD, _open)


# -- the receiving computer ---------------------------------------------------------

def allowed(cfg: dict | None = None) -> bool:
    """Whether the owner of this computer lets paired devices operate it (off by default)."""
    from openprogram.state import config as user_config
    try:
        section = user_config.read_config_value(SETTING[0]) if cfg is None else cfg.get(SETTING[0])
    except Exception:
        return False
    return isinstance(section, dict) and section.get(SETTING[1]) is True


def _ruled_out(call: dict) -> str | None:
    """This computer's own permission rules for the call: a deny rule refuses it, and so does an ask rule,
    since nobody here is asked. A rule names ``computer_use(<key>)`` as in a conversation here."""
    from openprogram.runtime.permissions.rule import parse_rule, pattern_matches
    from openprogram.state import config as user_config

    from .. import tool
    try:
        rules = ((user_config.read_config() or {}).get("tools") or {}).get("permission_rules") or {}
    except Exception:
        rules = {}
    key = tool.rule_key(call)
    for behavior in ("deny", "ask"):
        for raw in rules.get(behavior) or []:
            rule = parse_rule(str(raw))
            if rule.tool_name.lower() != "computer_use":
                continue
            if rule.pattern is None or (key is not None and pattern_matches(rule.pattern, key)):
                if behavior == "deny":
                    return f"this computer's permission rules deny computer_use({key or ''}); nothing was sent."
                return (f"this computer's permission rules ask before computer_use({key or ''}), and nobody here "
                        "is asked for a paired device; allow it in this computer's rules. Nothing was sent.")
    return None


def serve(call: dict, device: str) -> ToolReturn:
    """Run a call another computer forwarded, on this computer only. The caller checked ``allowed`` and the
    device's credential."""
    from .. import tool

    named: dict[str, Any] = {key: call.get(key) for key in _CALL_KEYS if key in call}
    # A forwarded call never hops on: it operates the computer it was sent to.
    named["device"] = ""
    reason = _ruled_out(named)
    if reason:
        return refused(errors.PermissionMissing(reason))
    return tool.run(named, f"paired:{device or 'device'}")


def encoded(result: ToolReturn) -> dict:
    """A result as the wire carries it: text, images in base64, whether it failed."""
    images = [base64.b64encode(image).decode("ascii") if isinstance(image, bytes) else str(image)
              for image in result.images or []]
    return {"text": result.text or "", "images": images, "is_error": bool(result.is_error)}


__all__ = ["METHOD", "OFF", "PATH", "PairedTarget", "SETTING", "allowed", "encoded", "serve"]
