"""Stand-in targets and machines for tests of ``computer_use`` and the code that asks a provider.

Two ways in. ``install`` lists exactly the given providers, for code that
reads a call only through its provider (permission rules, the authority
table, system access, the turn's tool list); ``App``, ``Screen``,
``Browser``, ``UserBrowser`` and ``MachineScreen`` answer the way the real
providers of each kind do. ``install_machines`` replaces the machines
registry with the machines a test names and opens, on this computer and on
devices reached by the test method ``fake``, the ``FakeProvider``s the test
gives, for tests of the registry, the description and dispatch. Nothing reaches a real window, page, machine
or network.
"""
from __future__ import annotations

from typing import Any, Callable

from openprogram.host import machines
from openprogram.host.machines import Connection, Machine, kinds
from openprogram.runtime.agent.tool_execution import ToolReturn
from openprogram.programs.tools.computer_use.computer_use import errors
from openprogram.programs.tools.computer_use.computer_use.routing import registry
from openprogram.programs.tools.computer_use.computer_use.contract import (
    Approval, Call, Described, Provider, Row, Steps,
)
from openprogram.programs.tools.computer_use.computer_use.routing.locks import Locks
from openprogram.programs.tools.computer_use.computer_use import context as cu_context

_INPUT = frozenset({"pointer", "hover", "keyboard"})


def _only(steps, actions) -> bool:
    return bool(steps) and all(action in actions for action, _ in steps)


class App(Provider):
    """Application windows of this computer: asked once per application."""
    family = "window"
    capabilities = _INPUT | {"tree", "pixels", "windows"}

    def describe(self):
        return Described("application windows")

    def extra_actions(self):
        return ("list_apps", "list_windows", "open_file")

    def opening(self, subject):
        return [("get_state", {})] if subject.get("app") else [("list_apps", {})]

    def approval(self, subject, steps):
        if _only(steps, {"list_apps"}):
            return None
        app = " ".join(str(subject.get("app") or "").split()).lower()
        return Approval(f"computer_use controls {app or 'an application'}", f"window {app}" if app else None)

    def system_access(self, subject, steps, code):
        return None if not code and _only(steps, {"list_apps"}) else "computer_use:desktop"


class Screen(Provider):
    """The whole screen of this computer: one grant for every application on it."""
    family = "screen"
    capabilities = _INPUT | {"pixels", "displays"}

    def describe(self):
        return Described("the whole screen")

    def extra_actions(self):
        return ("list_displays",)

    def approval(self, subject, steps):
        if _only(steps, {"list_displays"}):
            return None
        return Approval("computer_use controls the whole screen of this computer", "screen")

    def system_access(self, subject, steps, code):
        return None if not code and _only(steps, {"list_displays"}) else "computer_use:desktop"


class Browser(Provider):
    """OpenProgram's own browser: the conversation's Pages, which their own grant covers."""
    family = "browser"
    capabilities = _INPUT | {"tree", "pixels", "tabs", "script", "upload"}

    def describe(self):
        return Described("this conversation's pages")

    def extra_actions(self):
        return ("list_tabs", "create_tab", "navigate", "upload_file", "evaluate")

    def opening(self, subject):
        return [("list_tabs", {})]

    def approval(self, subject, steps):
        if any(action == "upload_file" for action, _ in steps):
            return Approval("Upload the selected local file to the page")
        return None

    def authority(self, steps, code):
        return "process.exec" if code else "browser.control"

    def runs_page_script(self, steps):
        return any(action == "evaluate" for action, _ in steps)


def user_browsers(*names: str):
    """The browser application's adapter to the user's browsers, as if exactly ``names`` could be reached."""
    from types import SimpleNamespace

    from openprogram.programs.applications.browser import extension
    return SimpleNamespace(**{**{name: getattr(extension, name) for name in extension.__all__},
                              "installed_browsers": lambda: tuple(names)})


class UserBrowser(Browser):
    """The user's own browser (Chrome, Safari, ...), with their sign-ins: asked once, never a Page."""

    def __init__(self, app: str = "chrome", name: str = "Google Chrome") -> None:
        self.app, self.name = app, name

    def opening(self, subject):
        return [("list_tabs", {})]

    def approval(self, subject, steps):
        return Approval(f"computer_use controls your {self.name}", self.target)

    def authority(self, steps, code):
        return "process.exec"


class MachineScreen(Provider):
    """The screen of a machine that was set up: setting it up was the consent."""
    family = "screen"
    capabilities = _INPUT | {"pixels"}

    def __init__(self, machine: str = "lab") -> None:
        self.machine = machine

    def describe(self):
        return Described("a virtual machine")

    def approval(self, subject, steps):
        return Approval(None, self.target)


def install(monkeypatch, *providers: Provider) -> dict[str, Provider]:
    """List exactly these targets, as the machines that are set up would."""
    listed = {provider.target: provider for provider in providers}
    monkeypatch.setattr(registry, "providers", lambda conversation="": dict(listed))
    return listed


def every_kind() -> tuple[Provider, ...]:
    return App(), Screen(), Browser(), UserBrowser("chrome", "Google Chrome"), \
        UserBrowser("edge", "Microsoft Edge"), MachineScreen("lab")


# -- machines and the fake method ---------------------------------------------------

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16

TREE = frozenset({"pixels", "tree", "pointer", "hover", "keyboard"})
PIXELS = frozenset({"pixels", "pointer", "hover", "keyboard"})


class FakeProvider(Provider):
    """A target that records what it is asked and answers in plain text."""

    def __init__(self, family: str, machine: str = "local", *, app: str = "",
                 capabilities: frozenset[str] = TREE, extras: tuple[str, ...] = (), line: str = "",
                 missing: str = "", note: str = "", approval: Callable[[dict, Steps], Approval | None] | None = None,
                 locks: Callable[[dict, Steps], Locks] | None = None, authority: str = "process.exec",
                 page_scripts: tuple[str, ...] = (), claims: Callable[[dict], str] | None = None,
                 act: Callable[[dict, Steps, Call], ToolReturn] | None = None) -> None:
        self.family, self.machine, self.app = family, machine, app
        self.capabilities = capabilities
        self._extras, self._line, self._missing, self._note = extras, line, missing, note
        self._approval, self._locks, self._authority = approval, locks, authority
        self._page_scripts, self._claims, self._act = page_scripts, claims, act
        self.calls: list[tuple] = []

    def describe(self) -> Described:
        return Described(self._line or f"{self.family} of {self.machine}", self._missing)

    def note(self) -> str:
        return self._note

    def extra_actions(self) -> tuple[str, ...]:
        return self._extras

    def list(self, kind: str, subject: dict, args: dict, call: Call) -> ToolReturn:
        self.calls.append(("list", kind, dict(subject), dict(args)))
        return ToolReturn(text=f"{kind} of {self.target}")

    def get_state(self, subject: dict, options: dict, call: Call) -> ToolReturn:
        self.calls.append(("get_state", dict(subject), dict(options), call.images))
        return ToolReturn(text=f"state of {self.target}", images=[PNG] if call.images else [])

    def act(self, subject: dict, steps: Steps, call: Call) -> ToolReturn:
        self.calls.append(("act", dict(subject), [(name, dict(step)) for name, step in steps]))
        if self._act is not None:
            return self._act(subject, steps, call)
        return ToolReturn(text="Done: " + ", ".join(name for name, _ in steps))

    def approval(self, subject: dict, steps: Steps) -> Approval | None:
        return self._approval(subject, steps) if self._approval else None

    def authority(self, steps: Steps, code: bool) -> str:
        return self._authority

    def runs_page_script(self, steps: Steps) -> bool:
        return any(name in self._page_scripts for name, _ in steps)

    def lock(self, subject: dict, steps: Steps) -> Locks:
        return self._locks(subject, steps) if self._locks else Locks()

    def resource(self, subject: dict) -> Row | None:
        return None

    def claims(self, window: dict[str, Any], conversation: str = "") -> str:
        return self._claims(window) if self._claims else ""


def app_approval(subject: dict, steps: Steps) -> Approval | None:
    """As this computer's window target asks: once per application, nothing for listing."""
    application = str(subject.get("app") or "").strip()
    if not application:
        return None
    return Approval(f"computer_use controls {application}", f"window {application.lower()}")


def machine(machine_id: str, name: str = "", method: str = "fake", *, enabled: bool = True, **settings) -> Machine:
    """A device reached by ``method`` (the test method ``fake`` unless named), with ``settings``."""
    return Machine(machine_id, name or machine_id, Connection(method, settings), enabled=enabled)


def local() -> Machine:
    return machine("local", "This computer", "local")


def install_machines(monkeypatch, listed: list[Machine], opened: dict[str, list[Provider]] | None = None,
            failing: tuple[str, ...] = ()) -> None:
    """Make ``listed`` the machines that exist. A device reached by ``local`` or by the test method ``fake``
    yields exactly the providers of ``opened[machine id]``; the other methods keep their real openers. A machine
    in ``failing`` raises when it is opened."""
    opened = opened or {}

    def open_providers(found: Machine, conversation: str) -> list[Provider]:
        if found.id in failing:
            raise RuntimeError("the machine's method could not open")
        return list(opened.get(found.id, []))

    registry.load()  # the real openers and methods first, so the test ones are not replaced
    kinds.all_kinds()
    monkeypatch.setitem(kinds._kinds, "fake", kinds.Kind("fake", "Test device"))
    monkeypatch.setattr(registry, "_openers", {**registry._openers, "local": [open_providers],
                                               "fake": [open_providers]})
    monkeypatch.setattr(machines, "list_machines", lambda cfg=None: list(listed))


def environment(monkeypatch, found: Machine | None) -> None:
    """The machine this conversation's commands run on, as the tool reads it."""
    monkeypatch.setattr(cu_context, "_environment", lambda: found)


__all__ = ["App", "Browser", "FakeProvider", "MachineScreen", "PIXELS", "PNG", "Screen", "TREE", "UserBrowser",
           "app_approval", "environment", "errors", "every_kind", "install", "install_machines", "local", "machine"]
