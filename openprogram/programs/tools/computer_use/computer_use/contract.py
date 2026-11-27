"""The contract every target is operated through.

What ``computer_use`` offers on a device's connection method
(``registry.offer``) opens one provider per target it yields there. The tool, the permission rules, code mode
and the Resources panel reach a target only through its provider: no module
outside ``providers/`` names a target or branches on one.

A provider is cheap to make and keeps nothing itself; what it remembers
between calls (element ids, the last screenshot, tabs) lives in its module,
keyed by the agent or the conversation as ``state_scope`` says.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Iterable

from openprogram.runtime.agent.tool_execution import ToolReturn

from . import errors
from .routing.locks import Locks
from .actions.vocabulary import Surface

Steps = list[tuple[str, dict]]


@dataclass(frozen=True)
class Approval:
    """What a call asks the user once: the sentence shown, and the key its answer is remembered under.

    ``rule`` is what a permission rule is written for (``window Calculator``,
    ``screen``, ``browser chrome``, ``lab screen``). A question without a
    rule is asked each time. A rule without a question (``shown`` is None)
    asks nothing, and the owner's own rules can still match it: a machine
    that was set up is operated without asking, and a deny rule can name it.
    """
    shown: str | None = None
    rule: str | None = None


@dataclass(frozen=True)
class Row:
    """One row of the conversation's Resources: what a call is operating."""
    kind: str
    title: str
    detail: str = ""


@dataclass
class Call:
    """One call as a provider sees it: who makes it, how long it may take, whether it was cancelled."""
    conversation: str
    agent: str
    deadline: float | None = None
    # Raises when the call was cancelled or stopped.
    check_cancelled: Callable[[], None] | None = None
    # False when the caller reads text only (code): states come without a screenshot.
    images: bool = True

    def check(self) -> None:
        """Raise when the call was cancelled, or ``TIMEOUT:`` once its deadline has passed."""
        if self.check_cancelled is not None:
            self.check_cancelled()
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise errors.Timeout("the call ran out of time")

    def cancelled(self) -> bool:
        try:
            self.check()
        except errors.Timeout:
            return False
        except BaseException:
            return True
        return False


@dataclass(frozen=True)
class Described:
    """What a provider says of its target without asking anything: no prompt, no network."""
    # The target's line of the tool description, after "- <target>: ".
    line: str
    # What stands in the way of using it now, with where to fix it (a missing permission), or "".
    missing: str = ""


# What a call's ``type`` names: an application's windows, a whole display, or a browser's pages.
TYPES = ("window", "screen", "browser")


class Provider:
    """One target on one device: a ``type`` (its ``family``) on a machine, and for a browser which one.

    Subclasses set ``family`` (the type, and the vocabulary), ``machine``
    (the machine's id; ``local`` for this computer), ``app`` for a browser
    other than OpenProgram's own, and ``capabilities``, and answer the methods
    below. ``subject`` is the part of the call's arguments that says what
    inside the target: the family's shared arguments, already in our names.
    """

    family: str = ""
    machine: str = "local"
    # The browser a browser target drives (``chrome``, ``edge``); "" for OpenProgram's own.
    app: str = ""
    capabilities: frozenset[str] = frozenset()
    # Whether what the provider remembers between calls belongs to the agent or to the conversation.
    state_scope: str = "agent"
    # The whole call goes to another computer, which checks and runs it there (``forward``): this side checks
    # no action against the vocabulary and holds no lock.
    forwards: bool = False

    @property
    def device(self) -> str:
        """The device a call names, "" for this computer."""
        return "" if self.machine == "local" else self.machine

    @property
    def target(self) -> str:
        """The target's key: its device unless it is this computer, its type, its browser (``browser chrome``,
        ``lab screen``). Locks, what was operated and the permission rules of a whole target use it."""
        return " ".join(part for part in (self.device, self.family, self.app) if part)

    def addressed(self) -> dict[str, str]:
        """The arguments of a call that name the target: ``{"type": "browser", "app": "chrome"}``."""
        named = {"device": self.device, "type": self.family, "app": self.app}
        return {key: value for key, value in named.items() if value}

    def address(self) -> str:
        """How a call names the target, as the model writes it: ``type="browser", app="chrome"``."""
        return ", ".join(f'{key}="{value}"' for key, value in self.addressed().items())

    # -- what the model reads -------------------------------------------------

    def describe(self) -> Described:
        raise NotImplementedError

    def note(self) -> str:
        """The target note: its machine, operating system, what it lacks, its shortcut keys."""
        return ""

    def extra_actions(self) -> tuple[str, ...]:
        """The actions of the family this target has beyond the core ones."""
        return ()

    def surface(self, elsewhere: Callable[[str], Iterable[str]] | None = None) -> Surface:
        return Surface(self.address(), self.family, self.capabilities, self.extra_actions(), elsewhere)

    # -- operating ------------------------------------------------------------

    def opening(self, subject: dict) -> Steps:
        """The steps of a call that names the target and nothing to do: its current state."""
        return [("get_state", {})]

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        """Applications, windows, tabs, displays or panes."""
        raise errors.NotSupported(f"{self.address()} has nothing to list as {kind}")

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        """The state text and, unless the caller reads text only, a screenshot."""
        raise NotImplementedError

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        """The steps in order, stopping at the first failure and never repeating a step that was sent."""
        raise NotImplementedError

    # -- permission -----------------------------------------------------------

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        """What these steps ask the user, or nothing."""
        return None

    def authority(self, steps: Steps, code: bool) -> str:
        """The capability the call needs (``openprogram.state.authority``): ``browser.control`` for a call
        confined to the conversation's own pages, else ``process.exec``."""
        return "process.exec"

    def system_access(self, subject: dict, steps: Steps, code: bool) -> str | None:
        """The operating-system access the call needs on this computer, as an operation of
        ``openprogram.host.system_access`` (``computer_use:desktop``), or None."""
        return None

    def runs_page_script(self, steps: Steps) -> bool:
        """Whether a step runs code inside a page, which reads all a signed-in page holds."""
        return False

    def lock(self, subject: dict, steps: Steps) -> Locks:
        """The keys the call holds while it runs. A provider that locks inside its driver returns none."""
        return Locks()

    # -- showing --------------------------------------------------------------

    def resource(self, subject: dict) -> Row | None:
        """The shared row, listed only while a call operates this subject, or nothing. A subject the
        conversation created is listed by ``created()`` and returns None here: operating it adds no second row."""
        return None

    def created(self, conversation: str) -> list[dict[str, Any]]:
        """The rows this conversation created here and still owns, read from the provider's own store."""
        return []

    def claims(self, window: dict[str, Any], conversation: str = "") -> str:
        """The part of a local window this target operates itself for ``conversation`` ("" for none), so
        ``app`` leaves it out."""
        return ""

    def environment_note(self, machine: str, targets: set[str]) -> str:
        """What this target's line adds when the conversation's shell runs on another machine, ``machine``,
        given the targets listed there ("" for nothing)."""
        return ""

    def frames(self, subject: dict):
        """A live view of the subject, only where the source pushes frames."""
        return None

    def close(self, call: Call) -> None:
        """The agent or the conversation ended: drop what was kept for it."""

    # -- forwarding (``forwards``) ----------------------------------------------

    def for_app(self, app: str) -> "Provider":
        """The same target with the browser a call names (a forwarding provider takes any)."""
        return self

    def forward(self, call_args: dict, call: Call) -> ToolReturn:
        """Run the whole call (type, app, display, title, action, args, actions, code, timeout) elsewhere."""
        raise NotImplementedError


@dataclass
class Result:
    """The pieces of an answer a provider builds before it becomes a ``ToolReturn``."""
    text: str
    images: list[bytes] = field(default_factory=list)
    data: Any = None
    failed: bool = False

    def tool_return(self) -> ToolReturn:
        return ToolReturn(text=self.text, images=self.images, json_data=self.data, is_error=self.failed)


def refused(error: errors.CallError) -> ToolReturn:
    """A coded failure as the model reads it. One that sent nothing says so, for code mode's step record."""
    data = None if error.sent else {"action_dispatched": False}
    return ToolReturn(text=error.text(), is_error=True, json_data=data)


__all__ = ["Approval", "Call", "Described", "Provider", "Result", "Row", "Steps", "TYPES", "refused"]
