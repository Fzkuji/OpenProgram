"""Session permission policy and execution lifecycle.

``state`` owns persisted modes and compare-and-set updates; ``policy`` owns
operation decisions; ``identity`` holds the limits on anyone but the owner;
``refusal`` is the one source of refusal text and results; ``classifier``
evaluates Auto risk; ``approval`` binds decisions to durable waits and tools;
``lifecycle`` applies confirmed changes to subsequent operations and waits.
``authority`` holds authenticated identity, ``attended`` denies questions in
unattended runs, and process isolation
remains in ``sandbox``. Interfaces use this package instead of duplicating policy.
"""
from openprogram.runtime.permissions.state import PermissionUpdateError, permission_state, update_permission
from openprogram.runtime.permissions.lifecycle import current_permission_request, reconcile_permission_waits, wrap_live_permission

__all__ = [
    "PermissionUpdateError", "permission_state", "update_permission",
    "current_permission_request", "reconcile_permission_waits", "wrap_live_permission",
]
