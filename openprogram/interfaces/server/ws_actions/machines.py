"""Machine WS actions.

list_machines / save_machine / delete_machine / set_machine_enabled /
test_machine / connect_openprogram

``connect_openprogram`` takes the pairing ``link`` another computer's
Settings → Devices → Add device shows, redeems it there and adds that computer as
a machine reached by the method ``openprogram`` (``host.machines.peer``); its
conversations then appear in this computer's lists.

Replies: ``machines`` (the snapshot: ``items``, each with its one
``connection``, its connection ``status`` and, when commands run on it,
``shell`` with what the shell feature says of it; and ``kinds`` with every
connection method, this computer's own included), ``machine_result`` (``ok``, ``id``, ``error`` or ``machine``)
and ``machine_test`` (``id``, ``ok``, ``message``); both carry the request's
``action`` and ``request_id``.

Machines are the computers OpenProgram reaches (``openprogram.host.machines``);
a project is a folder on one of them, and its conversations run there.
Every change, a machine's connection state included, is broadcast as
``machines`` so Settings, the sidebar and each open composer refresh. A secret setting never leaves the worker: replies carry
its marker.
"""
from __future__ import annotations

import asyncio
import json


def _snapshot() -> dict:
    from openprogram.host import machines
    from openprogram.host.machines.status import state_of
    from openprogram.state.config import read_config
    cfg = read_config()
    items = machines.list_machines(cfg)
    try:
        items += machines.discovered_machines(cfg)
    except Exception:
        pass
    return {
        "items": [_item(machine, state_of(machine)) for machine in items],
        "kinds": [kind.public() for kind in machines.kinds.all_kinds()],
    }


def _item(machine, status: dict) -> dict:
    """One machine as Settings and the pickers read it: the device, then what the shell feature says of it."""
    from openprogram.host.backend import shell_for
    data = {**machine.public(), "status": status}
    shell = shell_for(machine)
    if shell is not None:
        data["shell"] = {"remote_files": shell.remote_files, "instance_scope": shell.instance_scope}
    return data


def _broadcast() -> None:
    from openprogram.interfaces.server import server as _s
    _s._broadcast(json.dumps({"type": "machines", "data": _snapshot()}, default=str))


def _machines_changed() -> None:
    """A machine was added, edited, removed or switched: every page hears it, and each remote OpenProgram
    machine is connected or let go to match."""
    _broadcast()
    from openprogram.interfaces.server.remote_machines.links import sync
    sync()


def _status_changed() -> None:
    try:
        _broadcast()
    except Exception:
        pass


def _listen_for_status() -> None:
    from openprogram.host.machines.status import listeners
    if _status_changed not in listeners:
        listeners.append(_status_changed)


_listen_for_status()


async def _reply(ws, kind: str, data: dict, cmd: dict | None = None) -> None:
    # The answer to a request names it, so a client settles that request and no other.
    if cmd is not None:
        data = {**data, "action": cmd.get("action"), "request_id": cmd.get("request_id")}
    await ws.send_text(json.dumps({"type": kind, "data": data}, default=str))


def _id(cmd: dict) -> str:
    return str(cmd.get("id") or "").strip()


async def handle_list_machines(ws, cmd: dict):
    await _reply(ws, "machines", await asyncio.to_thread(_snapshot))


def _save_machine(payload: dict):
    from openprogram.host.machines import save_machine
    return save_machine(payload)


async def handle_save_machine(ws, cmd: dict):
    from openprogram.host.machines import MachineError
    payload = cmd.get("machine") if isinstance(cmd.get("machine"), dict) else {}
    try:
        # A kind may check its connection with one round trip before it is saved.
        machine = await asyncio.to_thread(_save_machine, payload)
    except MachineError as exc:
        await _reply(ws, "machine_result", {"ok": False, "error": str(exc), "id": payload.get("id")}, cmd)
        return
    await _reply(ws, "machine_result", {"ok": True, "id": machine.id, "machine": machine.public()}, cmd)
    _machines_changed()


async def handle_delete_machine(ws, cmd: dict):
    from openprogram.host.machines import MachineError, delete_machine
    machine_id = _id(cmd)
    try:
        await asyncio.to_thread(delete_machine, machine_id)
    except MachineError as exc:
        await _reply(ws, "machine_result", {"ok": False, "error": str(exc), "id": machine_id}, cmd)
        return
    await _reply(ws, "machine_result", {"ok": True, "id": machine_id}, cmd)
    _machines_changed()


async def handle_set_machine_enabled(ws, cmd: dict):
    from openprogram.host.machines import MachineError, set_enabled
    machine_id = _id(cmd)
    try:
        machine = await asyncio.to_thread(set_enabled, machine_id, cmd.get("enabled") is not False)
    except MachineError as exc:
        await _reply(ws, "machine_result", {"ok": False, "error": str(exc), "id": machine_id}, cmd)
        return
    await _reply(ws, "machine_result", {"ok": True, "id": machine_id, "machine": machine.public()}, cmd)
    _machines_changed()


async def handle_test_machine(ws, cmd: dict):
    from openprogram.host.machines import get_machine
    from openprogram.host.machines.check import check_machine
    machine_id = _id(cmd)
    machine = get_machine(machine_id)
    if machine is None:
        await _reply(ws, "machine_test", {"id": machine_id, "ok": False,
                                          "message": f"Unknown machine {machine_id!r}"}, cmd)
        return
    ok, message = await asyncio.to_thread(check_machine, machine)
    from openprogram.host.machines.status import note
    note(machine, ok, message)
    await _reply(ws, "machine_test", {"id": machine_id, "ok": ok, "message": message}, cmd)


def _connect(link: str, name: str):
    from urllib.parse import urlsplit

    from openprogram.host import machines
    from openprogram.host.machines.peer import redeem
    from openprogram.interfaces.server import server as _s

    own_port = getattr(getattr(_s, "_owner_auth_state", None), "port", None)
    paired = redeem(link, own_port=own_port)
    name = name.strip() or (urlsplit(paired["url"]).hostname or "OpenProgram").split(".", 1)[0]
    connection = {"kind": "openprogram", "url": paired["url"], "token": paired["token"],
                  "device_id": paired["device_id"]}
    if paired["routes"]:
        connection["routes"] = paired["routes"]
    return machines.save_machine({"name": name, "connection": connection})


async def handle_connect_openprogram(ws, cmd: dict):
    from openprogram.host.machines import MachineError
    from openprogram.host.machines.peer import PairingError
    try:
        machine = await asyncio.to_thread(_connect, str(cmd.get("link") or ""), str(cmd.get("name") or ""))
    except (PairingError, MachineError) as exc:
        await _reply(ws, "machine_result", {"ok": False, "error": str(exc)}, cmd)
        return
    await _reply(ws, "machine_result", {"ok": True, "id": machine.id, "machine": machine.public()}, cmd)
    _machines_changed()


async def handle_list_ssh_hosts(ws, cmd: dict):
    """The concrete hosts in ~/.ssh/config, for Add device → SSH server."""
    from openprogram.host.backend.ssh_config import config_hosts
    hosts = await asyncio.to_thread(config_hosts)
    await _reply(ws, "ssh_hosts", {"hosts": hosts}, cmd)


ACTIONS = {
    "connect_openprogram": handle_connect_openprogram,
    "list_ssh_hosts": handle_list_ssh_hosts,
    "list_machines": handle_list_machines,
    "save_machine": handle_save_machine,
    "delete_machine": handle_delete_machine,
    "set_machine_enabled": handle_set_machine_enabled,
    "test_machine": handle_test_machine,
}
