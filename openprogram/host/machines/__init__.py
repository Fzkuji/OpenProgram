"""Machines: the computers OpenProgram reaches, and the one a conversation runs on.

A machine is a computer with its own operating system: this computer, an
SSH host, a Docker machine, a virtual machine, a phone. It has up to four
connections (``openprogram.host.machines.kinds``), each giving one kind of
target: ``shell`` runs commands and reads files, ``window`` operates
application windows (type ``window``), ``screen`` operates the whole display
(type ``screen``), ``browser`` is a browser OpenProgram runs there
(type ``browser``). A fifth, ``openprogram``, is another computer's own
OpenProgram (``peer``): its conversations are listed beside this computer's
and forwarded there, never copied. One kind may serve several connections; two
connections of one machine with the same kind and address share one client.

This computer is built in as ``local`` and never stored. Its connections
are ``shell: local``, ``window: desktop`` and ``browser: built_in``; it has
a ``screen`` connection (``desktop``) only while ``computer_use.whole_screen``
is on, which it is not by default. Users add more in ``config.json``::

    "machines": {
      "local": {"window": {"kind": "desktop"}, "browser": {"kind": "built_in"}},
      "items": [
        {"id": "devbox", "name": "Dev box",
         "shell": {"kind": "ssh", "target": "me@devbox", "workdir": "/srv/app"}},
        {"id": "ubuntu", "name": "Ubuntu",
         "shell": {"kind": "docker", "image": "ubuntu:24.04"}},
        {"id": "lab", "name": "Lab VM",
         "screen": {"kind": "osworld", "url": "http://192.168.64.2:5000"}}
      ]
    }

Two bindings sit on this one registry and stay apart:

* The **environment** is a role: the machine a conversation's commands and
  file tools run on. It needs a shell. The conversation's project decides
  it: a project is a folder on one machine (``machine_id`` on the project
  record), and every conversation in it runs on that machine, in that
  folder. The home project is always this computer. A project whose machine
  was removed, switched off or has no shell is refused, never replaced by
  this computer.
* The **targets** of ``computer_use`` are listed per turn and chosen
  per call, from every machine's window, screen and browser connections. Being the
  environment never makes a machine the default target.

Channels are a third axis: a channel decides where a conversation's messages
are mirrored, never where its tools execute.

Design: docs/reference/design/integrations/computer-use/machines-and-targets.html
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import logging
import re
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

from openprogram.host.machines import here, kinds
from openprogram.host.machines.kinds import CONNECTIONS, Kind, KindError
from openprogram.state import config as user_config


_log = logging.getLogger(__name__)

LOCAL_ID = "local"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
# What this computer's connections are when the configuration names none; its screen only while
# ``computer_use.whole_screen`` is on.
_LOCAL_KINDS = {"shell": "local", "window": "desktop", "screen": "desktop", "browser": "built_in"}
# The connections whose kind may be replaced on this computer, and those that reach an address another
# machine may not share.
_LOCAL_EDITABLE = ("window", "screen", "browser")
_ADDRESSED = ("window", "screen")
# What a stored secret leaves in the configuration file.
SECRET_MARKER = {"secret": True}
# What ``Machine.public`` adds to each connection for display; never stored.
_DISPLAY_KEYS = ("label", "label_zh", "where", "known")
_DEFAULT_PORTS = {"http": 80, "https": 443, "vnc": 5900, "ssh": 22}


class MachineError(ValueError):
    """A request names a machine that is invalid, missing, or cannot do what is asked of it."""


@dataclass(frozen=True)
class Connection:
    kind: str
    settings: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)

    def get(self, key: str, default: Any = "") -> Any:
        value = self.settings.get(key)
        return default if value in (None, "") else value

    def raw(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.settings}


@dataclass(frozen=True)
class Machine:
    id: str
    name: str
    enabled: bool = True
    shell: Optional[Connection] = None
    window: Optional[Connection] = None
    screen: Optional[Connection] = None
    browser: Optional[Connection] = None
    # Another computer's OpenProgram (``peer``): its conversations are listed here and forwarded there.
    openprogram: Optional[Connection] = None
    # Found without configuration (a booted simulator), never stored.
    discovered: bool = False

    def connection(self, name: str) -> Optional[Connection]:
        return getattr(self, name) if name in CONNECTIONS else None

    def kind(self, name: str) -> Optional[Kind]:
        """The registered kind of one connection, or None when it has none or the kind is not loaded."""
        connection = self.connection(name)
        return kinds.get(name, connection.kind) if connection is not None else None

    @property
    def is_local(self) -> bool:
        """This computer, where the worker and the App run."""
        return self.id == LOCAL_ID

    @property
    def has_shell(self) -> bool:
        return self.shell is not None

    @property
    def remote_shell(self) -> bool:
        """Commands run somewhere other than this computer's own shell."""
        return self.shell is not None and not self.is_local

    @property
    def remote_files(self) -> bool:
        """File tools must go through the shell, not this computer's disk."""
        kind = self.kind("shell")
        return bool(kind and kind.remote_files)

    @property
    def instance_scope(self) -> str:
        """``machine`` when there is one of it, ``conversation`` when each conversation gets its own."""
        kind = self.kind("shell")
        return kind.instance_scope if kind else "machine"

    def instance(self, conversation: Optional[str] = None) -> str:
        """The key of the instance a conversation uses: what state and locks are kept under."""
        if self.instance_scope == "conversation" and conversation:
            return f"{self.id}/{conversation}"
        return self.id

    def where(self, name: str) -> str:
        """The detail that tells one connection of a kind from another ("me@devbox", an image), or "".

        It is the same in every language; a reader puts it after the kind's label.
        """
        connection, kind = self.connection(name), self.kind(name)
        if connection is None or kind is None or kind.where is None:
            return ""
        return str(kind.where(dict(connection.settings)) or "")

    def describe(self, name: str) -> str:
        """One connection in English, for the model and the command line: "SSH host me@devbox"."""
        connection, kind = self.connection(name), self.kind(name)
        if connection is None:
            return ""
        return " ".join(part for part in (kind.label if kind else connection.kind, self.where(name)) if part)

    def public(self) -> dict[str, Any]:
        """The machine as clients read it: secrets stay markers."""
        data: dict[str, Any] = {"id": self.id, "name": self.name, "enabled": self.enabled}
        for name in CONNECTIONS:
            connection, kind = self.connection(name), self.kind(name)
            if connection is None:
                continue
            raw = connection.raw()
            for key in (kind.secrets() if kind is not None else ()):
                if key in raw:
                    raw[key] = dict(SECRET_MARKER)
            data[name] = {**raw, "label": kind.label if kind else connection.kind,
                          "label_zh": (kind.label_zh or kind.label) if kind else connection.kind,
                          "where": self.where(name), "known": kind is not None}
        if self.discovered:
            data["discovered"] = True
        return data


# ---- configuration -----------------------------------------------------------


def _section(cfg: Optional[dict[str, Any]]) -> dict[str, Any]:
    section = user_config.read_config_value("machines") if cfg is None else cfg.get("machines")
    return section if isinstance(section, dict) else {}


def _connection(raw: Any) -> Optional[Connection]:
    """A stored connection, kept as written: an unknown kind is carried, never dropped."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        return None
    return Connection(kind, {key: value for key, value in raw.items() if key != "kind"})


def _keeps_local_targets(name: str, kind_name: str) -> bool:
    """A kind this computer's window, screen or browser may be: one that is this computer's own, so its window,
    screen and browser are reached without a device. Any other kind would give this computer a second name."""
    kind = kinds.get(name, kind_name)
    return bool(kind and kind.local_only)


def whole_screen(cfg: Optional[dict[str, Any]] = None) -> bool:
    """Whether the owner turned this computer's whole screen on (``computer_use.whole_screen``); off by default.

    Off, this computer has no screen connection, so the target ``screen`` is neither listed nor callable:
    an ordinary task needs one application, not the whole screen.
    """
    try:
        settings = user_config.read_config_value("computer_use") if cfg is None else cfg.get("computer_use")
    except Exception:
        return False
    return isinstance(settings, dict) and settings.get("whole_screen") is True


def _local(section: dict[str, Any], screen_on: bool) -> Machine:
    raw = section.get("local") if isinstance(section.get("local"), dict) else {}
    connections = {}
    for name, default in _LOCAL_KINDS.items():
        if name == "screen" and not screen_on:
            continue
        connection = _connection(raw.get(name)) if name != "shell" else None
        if connection is not None and not _keeps_local_targets(name, connection.kind):
            connection = None
        connections[name] = connection or Connection(default)
    return Machine(LOCAL_ID, "This computer", **connections)


def _from_item(raw: Any) -> Optional[Machine]:
    if not isinstance(raw, dict):
        return None
    machine_id = str(raw.get("id") or "").strip()
    if not _ID_RE.match(machine_id) or machine_id == LOCAL_ID:
        return None
    connections = {name: _connection(raw.get(name)) for name in CONNECTIONS}
    if not any(connections.values()):
        return None
    return Machine(machine_id, str(raw.get("name") or machine_id).strip() or machine_id,
                   enabled=raw.get("enabled") is not False, **connections)


def list_machines(cfg: Optional[dict[str, Any]] = None) -> list[Machine]:
    """This computer first, then the configured machines in their order, switched-off ones included."""
    section = _section(cfg)
    items: list[Machine] = [_local(section, whole_screen(cfg))]
    seen = {LOCAL_ID}
    for raw in section.get("items") or []:
        machine = _from_item(raw)
        if machine is not None and machine.id not in seen:
            items.append(machine)
            seen.add(machine.id)
    return items


def get_machine(machine_id: Optional[str], cfg: Optional[dict[str, Any]] = None) -> Optional[Machine]:
    if not machine_id:
        return None
    for machine in list_machines(cfg):
        if machine.id == machine_id:
            return machine
    return None


def local_machine(cfg: Optional[dict[str, Any]] = None) -> Machine:
    return _local(_section(cfg), whole_screen(cfg))


def discovered_machines(cfg: Optional[dict[str, Any]] = None) -> list[Machine]:
    """Machines the kinds find without configuration (a booted simulator), never stored.

    A kind's ``discover()`` may cost a process or two, so this is asked
    where a list is built for a person or a turn, not on every tool call.
    A discovered machine whose id a configured one already uses is left out.
    """
    taken = {machine.id for machine in list_machines(cfg)}
    found: list[Machine] = []
    for kind in kinds.all_kinds():
        if kind.discover is None:
            continue
        try:
            machines = list(kind.discover())
        except Exception:
            _log.warning("discovery by the %s kind %s failed", kind.connection, kind.name, exc_info=True)
            continue
        for machine in machines:
            if machine.id in taken:
                continue
            taken.add(machine.id)
            found.append(dataclasses.replace(machine, discovered=True))
    return found


def usable_as_environment(machine: Optional[Machine]) -> bool:
    """A conversation can run on it: it is switched on and has a shell whose kind is loaded."""
    return bool(machine and machine.enabled and machine.has_shell and machine.kind("shell") is not None)


def list_environments(cfg: Optional[dict[str, Any]] = None) -> list[Machine]:
    """The machines a project can be on, this computer first."""
    return [machine for machine in list_machines(cfg) if usable_as_environment(machine)]


def get_environment(machine_id: Optional[str], cfg: Optional[dict[str, Any]] = None) -> Optional[Machine]:
    machine = get_machine(machine_id, cfg)
    return machine if usable_as_environment(machine) else None


# ---- secrets -------------------------------------------------------------------


def _secrets_path(machine_id: str):
    from openprogram.state.paths import get_state_dir
    return get_state_dir() / "auth" / "machines" / f"{machine_id}.json"


def _read_secrets(machine_id: str) -> dict[str, str]:
    try:
        data = json.loads(_secrets_path(machine_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_secrets(machine_id: str, values: dict[str, str]) -> None:
    from openprogram.models.auth.credentials.io import _private_atomic_write
    path = _secrets_path(machine_id)
    if not values:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(values).encode("utf-8")
    _private_atomic_write(path, lambda handle: handle.write(data), root=path.parent)


def secret(machine: Machine, connection: str, key: str) -> str:
    """A secret setting of one connection, read from the state directory's ``auth/machines``."""
    return str(_read_secrets(machine.id).get(f"{connection}.{key}") or "")


# ---- changes -------------------------------------------------------------------


def _stored_item(machine_id: str) -> dict[str, Any]:
    section = _section(None)
    for raw in section.get("items") or []:
        if isinstance(raw, dict) and raw.get("id") == machine_id:
            return raw
    return {}


def _normalize_connection(name: str, raw: dict[str, Any], stored: Any,
                          held: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """One connection to store, and its secrets, from what a client sent.

    A secret setting left out or sent back as the marker keeps the stored
    value. A connection whose kind this process does not have loaded is
    kept exactly as stored when the client sends it back unchanged, so a
    process that knows fewer kinds never rewrites what another wrote.
    """
    kind_name = str(raw.get("kind") or "").strip().lower()
    kind = kinds.get(name, kind_name)
    # A client may send back what ``Machine.public`` added for display.
    own = set((kind.settings.get("properties") or {}) if kind is not None else ())
    raw = {key: value for key, value in raw.items() if key not in _DISPLAY_KEYS or key in own}
    if kind is None and isinstance(stored, dict) and str(stored.get("kind") or "").lower() == kind_name:
        unchanged = {key: value for key, value in raw.items() if value != SECRET_MARKER} ==             {key: value for key, value in stored.items() if value != SECRET_MARKER}
        if unchanged:
            kept = {key: value for key, value in held.items() if key.startswith(f"{name}.")}
            return dict(stored), kept
    if kind is not None and kind.local_only:
        raise MachineError(f"{kind.label} is this computer's own {name}; another machine cannot use it")
    hidden = kind.secrets() if kind is not None else ()
    full = {key: value for key, value in raw.items() if key not in hidden}
    for key in hidden:
        value = raw.get(key)
        if isinstance(value, str) and value:
            full[key] = value
        elif value in (None, "", SECRET_MARKER) and held.get(f"{name}.{key}"):
            full[key] = held[f"{name}.{key}"]
    try:
        stored_now = kinds.validated(name, full)
    except KindError as exc:
        raise MachineError(str(exc)) from None
    secrets: dict[str, str] = {}
    for key in hidden:
        value = stored_now.get(key)
        if isinstance(value, str) and value:
            secrets[f"{name}.{key}"] = value
            stored_now[key] = dict(SECRET_MARKER)
    return stored_now, secrets


def _normalize_payload(payload: dict[str, Any], held: dict[str, str],
                       stored: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """The item to store and the secrets to keep beside it, from what a client sent."""
    machine_id = str(payload.get("id") or "").strip().lower()
    if machine_id == LOCAL_ID:
        raise MachineError("'local' is this computer; it is built in and cannot be changed")
    if not _ID_RE.match(machine_id):
        raise MachineError("id must be 1-48 characters: lowercase letters, digits, '-' or '_'")
    item: dict[str, Any] = {"id": machine_id, "name": str(payload.get("name") or "").strip() or machine_id}
    if payload.get("enabled") is False:
        item["enabled"] = False
    secrets: dict[str, str] = {}
    for name in CONNECTIONS:
        raw = payload.get(name)
        if raw in (None, "", {}):
            continue
        if not isinstance(raw, dict):
            raise MachineError(f"{name} must be an object with a kind")
        item[name], found = _normalize_connection(name, raw, stored.get(name), held)
        secrets.update(found)
    if not any(name in item for name in CONNECTIONS):
        raise MachineError("a machine needs at least one connection: shell, window, screen, browser or openprogram")
    return item, secrets


def _writable(cfg: dict[str, Any]) -> dict[str, Any]:
    section = cfg.get("machines")
    if not isinstance(section, dict):
        section = cfg["machines"] = {}
    if not isinstance(section.get("items"), list):
        section["items"] = []
    return section


def new_machine_id(name: str) -> str:
    """An id for a machine added without one: its name in id form, made unique."""
    base = re.sub(r"[^a-z0-9_-]+", "-", str(name or "").strip().lower()).strip("-_")[:40] or "machine"
    taken = {machine.id for machine in list_machines()} | {machine.id for machine in _discovered_safely()}
    candidate, number = base, 2
    while candidate in taken:
        candidate, number = f"{base}-{number}", number + 1
    return candidate


def _normal_address(address: Any) -> str:
    """An address as two machines' addresses are compared: lower-case, without scheme, path or a default port,
    and every loopback spelling the same."""
    text = str(address or "").strip().lower()
    if not text:
        return ""
    parts = urlsplit(text if "://" in text else f"//{text}")
    try:
        port = parts.port
    except ValueError:
        port = None
    host = parts.hostname or text
    if port is None and parts.scheme in _DEFAULT_PORTS:
        port = _DEFAULT_PORTS[parts.scheme]
    if here.is_loopback(host):
        host = "localhost"
    return f"{host}:{port}" if port is not None else host


def connection_address(machine: Machine, name: str) -> str:
    """Where one connection of a machine reaches, as its kind says, normalized; "" when it does not say."""
    connection, kind = machine.connection(name), machine.kind(name)
    if connection is None or kind is None or kind.address is None:
        return ""
    try:
        return _normal_address(kind.address(dict(connection.settings)))
    except Exception:
        return ""


def screen_address(machine: Machine) -> str:
    """Where a machine's screen connection reaches, normalized; "" when it does not say."""
    return connection_address(machine, "screen")


def _refuse_shared_screen(machine_id: str, item: dict[str, Any]) -> None:
    """Two machines may not share one window or screen address: they would be one screen with two names.

    The window and the screen connection of one machine may share an address: one server serving both.
    """
    for name in _ADDRESSED:
        raw = item.get(name)
        if not isinstance(raw, dict):
            continue
        kind = kinds.get(name, str(raw.get("kind") or ""))
        if kind is None or kind.address is None:
            continue
        address = _normal_address(kind.address({key: value for key, value in raw.items() if key != "kind"}))
        if not address:
            continue
        for other in list_machines():
            if other.id != machine_id and address in {connection_address(other, own) for own in _ADDRESSED}:
                whose = "this computer" if other.is_local else f"machine {other.id!r}"
                raise MachineError(f"Refused: {whose} already has the screen address {address}, and two "
                                   "machines may not share one screen address.")


def _admit(item: dict[str, Any], secrets: dict[str, str]) -> None:
    """Ask each connection's kind whether it reaches this computer, which is built in and never added again."""
    for name in CONNECTIONS:
        raw = item.get(name)
        kind = kinds.get(name, str(raw.get("kind") or "")) if isinstance(raw, dict) else None
        if kind is None or kind.admit is None:
            continue
        settings = {key: value for key, value in raw.items() if key != "kind" and value != SECRET_MARKER}
        settings.update({key.split(".", 1)[1]: value for key, value in secrets.items()
                         if key.startswith(f"{name}.")})
        try:
            kind.admit(settings)
        except KindError as exc:
            raise MachineError(str(exc)) from None


def save_machine(payload: dict[str, Any]) -> Machine:
    """Create or replace one machine (matched by id). Its id never changes; only its name is edited.

    A payload without an id adds a machine, with an id made from its name.
    A machine that is this computer under another name is refused: an SSH
    target that resolves here, a screen another machine already has, a
    screen that shows this computer's own display.
    """
    if not str(payload.get("id") or "").strip():
        if not str(payload.get("name") or "").strip():
            raise MachineError("a machine needs a name")
        payload = {**payload, "id": new_machine_id(payload["name"])}
    machine_id = str(payload.get("id") or "").strip().lower()
    known = _ID_RE.match(machine_id) is not None
    item, secrets = _normalize_payload(payload, _read_secrets(machine_id) if known else {},
                                       _stored_item(machine_id) if known else {})
    if any(found.id == item["id"] for found in _discovered_safely()):
        raise MachineError(f"{item['id']!r} is the name of a discovered device; choose another id")
    _refuse_shared_screen(item["id"], item)
    _admit(item, secrets)

    def mutate(cfg: dict[str, Any]) -> None:
        section = _writable(cfg)
        kept = [raw for raw in section["items"] if not (isinstance(raw, dict) and raw.get("id") == item["id"])]
        position = next((index for index, raw in enumerate(section["items"])
                         if isinstance(raw, dict) and raw.get("id") == item["id"]), len(kept))
        kept.insert(position, item)
        section["items"] = kept

    cfg = user_config.update_config(mutate)
    _write_secrets(item["id"], secrets)
    machine = get_machine(item["id"], cfg)
    assert machine is not None
    if not machine.enabled:
        _remove_instances_later(machine.id)
    return machine


def _discovered_safely() -> list[Machine]:
    try:
        return discovered_machines()
    except Exception:
        return []


def delete_machine(machine_id: str, *, wait: bool = False) -> None:
    """Remove a machine; its instances go too, in the background unless ``wait`` (a CLI process would
    exit before a background removal ends)."""
    if machine_id == LOCAL_ID:
        raise MachineError("'local' is this computer; it is built in and cannot be removed")

    def mutate(cfg: dict[str, Any]) -> None:
        section = _writable(cfg)
        section["items"] = [raw for raw in section["items"]
                            if not (isinstance(raw, dict) and raw.get("id") == machine_id)]

    user_config.update_config(mutate)
    _write_secrets(machine_id, {})
    if wait:
        remove_instances(machine=machine_id)
    else:
        _remove_instances_later(machine_id)


def set_enabled(machine_id: str, enabled: bool, *, wait: bool = False) -> Machine:
    """Switch a machine on or off. Off, it yields no targets and no conversation starts on it; its
    instances go, in the background unless ``wait``."""
    if machine_id == LOCAL_ID:
        raise MachineError("this computer cannot be switched off")
    if get_machine(machine_id) is None:
        raise MachineError(f"unknown machine {machine_id!r}")

    def mutate(cfg: dict[str, Any]) -> None:
        section = _writable(cfg)
        for raw in section["items"]:
            if isinstance(raw, dict) and raw.get("id") == machine_id:
                if enabled:
                    raw.pop("enabled", None)
                else:
                    raw["enabled"] = False

    machine = get_machine(machine_id, user_config.update_config(mutate))
    assert machine is not None
    if not enabled:
        if wait:
            remove_instances(machine=machine_id)
        else:
            _remove_instances_later(machine_id)
    return machine


# Run first when a machine is switched off or removed (``machine``) and when a conversation is deleted
# (``conversation``), as ``releaser(machine=..., conversation=...)``: the layers above that keep state
# for a machine or a conversation let running calls finish and close it. Filled by those layers when they
# load (``computer_use`` closes its providers); the host imports none of them.
releasers: list[Callable[..., None]] = []
# How long a machine switched off or removed waits for the commands running on it: the bound an action
# call of ``computer_use`` has, so nothing a call started outlives the removal.
RELEASE_WAIT = 300.0


def remove_instances(*, machine: Optional[str] = None, conversation: Optional[str] = None) -> None:
    """Release what a machine or a conversation holds, then remove its instances of shell kinds scoped to
    the conversation, by their labels.

    Asked when a conversation is deleted, and when a machine is deleted or
    switched off; it finds them whether or not the machine is still configured.
    It may wait for running calls, so it runs off the caller's thread.
    """
    if not machine and not conversation:
        return
    if machine and _back(machine):
        return
    for release in list(releasers):
        try:
            release(machine=machine, conversation=conversation)
        except Exception:
            _log.warning("releasing %s failed", machine or conversation, exc_info=True)
    if machine:
        # Commands already running on it finish first; ``current_environment`` refuses new ones meanwhile.
        from openprogram.host.backend import wait_idle
        if not wait_idle(machine, RELEASE_WAIT):
            _log.warning("commands on machine %s still ran after %.0f s; its instances are removed anyway",
                         machine, RELEASE_WAIT)
        if _back(machine):
            # Switched on or added again while its commands finished: what runs now on it is in use.
            return
        shell_kinds = kinds.all_kinds("shell")
    else:
        # A conversation has instances only on a machine configured now (a machine's removal took its
        # own): no ``docker ps`` per conversation for someone without a Docker machine.
        used = {m.shell.kind for m in list_machines() if m.shell is not None}
        shell_kinds = [kind for kind in kinds.all_kinds("shell") if kind.name in used]
    for kind in shell_kinds:
        if kind.remove_instances is None:
            continue
        try:
            kind.remove_instances(machine, conversation)
        except Exception:
            _log.warning("removing the %s instances of %s failed", kind.name, machine or conversation,
                         exc_info=True)


def _back(machine_id: str) -> bool:
    """The machine is configured and on again: a removal asked before is stale."""
    machine = get_machine(machine_id)
    return machine is not None and machine.enabled


def _remove_instances_later(machine_id: str) -> None:
    """Remove a machine's instances in the background: a container can take seconds to stop."""
    threading.Thread(target=remove_instances, kwargs={"machine": machine_id},
                     name=f"remove-instances-{machine_id}", daemon=True).start()


def set_local_connection(name: str, raw: Optional[dict[str, Any]]) -> Machine:
    """Replace this computer's window, screen or browser connection (``None`` puts the built-in one back).

    Only a kind that is this computer's own is accepted, so its window, screen and browser
    are reached without a device; any other kind is a machine of
    its own. The screen connection is used only while ``computer_use.whole_screen`` is on.
    """
    if name not in _LOCAL_EDITABLE:
        raise MachineError("this computer's shell cannot be replaced")
    stored = None
    if raw:
        try:
            stored = kinds.validated(name, raw)
        except KindError as exc:
            raise MachineError(str(exc)) from None
        if not _keeps_local_targets(name, stored["kind"]):
            kind = kinds.get(name, stored["kind"])
            raise MachineError(f"Refused: this computer's {name} can only be its own; add the "
                               f"{kind.label if kind else stored['kind']} as a machine of its own.")
        if stored == {"kind": _LOCAL_KINDS[name]}:
            # The built-in one, as it is when nothing is stored.
            stored = None
        elif name in _ADDRESSED:
            _refuse_shared_screen(LOCAL_ID, {name: stored})

    def mutate(cfg: dict[str, Any]) -> None:
        section = _writable(cfg)
        local = section.get("local") if isinstance(section.get("local"), dict) else {}
        if stored is None:
            local.pop(name, None)
        else:
            local[name] = stored
        if local:
            section["local"] = local
        else:
            section.pop("local", None)

    return local_machine(user_config.update_config(mutate))


def _not_an_environment(machine: Machine) -> str:
    if not machine.enabled:
        return f"machine {machine.id!r} is switched off"
    if not machine.has_shell:
        return f"machine {machine.id!r} has no shell, so no conversation can run on it"
    return f"machine {machine.id!r} uses a shell kind this installation does not have ({machine.shell.kind})"


# ---- projects --------------------------------------------------------------


def _project_for_session(session_id: Optional[str]):
    """The session's project record, the home project when it names none; None outside a store."""
    try:
        from openprogram.state.store.project import project_store as projects
        if session_id:
            found = projects.project_for_session(session_id)
            if found is not None:
                return found
        return projects.get_project(projects.DEFAULT_PROJECT_ID)
    except Exception:
        return None


def project_machine_id(project) -> str:
    """The machine a project's folder is on: ``local`` for the home project and for a record that names none."""
    if project is None or getattr(project, "is_default", False):
        return LOCAL_ID
    return str(getattr(project, "machine_id", "") or "").strip() or LOCAL_ID


def resolve_for_project(project) -> Machine:
    """The machine a project's conversations run on.

    Raises :class:`MachineError` when the project names a machine that was
    removed, switched off or has lost its shell: running its commands
    somewhere else would be worse than refusing.
    """
    machine_id = project_machine_id(project)
    if machine_id == LOCAL_ID:
        return local_machine()
    return _pinned(machine_id, f"project {getattr(project, 'name', '') or machine_id!r}")


def resolve_for_session(session_id: Optional[str]) -> Machine:
    """The machine a session's commands and file tools run on: its project's."""
    return resolve_for_project(_project_for_session(session_id))


def _pinned(machine_id: str, owner: str) -> Machine:
    """A project's machine as configured now; refused when it cannot run commands."""
    machine = get_machine(machine_id)
    if machine is None:
        raise MachineError(
            f"{owner} is on machine {machine_id!r}, which no longer exists. "
            "Add it back in Settings > Machines.")
    if not usable_as_environment(machine):
        raise MachineError(
            f"{owner} is on machine {machine_id!r}, but {_not_an_environment(machine)}. "
            "Change it in Settings > Machines.")
    return machine


# ---- per-turn binding ------------------------------------------------------


_current_environment: ContextVar[Optional[Machine]] = ContextVar("_current_environment", default=None)


def bind_turn_environment(machine: Optional[Machine]):
    return _current_environment.set(machine)


def reset_turn_environment(token) -> None:
    with contextlib.suppress(Exception):
        _current_environment.reset(token)


def still_usable(machine: Machine) -> Machine:
    """The machine as configured now; raises :class:`MachineError` when it was switched off or removed.

    A turn or a call holds the machine it started with; this is read again
    before anything is sent to it, so a machine switched off mid-turn takes
    no further work. This computer and discovered machines always pass.
    """
    if machine.is_local or machine.discovered:
        return machine
    current = get_machine(machine.id)
    if current is None:
        raise MachineError(f"machine {machine.id!r} was removed")
    if not current.enabled:
        raise MachineError(f"machine {machine.id!r} is switched off")
    return current


def current_environment() -> Machine:
    """The machine the running tool call's commands run on.

    A turn binds its session's environment; outside a turn the current
    session's project decides, and with no session this computer. A bound machine
    that was switched off, removed or lost its shell since is refused with
    :class:`MachineError`, never replaced by this computer.
    """
    machine = _current_environment.get()
    if machine is not None:
        if not machine.is_local:
            _pinned(machine.id, "this conversation's project")
        return machine
    from openprogram.state.execution_facts import current_session_id
    return resolve_for_session(current_session_id())


__all__ = [
    "CONNECTIONS",
    "Connection",
    "Kind",
    "KindError",
    "LOCAL_ID",
    "Machine",
    "MachineError",
    "SECRET_MARKER",
    "bind_turn_environment",
    "connection_address",
    "current_environment",
    "delete_machine",
    "discovered_machines",
    "get_environment",
    "get_machine",
    "kinds",
    "list_environments",
    "list_machines",
    "local_machine",
    "new_machine_id",
    "releasers",
    "remove_instances",
    "reset_turn_environment",
    "project_machine_id",
    "resolve_for_project",
    "resolve_for_session",
    "save_machine",
    "screen_address",
    "secret",
    "still_usable",
    "set_enabled",
    "set_local_connection",
    "usable_as_environment",
    "whole_screen",
]
