"""Machines: the devices OpenProgram reaches, and the one a conversation runs on.

A device is a computer with its own operating system: this computer, an
SSH host, a Docker machine, a virtual machine, another computer's own
OpenProgram. It is an id, a name, a switch, and exactly one connection:
how it is reached. The connection's kind is its connection method
(``openprogram.host.machines.kinds``): ``local``, ``ssh``, ``docker``,
``openprogram`` or ``osworld``.

The device layer knows nothing about what can be done once connected. Each
feature decides that from the method: commands and files
(``openprogram.host.backend``) run through ``local``, ``ssh`` and
``docker``; ``computer_use`` operates this computer's windows, screen and
browsers through ``local``, a virtual machine's screen through ``osworld``
and another computer through ``openprogram``; another computer's
conversations are listed here and forwarded there through ``openprogram``
(``peer``).

This computer is built in as ``local``, reached by the method ``local``,
and never stored. Users add more in ``config.json``::

    "machines": {
      "items": [
        {"id": "devbox", "name": "Dev box",
         "connection": {"kind": "ssh", "target": "me@devbox", "workdir": "/srv/app"}},
        {"id": "ubuntu", "name": "Ubuntu",
         "connection": {"kind": "docker", "image": "ubuntu:24.04"}},
        {"id": "lab", "name": "Lab VM",
         "connection": {"kind": "osworld", "url": "http://192.168.64.2:5000"}}
      ]
    }

Two bindings sit on this one registry and stay apart:

* The **environment** is a role: the machine a conversation's commands and
  file tools run on. It needs a method the shell feature runs commands
  through. The conversation's project decides it: a project is a folder on
  one machine (``machine_id`` on the project record), and every
  conversation in it runs on that machine, in that folder. The home
  project is always this computer. A project whose machine was removed,
  switched off or cannot run commands is refused, never replaced by this
  computer.
* The **targets** of ``computer_use`` are listed per turn and chosen per
  call, from what ``computer_use`` offers on each device. Being the
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

from openprogram.host.backend import docker as _docker
from openprogram.host.machines import here, kinds
from openprogram.host.machines.kinds import Kind, KindError
from openprogram.state import config as user_config


_log = logging.getLogger(__name__)

LOCAL_ID = "local"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
# What a stored secret leaves in the configuration file.
SECRET_MARKER = {"secret": True}
# What ``Machine.public`` adds to the connection for display; never stored.
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
    # How it is reached; its kind is the connection method.
    connection: Optional[Connection] = None
    enabled: bool = True
    # Found without configuration (a booted simulator), never stored.
    discovered: bool = False

    @property
    def method(self) -> str:
        """The connection method's name: ``local``, ``ssh``, ``docker``, ``openprogram``, ``osworld``."""
        return self.connection.kind if self.connection is not None else ""

    @property
    def kind(self) -> Optional[Kind]:
        """The registered connection method, or None when this installation does not have it loaded."""
        return kinds.get(self.method) if self.connection is not None else None

    @property
    def is_local(self) -> bool:
        """This computer, where the worker and the App run."""
        return self.id == LOCAL_ID

    # The shell feature's answers for this device. ``openprogram.host.backend`` owns which methods run
    # commands and how; these only ask it.

    @property
    def has_shell(self) -> bool:
        return _shell(self) is not None

    @property
    def remote_shell(self) -> bool:
        """Commands run somewhere other than this computer's own shell."""
        return self.has_shell and not self.is_local

    @property
    def remote_files(self) -> bool:
        """File tools must go through the shell, not this computer's disk."""
        shell = _shell(self)
        return bool(shell and shell.remote_files)

    @property
    def instance_scope(self) -> str:
        """``machine`` when there is one of it, ``conversation`` when each conversation gets its own."""
        shell = _shell(self)
        return shell.instance_scope if shell else "machine"

    def instance(self, conversation: Optional[str] = None) -> str:
        """The key of the instance a conversation uses: what state and locks are kept under."""
        if self.instance_scope == "conversation" and conversation:
            return f"{self.id}/{conversation}"
        return self.id

    def where(self) -> str:
        """The detail that tells this connection from another of its method ("me@devbox", an image), or "".

        It is the same in every language; a reader puts it after the method's label.
        """
        kind = self.kind
        if self.connection is None or kind is None or kind.where is None:
            return ""
        return str(kind.where(dict(self.connection.settings)) or "")

    def describe(self) -> str:
        """The connection in English, for the model and the command line: "SSH host me@devbox"."""
        if self.connection is None:
            return ""
        kind = self.kind
        return " ".join(part for part in (kind.label if kind else self.connection.kind, self.where()) if part)

    def public(self) -> dict[str, Any]:
        """The machine as clients read it: secrets stay markers."""
        data: dict[str, Any] = {"id": self.id, "name": self.name, "enabled": self.enabled}
        if self.connection is not None:
            kind = self.kind
            raw = self.connection.raw()
            for key in (kind.secrets() if kind is not None else ()):
                if key in raw:
                    raw[key] = dict(SECRET_MARKER)
            data["connection"] = {**raw, "label": kind.label if kind else self.connection.kind,
                                  "label_zh": (kind.label_zh or kind.label) if kind else self.connection.kind,
                                  "where": self.where(), "known": kind is not None}
        if self.discovered:
            data["discovered"] = True
        return data


def _shell(machine: Machine):
    from openprogram.host.backend import shell_for
    return shell_for(machine)


# ---- configuration -----------------------------------------------------------


def _section(cfg: Optional[dict[str, Any]]) -> dict[str, Any]:
    section = user_config.read_config_value("machines") if cfg is None else cfg.get("machines")
    return section if isinstance(section, dict) else {}


def _connection(raw: Any) -> Optional[Connection]:
    """A stored connection, kept as written: an unknown method is carried, never dropped."""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("kind") or "").strip().lower()
    if not kind:
        return None
    return Connection(kind, {key: value for key, value in raw.items() if key != "kind"})


def _local() -> Machine:
    return Machine(LOCAL_ID, "This computer", Connection("local"))


def _from_item(raw: Any) -> Optional[Machine]:
    if not isinstance(raw, dict):
        return None
    machine_id = str(raw.get("id") or "").strip()
    if not _ID_RE.match(machine_id) or machine_id == LOCAL_ID:
        return None
    connection = _connection(raw.get("connection"))
    if connection is None:
        return None
    return Machine(machine_id, str(raw.get("name") or machine_id).strip() or machine_id, connection,
                   enabled=raw.get("enabled") is not False)


def list_machines(cfg: Optional[dict[str, Any]] = None) -> list[Machine]:
    """This computer first, then the configured machines in their order, switched-off ones included."""
    section = _section(cfg)
    items: list[Machine] = [_local()]
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
    return _local()


def discovered_machines(cfg: Optional[dict[str, Any]] = None) -> list[Machine]:
    """Machines the methods find without configuration (a booted simulator), never stored.

    A method's ``discover()`` may cost a process or two, so this is asked
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
            _log.warning("discovery by the method %s failed", kind.name, exc_info=True)
            continue
        for machine in machines:
            if machine.id in taken:
                continue
            taken.add(machine.id)
            found.append(dataclasses.replace(machine, discovered=True))
    return found


def usable_as_environment(machine: Optional[Machine]) -> bool:
    """A conversation can run on it: it is switched on and the shell feature runs commands through its method."""
    return bool(machine and machine.enabled and machine.has_shell)


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
    from openprogram.state.private_files import _private_atomic_write
    path = _secrets_path(machine_id)
    if not values:
        with contextlib.suppress(OSError):
            path.unlink()
        return
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    data = json.dumps(values).encode("utf-8")
    _private_atomic_write(path, lambda handle: handle.write(data), root=path.parent)


def secret(machine: Machine, key: str) -> str:
    """A secret setting of the machine's connection, read from the state directory's ``auth/machines``."""
    return str(_read_secrets(machine.id).get(key) or "")


# ---- changes -------------------------------------------------------------------


def _stored_item(machine_id: str) -> dict[str, Any]:
    section = _section(None)
    for raw in section.get("items") or []:
        if isinstance(raw, dict) and raw.get("id") == machine_id:
            return raw
    return {}


def _normalize_connection(raw: dict[str, Any], stored: Any,
                          held: dict[str, str]) -> tuple[dict[str, Any], dict[str, str]]:
    """The connection to store, and its secrets, from what a client sent.

    A secret setting left out or sent back as the marker keeps the stored
    value. A connection whose method this process does not have loaded is
    kept exactly as stored when the client sends it back unchanged, so a
    process that knows fewer methods never rewrites what another wrote.
    """
    kind_name = str(raw.get("kind") or "").strip().lower()
    kind = kinds.get(kind_name)
    # A client may send back what ``Machine.public`` added for display.
    own = set((kind.settings.get("properties") or {}) if kind is not None else ())
    raw = {key: value for key, value in raw.items() if key not in _DISPLAY_KEYS or key in own}
    if kind is None and isinstance(stored, dict) and str(stored.get("kind") or "").lower() == kind_name:
        unchanged = {key: value for key, value in raw.items() if value != SECRET_MARKER} == \
            {key: value for key, value in stored.items() if value != SECRET_MARKER}
        if unchanged:
            return dict(stored), dict(held)
    if kind is not None and kind.local_only:
        raise MachineError(f"{kind.label} is how this computer is reached; another machine cannot use it")
    hidden = kind.secrets() if kind is not None else ()
    full = {key: value for key, value in raw.items() if key not in hidden}
    for key in hidden:
        value = raw.get(key)
        if isinstance(value, str) and value:
            full[key] = value
        elif value in (None, "", SECRET_MARKER) and held.get(key):
            full[key] = held[key]
    try:
        stored_now = kinds.validated(full)
    except KindError as exc:
        raise MachineError(str(exc)) from None
    secrets: dict[str, str] = {}
    for key in hidden:
        value = stored_now.get(key)
        if isinstance(value, str) and value:
            secrets[key] = value
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
    raw = payload.get("connection")
    if raw in (None, "", {}):
        raise MachineError("a machine needs a connection: how it is reached (ssh, docker, osworld, ...)")
    if not isinstance(raw, dict):
        raise MachineError("connection must be an object with a kind")
    item["connection"], secrets = _normalize_connection(raw, stored.get("connection"), held)
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


def connection_address(machine: Machine) -> str:
    """The screen a machine's connection reaches, as its method says, normalized; "" when it does not say."""
    kind = machine.kind
    if machine.connection is None or kind is None or kind.address is None:
        return ""
    try:
        return _normal_address(kind.address(dict(machine.connection.settings)))
    except Exception:
        return ""


def _refuse_shared_screen(machine_id: str, item: dict[str, Any]) -> None:
    """Two machines may not share one screen address: they would be one screen with two names."""
    raw = item.get("connection")
    if not isinstance(raw, dict):
        return
    kind = kinds.get(str(raw.get("kind") or ""))
    if kind is None or kind.address is None:
        return
    address = _normal_address(kind.address({key: value for key, value in raw.items() if key != "kind"}))
    if not address:
        return
    for other in list_machines():
        if other.id != machine_id and address == connection_address(other):
            raise MachineError(f"Refused: machine {other.id!r} already has the screen address {address}, and two "
                               "machines may not share one screen address.")


def _admit(item: dict[str, Any], secrets: dict[str, str]) -> None:
    """Ask the connection's method whether it reaches this computer, which is built in and never added again."""
    raw = item.get("connection")
    kind = kinds.get(str(raw.get("kind") or "")) if isinstance(raw, dict) else None
    if kind is None or kind.admit is None:
        return
    settings = {key: value for key, value in raw.items() if key != "kind" and value != SECRET_MARKER}
    settings.update(secrets)
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
    """Switch a machine on or off. Off, no feature uses it and no conversation starts on it; its
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
    """Release what a machine or a conversation holds, then remove the shell feature's instances scoped to
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
    from openprogram.host.backend import SHELLS, wait_idle
    if machine:
        # Commands already running on it finish first; ``current_environment`` refuses new ones meanwhile.
        if not wait_idle(machine, RELEASE_WAIT):
            _log.warning("commands on machine %s still ran after %.0f s; its instances are removed anyway",
                         machine, RELEASE_WAIT)
        if _back(machine):
            # Switched on or added again while its commands finished: what runs now on it is in use.
            return
        methods = list(SHELLS)
    else:
        # A conversation has instances only on a machine configured now (a machine's removal took its
        # own): no ``docker ps`` per conversation for someone without a Docker machine.
        methods = sorted({m.method for m in list_machines() if m.method in SHELLS})
    for method in methods:
        remove = SHELLS[method].remove_instances
        if remove is None:
            continue
        try:
            remove(machine, conversation)
        except Exception:
            _log.warning("removing the %s instances of %s failed", method, machine or conversation,
                         exc_info=True)


def _back(machine_id: str) -> bool:
    """The machine is configured and on again: a removal asked before is stale."""
    machine = get_machine(machine_id)
    return machine is not None and machine.enabled


def _remove_instances_later(machine_id: str) -> None:
    """Remove a machine's instances in the background: a container can take seconds to stop."""
    threading.Thread(target=remove_instances, kwargs={"machine": machine_id},
                     name=f"remove-instances-{machine_id}", daemon=True).start()


def _not_an_environment(machine: Machine) -> str:
    if not machine.enabled:
        return f"machine {machine.id!r} is switched off"
    if machine.kind is None:
        return f"machine {machine.id!r} uses a connection method this installation does not have ({machine.method})"
    return f"machine {machine.id!r} is reached through {machine.kind.label}, which runs no commands"


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
    removed, switched off or cannot run commands: running its commands
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
            "Add it back in Settings > Devices.")
    if not usable_as_environment(machine):
        raise MachineError(
            f"{owner} is on machine {machine_id!r}, but {_not_an_environment(machine)}. "
            "Change it in Settings > Devices.")
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
    that was switched off, removed or can no longer run commands is refused with
    :class:`MachineError`, never replaced by this computer.
    """
    machine = _current_environment.get()
    if machine is not None:
        if not machine.is_local:
            _pinned(machine.id, "this conversation's project")
        return machine
    from openprogram.state.execution_facts import current_session_id
    return resolve_for_session(current_session_id())


def get_active_backend():
    """The backend for the running session's environment."""
    from openprogram.host.backend import UnavailableBackend, backend_for
    try:
        machine = current_environment()
    except MachineError as exc:
        return UnavailableBackend("unavailable", str(exc))
    from openprogram.state.execution_facts import current_session_id
    session_id = current_session_id()
    try:
        return backend_for(machine, session_id)
    except Exception as exc:
        return UnavailableBackend(machine.method or "unavailable", f"machine {machine.id!r}: {exc}")


def _container_refusal(machine_id: str) -> str | None:
    """Why no container may start for ``machine_id`` now, or None."""
    machine = get_machine(machine_id)
    if machine is None:
        return f"machine {machine_id!r} was removed"
    if not machine.enabled:
        return f"machine {machine_id!r} was switched off"
    return None


# A backend is bound to a machine only by a machine from here, so the Docker
# backend asks this before it starts a machine's container.
_docker.refuse_containers_with(_container_refusal)

__all__ = [
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
    "get_active_backend",
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
    "secret",
    "still_usable",
    "set_enabled",
    "usable_as_environment",
]
