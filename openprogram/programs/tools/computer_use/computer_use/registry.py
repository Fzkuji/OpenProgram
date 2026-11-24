"""Which targets exist now, and the provider behind each.

A target is listed exactly when one of a machine's connection kinds yields
it: the list follows the set-up (the configured machines, an installed
extension), never a probe. This module walks the machines registry and asks
each window, screen and browser kind to open its providers; nothing here,
and nothing else outside ``providers/``, names a target. Each of those
connections yields targets of one type: ``window``, ``screen`` or ``browser``.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import threading
import time
from typing import Any, Iterator

from openprogram.host import machines
from openprogram.host.machines import Machine
from openprogram.host.machines.kinds import SURFACES

from . import errors, vocabulary
from .contract import TYPES, Call, Provider

_log = logging.getLogger(__name__)



def load() -> None:
    """Import the provider modules, which register their kinds."""
    from . import providers  # noqa: F401


@dataclass(frozen=True)
class Listed:
    """One machine and the targets it yields now."""
    machine: Machine
    providers: tuple[Provider, ...]


def _open(machine: Machine, conversation: str) -> list[Provider]:
    found: list[Provider] = []
    for name in SURFACES:
        kind = machine.kind(name)
        if kind is None or kind.open is None:
            continue
        try:
            found.extend(kind.open(machine, conversation))
        except Exception:
            # A kind that cannot open leaves its targets out; the others stay.
            _log.warning("the %s kind %s of machine %s did not open", name, kind.name, machine.id, exc_info=True)
    return found


def listing(conversation: str = "") -> list[Listed]:
    """Every machine that is switched on, this computer first, with the targets each yields."""
    load()
    rows, seen = [], set()
    for machine in machines.list_machines():
        if not machine.enabled:
            continue
        own = []
        for provider in _open(machine, conversation):
            if provider.target not in seen:
                seen.add(provider.target)
                own.append(provider)
        rows.append(Listed(machine, tuple(own)))
    return rows


def providers(conversation: str = "") -> dict[str, Provider]:
    """Every target set up now, by its key (``Provider.target``)."""
    return {provider.target: provider for row in listing(conversation) for provider in row.providers}


# What a model writes for this computer as a device, beside leaving device out.
_THIS_COMPUTER = frozenset({"", "local", "this computer", "this_computer", "localhost"})
# Every browser a call may name as ``app``, in the order the description lists them. Each is the name of one
# browser of the browser application's catalog (``applications/browser/catalog.py``).
BROWSER_APPS = ("chrome", "chrome-beta", "chrome-dev", "chrome-canary", "edge", "edge-beta", "edge-dev",
                "edge-canary", "brave", "vivaldi", "opera", "arc", "chromium", "safari", "firefox")
# The names a model writes for a browser, by the one ``app`` it means; matched lower-cased, spaces collapsed.
_BROWSERS = {
    **{name: name for name in BROWSER_APPS},
    "google chrome": "chrome", "google-chrome": "chrome", "com.google.chrome": "chrome",
    "google chrome beta": "chrome-beta", "com.google.chrome.beta": "chrome-beta",
    "google chrome dev": "chrome-dev", "com.google.chrome.dev": "chrome-dev", "google-chrome-unstable": "chrome-dev",
    "google chrome canary": "chrome-canary", "com.google.chrome.canary": "chrome-canary",
    "msedge": "edge", "microsoft edge": "edge", "microsoft-edge": "edge", "com.microsoft.edgemac": "edge",
    "msedge-beta": "edge-beta", "microsoft edge beta": "edge-beta", "com.microsoft.edgemac.beta": "edge-beta",
    "msedge-dev": "edge-dev", "microsoft edge dev": "edge-dev", "com.microsoft.edgemac.dev": "edge-dev",
    "msedge-canary": "edge-canary", "microsoft edge canary": "edge-canary",
    "com.microsoft.edgemac.canary": "edge-canary",
    "brave browser": "brave", "brave-browser": "brave", "com.brave.browser": "brave",
    "com.vivaldi.vivaldi": "vivaldi", "opera browser": "opera", "com.operasoftware.opera": "opera",
    "arc browser": "arc", "company.thebrowser.browser": "arc",
    "chromium-browser": "chromium", "org.chromium.chromium": "chromium",
    "apple safari": "safari", "com.apple.safari": "safari",
    "mozilla firefox": "firefox", "firefox browser": "firefox", "org.mozilla.firefox": "firefox",
    "built_in": "", "built-in": "", "built_in_browser": "", "openprogram": ""}


def _device(call: Any) -> str:
    """The machine id a call names, ``local`` for this computer; a machine's name counts as its id."""
    wanted = str((call or {}).get("device") or "").strip()
    if wanted.lower() in _THIS_COMPUTER:
        return "local"
    for machine in machines.list_machines():
        if wanted.lower() in (machine.id.lower(), str(machine.name or "").strip().lower()):
            return machine.id
    return wanted


def browser_name(app: Any) -> str:
    """The browser ``app`` means: one of ``BROWSER_APPS``, empty for the built-in one, or the name lower-cased
    when it names none of them."""
    return _browser(app)


def _browser(app: Any) -> str:
    named = " ".join(str(app or "").split()).lower()
    if named in _BROWSERS:
        return _BROWSERS[named]
    # "Chrome Beta", "chrome_beta" and "chrome beta" all mean chrome-beta.
    joined = named.replace("_", "-").replace(" ", "-")
    return _BROWSERS.get(joined, _BROWSERS.get(named.replace("_", " "), named))


def _listed(found: dict[str, Provider]) -> str:
    return "; ".join(provider.address() for provider in found.values()) or "none"


def resolve(call: Any, conversation: str = "") -> Provider:
    """The provider a call's ``device``, ``type`` and, for a browser, ``app`` name; never another one."""
    call = call if isinstance(call, dict) else {}
    kind = str(call.get("type") or "").strip().lower()
    device = _device(call)
    found = providers(conversation)
    if kind not in TYPES:
        raise errors.UnknownTarget(f"type is one of {', '.join(TYPES)}, not {str(call.get('type') or '')!r}. "
                                   f"Targets now: {_listed(found)}.")
    on_device = [provider for provider in found.values() if provider.machine == device]
    if not on_device:
        where = "this computer" if device == "local" else f"device {str(call.get('device') or '')!r}"
        raise errors.UnknownTarget(f"nothing on {where} can be operated now. Targets now: {_listed(found)}.")
    app = _browser(call.get("app")) if kind == "browser" else ""
    for provider in on_device:
        if provider.family == kind and provider.app == app:
            return provider
    here = {provider.target: provider for provider in on_device}
    if kind == "browser" and app:
        raise errors.UnknownTarget(f"there is no browser {str(call.get('app'))!r} to operate here: app is one of the "
                                   "user's browsers listed below (a Chromium browser with the Playwright Extension "
                                   "installed, Safari on macOS, Firefox where it is installed), or left out for "
                                   f"OpenProgram's own browser. Targets here: {_listed(here)}.")
    raise errors.UnknownTarget(f"type={kind!r} is not available here. Targets here: {_listed(here)}.")


def find(call: Any, conversation: str = "") -> Provider | None:
    try:
        return resolve(call, conversation)
    except errors.UnknownTarget:
        return None


def elsewhere(provider: Provider, conversation: str = ""):
    """For an unknown action: how a call names the other targets of the same device that have it, under its
    name there (``goto`` is ``navigate`` on a browser)."""
    def targets(action: str) -> list[str]:
        return [other.address() for other in providers(conversation).values()
                if other.machine == provider.machine and other.target != provider.target
                and vocabulary.counterpart(other.family, action) in other.surface().actions()]
    return targets


def claim(window: dict[str, Any], conversation: str = "") -> tuple[str, str] | None:
    """The target that operates the content of a local window itself, with what it says of it.

    A surface with its own target is not also operated through the window
    that shows it: ``window`` keeps the window's own controls, leaves the
    claimed content out and names the target.
    """
    for provider in providers(conversation).values():
        try:
            said = provider.claims(window, conversation)
        except Exception:
            said = ""
        if said:
            return provider.address(), said
    return None


# -- what was operated, and closing it ---------------------------------------------

# How long closing a machine waits for the calls running on it: a code call may run for two minutes.
FINISH_SECONDS = 300.0

_activity = threading.Condition()
# Calls running now: (machine, conversation, agent) -> how many.
_running: dict[tuple[str, str, str], int] = {}
# Every target an agent operated and has not closed: (machine, target, conversation, agent) -> its provider.
_operated: dict[tuple[str, str, str, str], Provider] = {}


@contextmanager
def operating(provider: Provider, call: Call) -> Iterator[None]:
    """Mark a call running on its provider's machine, and remember who operated the target, so that ending
    the agent, the conversation or the machine closes what the provider keeps for them.

    The machine is read again here, under the same lock ``release`` takes: a call that resolved its target
    before the machine was switched off or removed is either counted before that, so the release waits for
    it, or refused with nothing sent.
    """
    running = (provider.machine, call.conversation, call.agent)
    with _activity:
        current = machines.get_machine(provider.machine)
        if current is None or not current.enabled:
            raise errors.MachineUnreachable(f"machine {provider.machine!r} was switched off or removed; "
                                            "nothing was sent.")
        _operated[(provider.machine, provider.target, call.conversation, call.agent)] = provider
        _running[running] = _running.get(running, 0) + 1
    try:
        yield
    finally:
        with _activity:
            left = _running.get(running, 1) - 1
            if left > 0:
                _running[running] = left
            else:
                _running.pop(running, None)
            _activity.notify_all()


def release(*, machine: str | None = None, conversation: str | None = None, agent: str | None = None,
            wait: float = FINISH_SECONDS) -> int:
    """Close what providers keep for a machine, a conversation or one agent of it; returns how many closed.

    Calls running on what is released finish first, for up to ``wait``
    seconds. Each provider that was operated is closed once per agent that
    operated it (``Provider.close``), whether or not its machine is still
    configured or switched on: the providers come from what was operated,
    not from the list.
    """
    if machine is None and conversation is None and agent is None:
        return 0

    def covers(owner: str, talk: str) -> bool:
        return (machine is None or owner == machine) and (conversation is None or talk == conversation)

    deadline = time.monotonic() + max(0.0, wait)
    with _activity:
        while any(covers(owner, talk) and agent in (None, who) for owner, talk, who in _running):
            left = deadline - time.monotonic()
            if left <= 0:
                _log.warning("closing %s while calls still run on it", machine or conversation)
                break
            _activity.wait(left)
        chosen = [(key, provider) for key, provider in _operated.items()
                  if covers(key[0], key[2]) and agent in (None, key[3])]
        for key, _ in chosen:
            _operated.pop(key, None)
    for (_, target, talk, who), provider in chosen:
        try:
            provider.close(Call(conversation=talk, agent=who))
        except Exception:
            _log.warning("closing %s for %s failed", target, who or talk, exc_info=True)
    return len(chosen)


__all__ = ["BROWSER_APPS", "FINISH_SECONDS", "Listed", "claim", "elsewhere", "find", "listing", "load", "operating", "providers",
           "release", "resolve"]
