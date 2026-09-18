"""Admission for owner-level framework operations, separate from resource identity."""


def require_owner() -> None:
    from openprogram import sandbox
    from openprogram.agent.turn_request_context import get_turn_request
    from openprogram.backend import get_active_backend
    request = get_turn_request()
    if request is not None and getattr(request, 'authority_tier', None) != 'owner':
        raise PermissionError('framework_requires_owner')
    if get_active_backend().backend_id != 'local' or sandbox.resolve_policy() is not None:
        raise PermissionError('framework_outside_active_backend_or_sandbox')
