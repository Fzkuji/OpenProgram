"""Session permission policy and execution lifecycle.

``state`` owns persisted modes and compare-and-set updates; ``policy`` owns
operation decisions; ``classifier`` evaluates Auto risk; ``approval`` binds decisions to durable waits and tools;
``lifecycle`` applies confirmed changes to subsequent operations and waits.
Authenticated identity remains in ``agent.authority`` and process isolation
remains in ``sandbox``. Interfaces use this package instead of duplicating policy.
"""
from .state import PermissionUpdateError, permission_state, update_permission
from .lifecycle import current_permission_request, reconcile_permission_waits, wrap_live_permission

__all__ = [
    "PermissionUpdateError", "permission_state", "update_permission",
    "current_permission_request", "reconcile_permission_waits", "wrap_live_permission",
]
