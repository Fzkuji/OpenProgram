"""Admission for owner-level framework operations, separate from resource identity."""


def require_owner() -> None:
    from openprogram.host import sandbox
    from openprogram.runtime.agent.turn_request import get_turn_request
    from openprogram.runtime.permissions import refusal
    from openprogram.host.backend import get_active_backend
    request = get_turn_request()
    if request is not None and getattr(request, 'authority_tier', None) != 'owner':
        raise refusal.framework_owner('this framework operation')
    if get_active_backend().backend_id != 'local' or sandbox.resolve_policy() is not None:
        raise refusal.framework_backend('this framework operation')
