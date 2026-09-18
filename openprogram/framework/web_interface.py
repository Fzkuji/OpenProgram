"""Reuse the existing Page lease for native presentation commands."""
from contextlib import contextmanager


@contextmanager
def authorized_page(arguments, window_id):
    from openprogram.resources.providers.builtin import _require_tool
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser.web_use_runtime import get_registry
    from openprogram.webui.ws_actions import webtab
    _require_tool('web_use')
    registry = get_registry()
    with registry._lock:
        session = registry._sessions.get(arguments.get('web_session_id'))
    if session is None:
        raise ValueError('observe_web_resource_before_interface_operation')
    with session.operation_lock:
        if session.closed or session.closing or session.owner_id != surface_context.web_use_owner_id():
            raise PermissionError('web_session_owner_mismatch')
        descriptor = webtab.binding_page_descriptor(session.binding_id)
        args = arguments.get('arguments', [])
        if (not args or descriptor.get('tab_id') != args[0] or descriptor.get('page_key') != session.page_key
                or (window_id and descriptor.get('window_id') != window_id)):
            raise ValueError('page_context_stale')
        resolved = webtab.request_bound_tab(session.binding_id)
        if not resolved.get('ok'):
            raise ValueError('page_context_stale')
        from openprogram.processes import current_owner
        session_id, _, _ = current_owner()
        yield {**descriptor, 'session_id': session_id}
