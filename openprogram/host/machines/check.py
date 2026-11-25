"""Reachability checks: one quick round trip to a machine.

Each connection method brings its own ``check``; this module only asks the
machine's method and names none.
"""
from __future__ import annotations


def check_machine(machine) -> tuple[bool, str]:
    """Check ``machine`` through its connection method; a method without a check has nothing to check."""
    if machine.connection is None:
        return False, "it has no connection"
    kind = machine.kind
    if kind is None:
        return False, f"the connection method {machine.method!r} is not available in this installation"
    if kind.check is None:
        return True, "Nothing to check"
    try:
        return kind.check(machine)
    except Exception as exc:
        return False, f"{kind.label}: {exc}"
