"""Connection methods: how a device is reached.

A device has exactly one connection, and its kind is the connection method:
``local`` (this computer), ``ssh``, ``docker``, ``openprogram`` (another
computer's own OpenProgram, paired), ``osworld`` (a virtual machine behind
an OSWorld server). A method registers here with its name, the JSON Schema
of its settings and the few things every reader of a device needs: a
label, a reachability check, what tells one connection from another, and
whether the address reaches this computer.

A method says nothing about what can be done once connected. Each feature
decides that from the method itself: the shell feature
(``openprogram.host.backend``) runs commands through ``local``, ``ssh`` and
``docker``; ``computer_use`` operates this computer's windows, screen and
browsers through ``local``, a virtual machine's screen through ``osworld``
and another computer through ``openprogram``. So a new feature needs no
change here, and a new method is one module.

Methods are registered by the modules that know them: ``local`` here,
``ssh`` and ``docker`` by ``shells`` (they run commands through
``openprogram.host.backend``), ``openprogram`` by ``peer``. Methods that live above the host (``osworld``, with the tool that
uses it) reach this registry through ``loaders``: functions that import
those modules, added by the layer that owns them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


class KindError(ValueError):
    """A connection that names an unknown method or breaks its method's settings."""


@dataclass(frozen=True)
class Kind:
    """One connection method."""
    name: str
    # What a person calls it: "SSH host", "OSWorld server".
    label: str
    # JSON Schema of the connection object, without its "kind". A property with "secret": true is stored
    # outside the configuration file. The schema and each property may carry "x-zh": {"title",
    # "description"}, the Chinese text a reader shows in place of the English one.
    settings: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # Only this computer is reached this way (``local``): no device is added with it.
    local_only: bool = False
    # Devices found without configuration: ``discover() -> [Machine]``.
    discover: Callable[[], list] | None = None
    # ``check(machine) -> (ok, message)``: one quick round trip.
    check: Callable[..., tuple[bool, str]] | None = None
    # ``where(settings) -> str``: what tells one connection of this method from another ("me@devbox", an
    # image, a URL), the same in every language; never a sentence. A reader shows it after the label.
    where: Callable[[dict], str] | None = None
    # ``normalize(settings) -> settings``: checks the schema cannot express; raises KindError.
    normalize: Callable[[dict], dict] | None = None
    # ``address(settings) -> str``: the screen the connection reaches, such as ``http://10.0.0.5:5000``, or "".
    # Two devices may not share one such address; the registry compares them after normalizing.
    address: Callable[[dict], str] | None = None
    # ``admit(settings)``: run when a device is saved, with the settings and their secrets. Raises
    # KindError, in one sentence, when the connection reaches this computer, which is built in and never
    # added a second time. It may take a round trip; it never asks the user anything.
    admit: Callable[[dict], None] | None = None
    # The label in Chinese: "SSH 主机".
    label_zh: str = ""
    # Added another way than the form (``openprogram`` is added by pairing): never offered there.
    paired: bool = False

    def secrets(self) -> tuple[str, ...]:
        properties = self.settings.get("properties") or {}
        return tuple(name for name, spec in properties.items() if isinstance(spec, dict) and spec.get("secret"))

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "label": self.label, "label_zh": self.label_zh or self.label,
                "settings": self.settings, "local_only": self.local_only, "paired": self.paired}


_kinds: dict[str, Kind] = {}
# Functions that import the modules which register methods; each runs once.
loaders: list[Callable[[], None]] = []
_loaded: set[int] = set()


def register(kind: Kind) -> Kind:
    _kinds[kind.name] = kind
    return kind


register(Kind(name="local", label="This computer", label_zh="这台电脑", local_only=True,
              check=lambda machine: (True, "This computer")))


def _load() -> None:
    import openprogram.host.machines.shells  # noqa: F401  (registers ssh and docker)
    import openprogram.host.machines.peer  # noqa: F401  (registers openprogram)
    for loader in list(loaders):
        if id(loader) in _loaded:
            continue
        _loaded.add(id(loader))
        loader()


def get(name: str) -> Kind | None:
    _load()
    return _kinds.get(str(name or ""))


def all_kinds() -> list[Kind]:
    _load()
    return list(_kinds.values())


def validated(raw: Any) -> dict[str, Any]:
    """A connection object as stored: ``{"kind": ..., **settings}``, checked against its method."""
    if not isinstance(raw, dict):
        raise KindError("connection must be an object with a kind")
    name = str(raw.get("kind") or "").strip().lower()
    kind = get(name)
    if kind is None:
        known = ", ".join(sorted(item.name for item in all_kinds() if not item.local_only))
        raise KindError(f"unknown connection method {name!r}; methods: {known or 'none'}")
    settings = {key: value for key, value in raw.items() if key != "kind" and value not in (None, "")}
    import jsonschema
    schema = {"additionalProperties": False, **kind.settings}
    try:
        jsonschema.validate(settings, schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path)
        raise KindError(f"{kind.label}: {where + ': ' if where else ''}{exc.message}") from None
    if kind.normalize is not None:
        settings = kind.normalize(settings)
    return {"kind": name, **settings}


__all__ = ["Kind", "KindError", "all_kinds", "get", "loaders", "register", "validated"]
