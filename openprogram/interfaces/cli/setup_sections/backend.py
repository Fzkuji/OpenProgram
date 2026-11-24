"""Machines section.

A project is a folder on one machine, and its conversations run there:
this computer, or a machine with a shell. The machines themselves are
managed with ``openprogram machines``; this wizard step lists them and can
add one, asking for the settings its shell kind declares.
"""
from __future__ import annotations


def _add_machine(text, choose) -> str | None:
    from openprogram.host.machines import kinds, new_machine_id, save_machine
    shells = [kind for kind in kinds.all_kinds("shell") if not kind.local_only]
    labels = {kind.label: kind for kind in shells}
    picked = choose("Its shell:", list(labels), next(iter(labels), ""))
    if picked is None:
        return None
    kind = labels[picked]
    suggested = new_machine_id(kind.name)
    machine_id = text("Machine id (lowercase, e.g. devbox):", default=suggested) or suggested
    shell: dict = {"kind": kind.name}
    required = set(kind.settings.get("required") or ())
    for key, spec in (kind.settings.get("properties") or {}).items():
        spec = spec if isinstance(spec, dict) else {}
        title = spec.get("title") or key
        hint = "" if key in required else " (blank = default)"
        value = text(f"{title}{hint}:", default=str(spec.get("default") or "")) or ""
        if value:
            shell[key] = value
    return save_machine({"id": machine_id, "shell": shell}).id


def run_backend_section() -> int:
    from openprogram.host.machines import MachineError, list_environments
    from openprogram.interfaces.cli.setup_sections.prompts import _choose_one, _text

    add, done = "+ add a machine", "done"
    print("Machines a project can be on: " + ", ".join(machine.id for machine in list_environments()))
    picked = _choose_one("Add a machine with a shell?", [done, add], done)
    if picked is None:
        print("Cancelled.")
        return 1
    if picked == add:
        try:
            added = _add_machine(_text, _choose_one)
        except MachineError as exc:
            print(f"[error] {exc}")
            return 1
        if added is None:
            print("Cancelled.")
            return 1
        print(f"Added machine: {added}. Open a folder on it to make a project there.")
    print("Manage machines with `openprogram machines`.")
    return 0
