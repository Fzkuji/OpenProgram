"""Connection kinds: how one capability of a machine is reached.

A machine has up to four connections, and each gives one kind of target.
``shell`` runs commands and reads files; ``window`` operates application
windows by their element tree (type ``window``); ``screen`` operates the
whole display with the real pointer (type ``screen``); ``browser`` is a
browser OpenProgram runs there (type ``browser``). Each
connection names a kind, and the kind picks the code: a shell kind builds
the backend commands run through, any other kind opens the providers
``computer_use`` operates.

A kind registers here with its name, the JSON Schema of its settings and
what it gives, once per connection it serves: ``desktop`` serves ``window``
and ``screen`` on this computer, ``osworld`` only ``screen``, because it
has no element tree. Where two connections of one machine name the same
kind and address, the kind keeps one client for both, so the address and
the sign-in are set up once. The parser, the validator, Settings and the
command line read this registry and name no kind themselves, so a new kind
is one module.

Shell kinds are registered by ``openprogram.host.backend``. The other kinds
live above the host, with the tool that uses them; they reach this registry
through ``loaders``: functions that import those modules, added by the
layer that owns them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

CONNECTIONS = ("shell", "window", "screen", "browser", "openprogram")
# The connections that yield targets of ``computer_use``, each one surface.
SURFACES = ("window", "screen", "browser")


class KindError(ValueError):
    """A connection that names an unknown kind or breaks its kind's settings."""


@dataclass(frozen=True)
class Kind:
    name: str
    connection: str
    # What a person calls it: "SSH host", "OSWorld server".
    label: str
    # JSON Schema of the connection object, without its "kind". A property with "secret": true is stored
    # outside the configuration file. The schema and each property may carry "x-zh": {"title",
    # "description"}, the Chinese text a reader shows in place of the English one.
    settings: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # What it gives, declared and never probed.
    capabilities: frozenset[str] = frozenset()
    # Only this computer has it (its own shell, its desktop, the built-in browser).
    local_only: bool = False
    # Shell kinds: one instance for the machine, or one per conversation.
    instance_scope: str = "machine"
    # Shell kinds: file tools go through the shell rather than this computer's disk.
    remote_files: bool = False
    # Shell kinds: ``backend(machine, conversation) -> Backend``.
    backend: Callable[..., Any] | None = None
    # Shell kinds scoped to the conversation: ``remove_instances(machine_id, conversation_id)`` removes
    # the instances labelled with either (both may be given; one is None), whether or not the machine
    # is still configured.
    remove_instances: Callable[[str | None, str | None], None] | None = None
    # Window, screen and browser kinds: ``open(machine, conversation) -> [Provider]``, the targets this
    # connection yields: ``app``, ``screen`` or the browser's.
    open: Callable[..., list] | None = None
    # Machines found without configuration: ``discover() -> [Machine]``.
    discover: Callable[[], list] | None = None
    # ``check(machine) -> (ok, message)``: one quick round trip.
    check: Callable[..., tuple[bool, str]] | None = None
    # ``where(settings) -> str``: what tells one connection of this kind from another ("me@devbox", an image,
    # a URL), the same in every language; never a sentence. A reader shows it after the label.
    where: Callable[[dict], str] | None = None
    # ``normalize(settings) -> settings``: checks the schema cannot express; raises KindError.
    normalize: Callable[[dict], dict] | None = None
    # ``address(settings) -> str``: where the connection reaches, such as ``http://10.0.0.5:5000``, or "".
    # Two machines may not share one window or screen address; the registry compares them after normalizing.
    address: Callable[[dict], str] | None = None
    # ``admit(settings)``: run when a machine other than this computer is saved, with the settings and their
    # secrets. Raises KindError, in one sentence, when the connection reaches this computer, which is built
    # in and never added a second time. It may take a round trip; it never asks the user anything.
    admit: Callable[[dict], None] | None = None
    # The label in Chinese: "SSH 主机".
    label_zh: str = ""

    def secrets(self) -> tuple[str, ...]:
        properties = self.settings.get("properties") or {}
        return tuple(name for name, spec in properties.items() if isinstance(spec, dict) and spec.get("secret"))

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "connection": self.connection, "label": self.label,
                "label_zh": self.label_zh or self.label, "settings": self.settings,
                "capabilities": sorted(self.capabilities), "local_only": self.local_only,
                "instance_scope": self.instance_scope, "remote_files": self.remote_files}


_kinds: dict[tuple[str, str], Kind] = {}
# Functions that import the modules which register kinds; each runs once.
loaders: list[Callable[[], None]] = []
_loaded: set[int] = set()


def register(kind: Kind) -> Kind:
    if kind.connection not in CONNECTIONS:
        raise ValueError(f"connection must be one of {', '.join(CONNECTIONS)}")
    _kinds[(kind.connection, kind.name)] = kind
    return kind


def _load() -> None:
    import openprogram.host.backend  # noqa: F401  (registers the shell kinds)
    import openprogram.host.machines.peer  # noqa: F401  (registers the openprogram kind)
    for loader in list(loaders):
        if id(loader) in _loaded:
            continue
        _loaded.add(id(loader))
        loader()


def get(connection: str, name: str) -> Kind | None:
    _load()
    return _kinds.get((connection, str(name or "")))


def all_kinds(connection: str | None = None) -> list[Kind]:
    _load()
    return [kind for (owner, _), kind in _kinds.items() if connection in (None, owner)]


def validated(connection: str, raw: Any) -> dict[str, Any]:
    """A connection object as stored: ``{"kind": ..., **settings}``, checked against its kind."""
    if not isinstance(raw, dict):
        raise KindError(f"{connection} must be an object with a kind")
    name = str(raw.get("kind") or "").strip().lower()
    kind = get(connection, name)
    if kind is None:
        serves = [item for item in all_kinds() if item.name == name]
        if serves:
            raise KindError(f"Refused: the {serves[0].label} ({name}) cannot be a {connection} connection; it "
                            f"serves {' and '.join(item.connection for item in serves)} only.")
        known = ", ".join(sorted(item.name for item in all_kinds(connection) if not item.local_only))
        raise KindError(f"unknown {connection} kind {name!r}; kinds: {known or 'none'}")
    settings = {key: value for key, value in raw.items() if key != "kind" and value not in (None, "")}
    import jsonschema
    schema = {"additionalProperties": False, **kind.settings}
    try:
        jsonschema.validate(settings, schema)
    except jsonschema.ValidationError as exc:
        where = ".".join(str(part) for part in exc.absolute_path)
        raise KindError(f"{connection} ({kind.label}): {where + ': ' if where else ''}{exc.message}") from None
    if kind.normalize is not None:
        settings = kind.normalize(settings)
    return {"kind": name, **settings}


__all__ = ["CONNECTIONS", "SURFACES", "Kind", "KindError", "all_kinds", "get", "loaders", "register", "validated"]
