"""Reachability checks: one quick round trip per connection of a machine.

Each kind brings its own ``check``; this module only walks a machine's
connections and names no kind.
"""
from __future__ import annotations


def check_machine(machine) -> tuple[bool, str]:
    """Check every connection of ``machine`` whose kind has a check; the first failure is the answer."""
    from openprogram.host.machines import CONNECTIONS

    messages = []
    for name in CONNECTIONS:
        connection, kind = machine.connection(name), machine.kind(name)
        if connection is None:
            continue
        if kind is None:
            return False, f"the {name} kind {connection.kind!r} is not available in this installation"
        if kind.check is None:
            continue
        try:
            ok, message = kind.check(machine)
        except Exception as exc:
            ok, message = False, f"{kind.label}: {exc}"
        if not ok:
            return False, message
        messages.append(message)
    return True, " · ".join(messages) or "Nothing to check"
