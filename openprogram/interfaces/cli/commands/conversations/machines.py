"""``openprogram machines`` — the computers OpenProgram reaches.

A machine has one connection: how it is reached. Its kind is the
connection method (ssh, docker, osworld, ...). This command names no
method: ``add`` takes the method followed by ``KEY=VALUE`` settings, and
the method's own schema checks them.
"""
from __future__ import annotations

import json


def _load_kinds() -> None:
    """Make the methods registered above the host known (osworld): they register when Programs load."""
    try:
        import openprogram.programs  # noqa: F401
    except Exception:
        pass


def _print_list(show_kinds: bool) -> int:
    from openprogram.host import machines
    print(f"{'id':16} {'state':5} {'name':20} connection")
    for machine in machines.list_machines() + machines.discovered_machines():
        state = "on" if machine.enabled else "off"
        print(f"{machine.id:16} {state:5} {machine.name[:20]:20} {machine.describe()}")
    if show_kinds:
        print("\nConnection methods:")
        for kind in machines.kinds.all_kinds():
            if kind.local_only or kind.paired:
                continue
            properties = kind.settings.get("properties") or {}
            required = set(kind.settings.get("required") or ())
            fields = ", ".join(
                f"{key}{'' if key in required else '?'}" + (" (secret)" if spec.get("secret") else "")
                for key, spec in properties.items() if isinstance(spec, dict))
            print(f"  {kind.name:14} {kind.label}" + (f": {fields}" if fields else ""))
    return 0


def _value(raw: str, spec: dict):
    """A setting typed as its schema says; a string otherwise."""
    wanted = spec.get("type") if isinstance(spec, dict) else None
    if wanted in ("integer", "number", "boolean", "array", "object"):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def connection_from_args(words: list[str] | None) -> dict:
    """The connection object ``add`` builds: the method, then ``KEY=VALUE`` settings."""
    from openprogram.host.machines import MachineError, kinds
    if not words:
        raise MachineError("name how the machine is reached: METHOD, then its KEY=VALUE settings")
    kind_name, settings = words[0].strip().lower(), words[1:]
    stored: dict = {"kind": kind_name}
    kind = kinds.get(kind_name)
    properties = (kind.settings.get("properties") or {}) if kind is not None else {}
    for item in settings:
        key, sep, raw = item.partition("=")
        if not sep or not key.strip():
            raise MachineError(f"settings are KEY=VALUE, not {item!r}")
        stored[key.strip()] = _value(raw, properties.get(key.strip()) or {})
    return stored


def dispatch(args) -> int:
    from openprogram.host.machines import (
        MachineError,
        delete_machine,
        get_machine,
        save_machine,
        set_enabled,
    )
    _load_kinds()
    verb = getattr(args, "machines_verb", None)
    if verb in (None, "list"):
        return _print_list(bool(getattr(args, "kinds", False)))
    try:
        if verb == "add":
            payload = {"id": args.id, "name": args.name, "connection": connection_from_args(args.connection)}
            machine = save_machine(payload)
            print(f"Saved machine {machine.id!r}: {machine.describe()}")
            return 0
        if verb in ("remove", "enable", "disable", "test") and get_machine(args.id) is None:
            print(f"[error] unknown machine {args.id!r}")
            return 1
        if verb == "remove":
            # This process exits next: the containers go before it does, not in a thread it would cut off.
            delete_machine(args.id, wait=True)
            print(f"Removed {args.id!r}. Projects on it stop running until it is added back.")
            return 0
        if verb in ("enable", "disable"):
            set_enabled(args.id, verb == "enable", wait=True)
            print(f"Machine {args.id!r} is {'on' if verb == 'enable' else 'off'}.")
            return 0
        if verb == "test":
            from openprogram.host.machines.check import check_machine
            ok, message = check_machine(get_machine(args.id))
            print(("ok: " if ok else "failed: ") + message)
            return 0 if ok else 1
    except MachineError as exc:
        print(f"[error] {exc}")
        return 1
    print(f"[error] unknown verb {verb!r}")
    return 2
