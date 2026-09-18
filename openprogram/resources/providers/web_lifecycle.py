"""Explicit Page closure is distinct from releasing a browser control session."""


def close_page(web_session_id):
    from openprogram.agent import surface_context
    from openprogram.programs.workflow.browser.web_use_runtime import get_registry
    from openprogram.webui.ws_actions import webtab
    registry = get_registry()
    owner = surface_context.web_use_owner_id()
    with registry._lock:
        session = registry._sessions.get(web_session_id)
    if session is None:
        return {'ok': False, 'error': 'web_session_not_found'}
    with session.operation_lock:
        if session.closed or session.closing or session.owner_id != owner:
            return {'ok': False, 'error': 'web_session_owner_mismatch'}
        descriptor = webtab.binding_page_descriptor(session.binding_id)
        if descriptor.get('page_key') != session.page_key:
            return {'ok': False, 'error': 'page_context_stale'}
        result = webtab.request_close_tab(session.binding_id)
        if result.get('ok'):
            registry.execute('close', owner_id=owner, web_session_id=web_session_id)
        return result
